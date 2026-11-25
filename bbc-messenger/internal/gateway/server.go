package gateway

import (
	"bytes"
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/Nasalciuc/BBC-AI-Chatbot-Admin-Panel/bbc-messenger/internal/meta"
)

// MaxBody is the largest webhook or API call the gateway reads: Meta batches a few events, never this many.
const MaxBody = 1 << 20

// InboundPath is the API's route for each event (contracts/messenger.md).
const InboundPath = "/api/channels/messenger/inbound"

// Server is the gateway: the webhook, the workers that take each person's events to the API in order, the route
// the API sends the agents' messages through, and /health.
type Server struct {
	Cfg    Config
	Meta   *meta.Client
	API    *http.Client
	Log    *slog.Logger
	Health Health
	Now    func() time.Time

	seen    *seen
	queues  []chan meta.Inbound
	sending stripes
	wg      sync.WaitGroup
	mu      sync.RWMutex
	closed  bool
}

// Start makes the queues and starts the workers; Stop drains them.
func (s *Server) Start(ctx context.Context) {
	if s.Now == nil {
		s.Now = time.Now
	}
	if s.Cfg.Workers <= 0 {
		s.Cfg.Workers = 16
	}
	if s.Cfg.QueueSize <= 0 {
		s.Cfg.QueueSize = 256
	}
	if s.Cfg.APITimeout <= 0 {
		s.Cfg.APITimeout = 75 * time.Second
	}
	s.seen = newSeen(2*time.Hour, 200_000)
	s.queues = make([]chan meta.Inbound, s.Cfg.Workers)
	for i := range s.queues {
		s.queues[i] = make(chan meta.Inbound, s.Cfg.QueueSize)
		s.wg.Add(1)
		go s.work(ctx, s.queues[i])
	}
	go func() {
		t := time.NewTicker(time.Minute)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				s.seen.forget(s.Now())
			}
		}
	}()
}

// Stop takes no more events and waits for the queued ones, until ctx ends; what is left then is counted.
func (s *Server) Stop(ctx context.Context) {
	s.mu.Lock()
	if !s.closed {
		s.closed = true
		for _, q := range s.queues {
			close(q)
		}
	}
	s.mu.Unlock()
	done := make(chan struct{})
	go func() { s.wg.Wait(); close(done) }()
	select {
	case <-done:
	case <-ctx.Done():
		left := 0
		for _, q := range s.queues {
			left += len(q)
		}
		s.Health.DroppedAtShutdown.Add(int64(left))
		s.Log.Error("stopped with events still queued", "left", left)
	}
}

// Handler is the gateway's routes.
func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /webhook", s.verify)
	mux.HandleFunc("POST /webhook", s.webhook)
	mux.HandleFunc("POST /v1/send", s.send)
	mux.HandleFunc("GET /health", s.health)
	return mux
}

// verify answers Meta's check when the webhook is saved: the verify token we gave, and Meta's challenge back.
func (s *Server) verify(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	token := []byte(q.Get("hub.verify_token"))
	ok := q.Get("hub.mode") == "subscribe" && s.Cfg.VerifyToken != "" &&
		subtle.ConstantTimeCompare(token, []byte(s.Cfg.VerifyToken)) == 1
	challenge := q.Get("hub.challenge")
	if !ok || challenge == "" || len(challenge) > 256 || strings.ContainsAny(challenge, "<>\"'&\r\n") {
		s.Log.Warn("webhook verification refused")
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	_, _ = io.WriteString(w, challenge)
}

// webhook takes Meta's events: signed, read, each person's in order to their worker, answered at once (Meta wants
// 200 within 5 seconds; the API's answer comes later, through the Send API).
func (s *Server) webhook(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, MaxBody))
	if err != nil {
		s.Health.TooLarge.Add(1)
		http.Error(w, "too large", http.StatusRequestEntityTooLarge)
		return
	}
	if !meta.ValidSignature(body, r.Header.Get("X-Hub-Signature-256"), s.Cfg.AppSecret) {
		s.Health.BadSignature.Add(1)
		s.Log.Warn("webhook with a bad signature refused")
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}
	var p meta.Payload
	if err := json.Unmarshal(body, &p); err != nil || p.Object != "page" {
		// Signed but not a Page's events: answered 200, or Meta would send it again and again.
		s.Health.Unreadable.Add(1)
		s.Log.Warn("signed webhook that is not a Page's events", "object", p.Object)
		w.WriteHeader(http.StatusOK)
		return
	}
	events, skipped := meta.Events(p, s.Cfg.AppID)
	s.Health.OwnEchoes.Add(int64(skipped.OwnEchoes))
	s.Health.Skipped.Add(int64(skipped.Other))
	now := s.Now()
	for _, in := range events {
		if _, ok := s.Cfg.Pages[in.PageID]; !ok {
			s.Health.UnknownPage.Add(1)
			s.Log.Warn("event for a Page the gateway has no token for", "page", in.PageID)
			continue
		}
		key := in.Key()
		if s.seen.has(key, now) {
			s.Health.Duplicates.Add(1)
			continue
		}
		if !s.enqueue(in) {
			// Meta sends the whole batch again: what was queued before this one is a duplicate then, the rest
			// is taken.
			s.Health.QueueFull.Add(1)
			s.Log.Error("queue full: Meta will deliver again", logAttrs(in)...)
			http.Error(w, "busy", http.StatusServiceUnavailable)
			return
		}
		s.seen.mark(key, now)
		s.Health.Events.Add(1)
	}
	w.WriteHeader(http.StatusOK)
}

func (s *Server) enqueue(in meta.Inbound) bool {
	s.mu.RLock()
	defer s.mu.RUnlock()
	if s.closed {
		return false
	}
	select {
	case s.queues[shard(in.PageID, in.PSID, len(s.queues))] <- in:
		return true
	default:
		return false
	}
}

func (s *Server) work(ctx context.Context, q chan meta.Inbound) {
	defer s.wg.Done()
	for in := range q {
		s.handle(ctx, in)
	}
}

// apiAnswer is the API's answer to one event: the replies to send, in order (contracts/messenger.md).
type apiAnswer struct {
	Replies []meta.Reply `json:"replies"`
}

// handle takes one event to the API and sends what it answers. The person sees the message read and Skye typing
// while the API thinks.
func (s *Server) handle(ctx context.Context, in meta.Inbound) {
	page := s.Cfg.Pages[in.PageID]
	if in.Answered() {
		for _, action := range []string{"mark_seen", "typing_on"} {
			if err := s.Meta.Action(ctx, page.Token, in.PSID, action); err != nil {
				s.Health.ActionFailed.Add(1)
				s.Log.Warn("sender action failed", append(logAttrs(in), "action", action, "kind", meta.KindOf(err))...)
				break
			}
		}
	}
	answer, err := s.forward(ctx, in, page.Site)
	if err != nil {
		s.Log.Error("event not taken by the API", append(logAttrs(in), "error", err.Error())...)
		return
	}
	s.Health.Forwarded.Add(1)
	if len(answer.Replies) == 0 {
		return
	}
	n, err := s.deliver(ctx, page, in.PSID, answer.Replies, "")
	s.Health.Replies.Add(int64(n))
	if err != nil {
		kind := s.Health.sendFailed(err)
		s.Log.Error("reply not sent", append(logAttrs(in), "sent", n, "of", len(answer.Replies), "kind", kind)...)
	}
}

// errRefused is an API answer that a retry would not change (a 4xx).
var errRefused = errors.New("refused by the API")

// forward hands one event to the API, signed, trying again while the API is down or still on the same event.
func (s *Server) forward(ctx context.Context, in meta.Inbound, site string) (apiAnswer, error) {
	body, err := json.Marshal(struct {
		meta.Inbound
		Site string `json:"site"`
	}{in, site})
	if err != nil {
		return apiAnswer{}, err
	}
	var last error
	for try := 0; try <= len(s.Cfg.Retries); try++ {
		if try > 0 && !wait(ctx, s.Cfg.Retries[try-1]) {
			break
		}
		answer, status, err := s.post(ctx, body)
		switch {
		case err == nil && status/100 == 2:
			return answer, nil
		case err == nil && status != http.StatusConflict && status != http.StatusTooManyRequests && status < 500:
			// The API refused it for good (off, a bad call): trying again changes nothing.
			s.Health.ForwardRefused.Add(1)
			return apiAnswer{}, fmt.Errorf("%w: %d", errRefused, status)
		case err == nil:
			last = fmt.Errorf("api answered %d", status)
		default:
			last = err
		}
	}
	s.Health.ForwardFailed.Add(1)
	return apiAnswer{}, last
}

func (s *Server) post(ctx context.Context, body []byte) (apiAnswer, int, error) {
	ctx, cancel := context.WithTimeout(ctx, s.Cfg.APITimeout)
	defer cancel()
	url := strings.TrimRight(s.Cfg.APIURL, "/") + InboundPath
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return apiAnswer{}, 0, err
	}
	unix := s.Now().Unix()
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("User-Agent", "bbc-messenger/1")
	req.Header.Set("X-Gateway-Time", strconv.FormatInt(unix, 10))
	req.Header.Set("X-Gateway-Signature", Sign(s.Cfg.GatewaySecret, unix, body))
	res, err := s.API.Do(req)
	if err != nil {
		return apiAnswer{}, 0, err
	}
	defer func() { _ = res.Body.Close() }()
	data, err := io.ReadAll(io.LimitReader(res.Body, MaxBody))
	if err != nil {
		return apiAnswer{}, 0, err
	}
	var answer apiAnswer
	if res.StatusCode/100 == 2 {
		if err := json.Unmarshal(data, &answer); err != nil {
			return apiAnswer{}, 0, fmt.Errorf("an API answer that is not JSON: %w", err)
		}
	}
	return answer, res.StatusCode, nil
}

// deliver sends replies to one person in order, each split to Messenger's length, the quick replies on the last
// piece of the last one. A failure Meta may get over is tried again; it answers how many replies went out whole.
func (s *Server) deliver(ctx context.Context, page Page, psid string, replies []meta.Reply, tag string) (int, error) {
	unlock := s.sending.lock(page.ID, psid)
	defer unlock()
	sent := 0
	for _, r := range replies {
		pieces := meta.Split(r.Text)
		for i, text := range pieces {
			out := meta.Reply{Text: text}
			if i == len(pieces)-1 {
				out.QuickReplies = quickReplies(r.QuickReplies)
			}
			if err := s.sendOne(ctx, page.Token, psid, out, tag); err != nil {
				return sent, err
			}
		}
		if len(pieces) > 0 {
			sent++
		}
	}
	return sent, nil
}

func (s *Server) sendOne(ctx context.Context, token, psid string, r meta.Reply, tag string) error {
	var err error
	for try := 0; try <= len(s.Cfg.Retries); try++ {
		if try > 0 && !wait(ctx, s.Cfg.Retries[try-1]) {
			return err
		}
		if _, err = s.Meta.Send(ctx, token, psid, r, tag); err == nil || !meta.Retryable(err) {
			return err
		}
	}
	return err
}

// quickReplies keeps Meta's limits: 13 at most, text ones with a title (20 characters) and a payload.
func quickReplies(in []meta.QuickReplyOut) []meta.QuickReplyOut {
	var out []meta.QuickReplyOut
	for _, q := range in {
		if len(out) == meta.MaxQuickReplies {
			break
		}
		switch q.ContentType {
		case "user_phone_number", "user_email":
			out = append(out, meta.QuickReplyOut{ContentType: q.ContentType})
		case "text", "":
			if strings.TrimSpace(q.Title) == "" || q.Payload == "" {
				continue
			}
			out = append(out, meta.QuickReplyOut{ContentType: "text", Title: meta.Clip(q.Title), Payload: q.Payload})
		}
	}
	return out
}

// sendCall is the API's call to send an agent's message (or anything the API says outside a reply to an event).
type sendCall struct {
	PageID   string       `json:"page_id"`
	PSID     string       `json:"psid"`
	Messages []meta.Reply `json:"messages"`
	Tag      string       `json:"tag,omitempty"`
}

type sendResult struct {
	Sent   int         `json:"sent"`
	Failed *sendFailed `json:"failed,omitempty"`
}

type sendFailed struct {
	Kind    string `json:"kind"`
	Code    int    `json:"code,omitempty"`
	Subcode int    `json:"subcode,omitempty"`
}

// send is the API's route out: signed like the calls the gateway makes to it. It answers 200 with what went out
// and, when something did not, why (the API tells the agent: the 24 hours passed, the person can't be reached…).
func (s *Server) send(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, MaxBody))
	if err != nil {
		s.Health.BadCalls.Add(1)
		http.Error(w, "too large", http.StatusRequestEntityTooLarge)
		return
	}
	if !validSigned(s.Cfg.GatewaySecret, s.Now(), r.Header.Get("X-Gateway-Time"), r.Header.Get("X-Gateway-Signature"), body) {
		s.Health.BadCalls.Add(1)
		s.Log.Warn("send call with a bad signature refused")
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}
	var call sendCall
	if err := decodeStrict(body, &call); err != nil || call.PSID == "" || len(call.Messages) == 0 {
		s.Health.BadCalls.Add(1)
		http.Error(w, "bad call", http.StatusBadRequest)
		return
	}
	if call.Tag != "" && call.Tag != "HUMAN_AGENT" {
		s.Health.BadCalls.Add(1)
		http.Error(w, "bad tag", http.StatusBadRequest)
		return
	}
	page, ok := s.Cfg.Pages[call.PageID]
	if !ok {
		s.Health.UnknownPage.Add(1)
		http.Error(w, "unknown page", http.StatusNotFound)
		return
	}
	n, err := s.deliver(r.Context(), page, call.PSID, call.Messages, call.Tag)
	s.Health.AgentSent.Add(int64(n))
	out := sendResult{Sent: n}
	if err != nil {
		f := &sendFailed{Kind: s.Health.sendFailed(err)}
		var se *meta.Error
		if errors.As(err, &se) {
			f.Code, f.Subcode = se.Code, se.Subcode
		}
		out.Failed = f
		s.Log.Warn("message from the API not sent", "who", who(call.PageID, call.PSID), "sent", n, "kind", f.Kind)
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(out)
}

// health is the counters, the queues and whether the gateway has what it needs: never a secret.
func (s *Server) health(w http.ResponseWriter, _ *http.Request) {
	depth, capacity := 0, 0
	for _, q := range s.queues {
		depth += len(q)
		capacity += cap(q)
	}
	ready := len(s.Cfg.AppSecret) > 0 && s.Cfg.VerifyToken != "" && len(s.Cfg.Pages) > 0 && s.Cfg.APIURL != "" &&
		len(s.Cfg.GatewaySecret) > 0
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"status": "ok", "ready": ready, "pages": len(s.Cfg.Pages),
		"queue": map[string]int{"depth": depth, "capacity": capacity}, "counters": s.Health.snapshot(),
	})
}
