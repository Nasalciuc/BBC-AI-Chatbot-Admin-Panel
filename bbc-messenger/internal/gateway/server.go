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
	"sync/atomic"
	"time"

	"github.com/Nasalciuc/BBC-AI-Chatbot-Admin-Panel/bbc-messenger/internal/meta"
)

// MaxBody is the largest webhook or API call the gateway reads: Meta batches a few events, never this many.
const MaxBody = 1 << 20

// InboundPath is the API's route for each event (contracts/messenger.md).
const InboundPath = "/api/channels/messenger/inbound"

// The API's answers about an event it could not answer now (contracts/messenger.md): 503 with HeaderKept, it keeps
// the event and answers it later itself; 503 with HeaderOff, the channel is off and nothing is kept.
const (
	HeaderKept = "X-Messenger-Kept"
	HeaderOff  = "X-Messenger-Off"
)

// Server is the gateway: the webhook, the workers that take each person's events to the API in order, the route
// the API sends the agents' messages through, and /health.
type Server struct {
	Cfg    Config
	Meta   *meta.Client
	API    *http.Client
	Log    *slog.Logger
	Health Health
	Now    func() time.Time
	// sleep is the wait between an event's tries to the API (wait; a test's clock moves with it instead).
	sleep func(ctx context.Context, d time.Duration) bool

	intake  *intake
	queues  []chan meta.Inbound
	sending stripes
	wg      sync.WaitGroup
	mu      sync.RWMutex
	closed  bool
	// pending is the events taken and not yet done with: queued, or with their worker.
	pending atomic.Int64
}

// Start makes the queues and starts the workers; Close takes no more events, Stop drains them.
func (s *Server) Start(ctx context.Context) {
	if s.Now == nil {
		s.Now = time.Now
	}
	if s.sleep == nil {
		s.sleep = wait
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
	if s.Cfg.KeepFor <= 0 {
		s.Cfg.KeepFor = time.Hour
	}
	if s.Cfg.LongRetry <= 0 {
		s.Cfg.LongRetry = time.Minute
	}
	if s.Cfg.SendBudget <= 0 {
		s.Cfg.SendBudget = 60 * time.Second
	}
	if s.Cfg.CallTimeout <= 0 {
		s.Cfg.CallTimeout = 10 * time.Second
	}
	if s.Cfg.HoldFor <= 0 {
		s.Cfg.HoldFor = 15 * time.Minute
	}
	s.intake = newIntake(2*time.Hour, 200_000, s.Cfg.HoldFor)
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
				if n := s.intake.forget(s.Now()); n > 0 {
					s.Health.HoldExpired.Add(int64(n))
					s.Log.Warn("refused events never came again: no longer waited for", "expired", n)
				}
			}
		}
	}()
}

// Close takes no more events: the webhook answers 503 from now on (Meta delivers them to the next process), and the
// workers finish what is queued. Safe to call more than once.
func (s *Server) Close() {
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.closed {
		s.closed = true
		for _, q := range s.queues {
			close(q)
		}
	}
}

// Stop closes the intake and waits for the events taken, until ctx ends; what is not done with then (queued, or
// still with its worker) is counted.
func (s *Server) Stop(ctx context.Context) {
	s.Close()
	done := make(chan struct{})
	go func() { s.wg.Wait(); close(done) }()
	select {
	case <-done:
	case <-ctx.Done():
		left := s.pending.Load()
		s.Health.DroppedAtShutdown.Add(left)
		s.Log.Error("stopped with events not done", "left", left)
	}
}

func (s *Server) isClosed() bool {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.closed
}

// Ready is whether every setting the webhook and the way to the API need is there (never their values): Meta's app
// secret and verify token, the app's id (without it the app's own messages, coming back as echoes, would reach the
// API as the Page inbox's, and Skye would leave each thread after her every reply), at least one Page with its id
// and token, the API's address and the secret shared with it.
func (s *Server) Ready() bool {
	return len(s.Cfg.AppSecret) > 0 && s.Cfg.VerifyToken != "" && s.Cfg.AppID != "" && len(s.Cfg.Pages) > 0 &&
		s.Cfg.APIURL != "" && len(s.Cfg.GatewaySecret) > 0
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
// 200 within 5 seconds; the API's answer comes later, through the Send API). While a setting is missing nothing is
// taken (503: Meta delivers it again once the gateway is set up).
func (s *Server) webhook(w http.ResponseWriter, r *http.Request) {
	if !s.Ready() {
		s.Health.NotReady.Add(1)
		s.Log.Warn("webhook refused: not configured yet (/health says ready)")
		http.Error(w, "not configured", http.StatusServiceUnavailable)
		return
	}
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
	ours := make([]meta.Inbound, 0, len(events))
	for _, in := range events {
		if _, ok := s.Cfg.Pages[in.PageID]; !ok {
			s.Health.UnknownPage.Add(1)
			s.Log.Warn("event for a Page the gateway has no token for", "page", in.PageID)
			continue
		}
		ours = append(ours, in)
	}
	// One step for the whole delivery: what stops it (a full queue, a person waiting) holds the people of the rest
	// before any other delivery can be taken (contracts/messenger.md).
	results, unheld, expired := s.intake.admit(ours, now, s.enqueue)
	if expired > 0 {
		s.Health.HoldExpired.Add(int64(expired))
		s.Log.Warn("refused events of a delivery's other people never came again: no longer waited for",
			"expired", expired)
	}
	stop, last := taken, meta.Inbound{}
	for _, a := range results {
		if a.expired > 0 {
			s.Health.HoldExpired.Add(int64(a.expired))
			s.Log.Warn("a refused event never came again: the person's events are taken again",
				append(logAttrs(a.ev), "expired", a.expired)...)
		}
		switch a.v {
		case duplicate:
			s.Health.Duplicates.Add(1)
		case taken:
			s.Health.Events.Add(1)
		case waiting:
			s.Health.Held.Add(1)
			s.Log.Warn("an earlier event of this person was refused: this one waits for it", logAttrs(a.ev)...)
		}
		stop, last = a.v, a.ev
	}
	if unheld > 0 {
		s.Health.HoldFull.Add(int64(unheld))
		s.Log.Error("too many refused events waited for: these are not, and a later event may overtake them",
			"events", unheld)
	}
	// What stopped the delivery: nothing more of it is taken now, and Meta delivers it again (what was taken comes
	// back as duplicates), each person in the rest waiting for their first event there.
	switch {
	case stop == refused && s.isClosed():
		s.Health.Closing.Add(1)
		http.Error(w, "stopping", http.StatusServiceUnavailable)
	case stop == refused:
		s.Health.QueueFull.Add(1)
		s.Log.Error("queue full: Meta will deliver again", logAttrs(last)...)
		http.Error(w, "busy", http.StatusServiceUnavailable)
	case stop == waiting:
		http.Error(w, "busy", http.StatusServiceUnavailable)
	default:
		w.WriteHeader(http.StatusOK)
	}
}

func (s *Server) enqueue(in meta.Inbound) bool {
	s.mu.RLock()
	defer s.mu.RUnlock()
	if s.closed {
		return false
	}
	s.pending.Add(1)
	select {
	case s.queues[shard(in.PageID, in.PSID, len(s.queues))] <- in:
		return true
	default:
		s.pending.Add(-1)
		return false
	}
}

func (s *Server) work(ctx context.Context, q chan meta.Inbound) {
	defer s.wg.Done()
	for in := range q {
		s.handle(ctx, in)
		s.pending.Add(-1)
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
		if errors.Is(err, errKept) {
			s.Log.Warn("event kept by the API, which answers it later", append(logAttrs(in), "error", err.Error())...)
		} else if ctx.Err() == nil {
			s.Log.Error("event not taken by the API", append(logAttrs(in), "error", err.Error())...)
		}
		return
	}
	s.Health.Forwarded.Add(1)
	if len(answer.Replies) == 0 {
		return
	}
	p, err := s.deliver(ctx, page, in.PSID, answer.Replies, "")
	s.Health.Replies.Add(int64(p.Whole))
	if err != nil {
		kind := s.Health.sendFailed(err)
		s.Log.Error("reply not sent", append(logAttrs(in), "sent", p.Whole, "of", len(answer.Replies), "kind", kind)...)
	}
}

var (
	// errRefused is an API answer that a retry would not change (a 4xx, the channel off).
	errRefused = errors.New("refused by the API")
	// errKept is an event the API keeps after it failed to answer it: it answers it later itself.
	errKept = errors.New("kept by the API")
)

// forward hands one event to the API, signed. The gateway keeps an event until the API has it: while the API can't
// be reached (a network failure, a 5xx it did not answer itself, 429) or is still on the same event (409), it is
// tried again after Retries, then every LongRetry until KeepFor has passed since the first try (forward_failed
// after that). Once the API said it keeps the event (503 with HeaderKept) only Retries are left to it
// (forward_kept after that: the API answers it itself). The channel off (503 with HeaderOff) and any other 4xx are
// final (api_off, forward_refused).
func (s *Server) forward(ctx context.Context, in meta.Inbound, site string) (apiAnswer, error) {
	body, err := json.Marshal(struct {
		meta.Inbound
		Site string `json:"site"`
	}{in, site})
	if err != nil {
		return apiAnswer{}, err
	}
	first := s.Now()
	kept := false
	var last error
	for try := 0; ; try++ {
		if try > 0 {
			d, ok := s.retryWait(try, first, kept)
			if !ok {
				break
			}
			if !s.sleep(ctx, d) {
				return apiAnswer{}, ctx.Err()
			}
			// A long retry starts no call once KeepFor is over (the wait may have taken it to the end).
			if try > len(s.Cfg.Retries) && s.Now().Sub(first) >= s.Cfg.KeepFor {
				break
			}
		}
		answer, status, header, err := s.post(ctx, body)
		switch {
		case err == nil && status/100 == 2:
			return answer, nil
		case err == nil && status == http.StatusServiceUnavailable && header.Get(HeaderOff) != "":
			s.Health.APIOff.Add(1)
			return apiAnswer{}, fmt.Errorf("%w: the channel is off", errRefused)
		case err == nil && status == http.StatusServiceUnavailable && header.Get(HeaderKept) != "":
			kept = true
			last = fmt.Errorf("api answered %d, keeping the event", status)
		case err == nil && status != http.StatusConflict && status != http.StatusTooManyRequests && status < 500:
			// The API refused it for good (a bad call): trying again changes nothing.
			s.Health.ForwardRefused.Add(1)
			return apiAnswer{}, fmt.Errorf("%w: %d", errRefused, status)
		case err == nil:
			last = fmt.Errorf("api answered %d", status)
		default:
			if ctx.Err() != nil {
				return apiAnswer{}, ctx.Err()
			}
			last = err
		}
	}
	if kept {
		s.Health.ForwardKept.Add(1)
		return apiAnswer{}, fmt.Errorf("%w: %w", errKept, last)
	}
	s.Health.ForwardFailed.Add(1)
	return apiAnswer{}, last
}

// retryWait is the wait before try n (from 1): Retries first; then, for an event the API has not taken, LongRetry
// while KeepFor is not over since the first try, the last wait cut to what is left of it. False: tried long enough.
func (s *Server) retryWait(n int, first time.Time, kept bool) (time.Duration, bool) {
	if n <= len(s.Cfg.Retries) {
		return s.Cfg.Retries[n-1], true
	}
	left := s.Cfg.KeepFor - s.Now().Sub(first)
	if kept || left <= 0 {
		return 0, false
	}
	return min(s.Cfg.LongRetry, left), true
}

func (s *Server) post(ctx context.Context, body []byte) (apiAnswer, int, http.Header, error) {
	ctx, cancel := context.WithTimeout(ctx, s.Cfg.APITimeout)
	defer cancel()
	url := strings.TrimRight(s.Cfg.APIURL, "/") + InboundPath
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return apiAnswer{}, 0, nil, err
	}
	unix := s.Now().Unix()
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("User-Agent", "bbc-messenger/1")
	req.Header.Set("X-Gateway-Time", strconv.FormatInt(unix, 10))
	req.Header.Set("X-Gateway-Signature", Sign(s.Cfg.GatewaySecret, unix, body))
	res, err := s.API.Do(req)
	if err != nil {
		return apiAnswer{}, 0, nil, err
	}
	defer func() { _ = res.Body.Close() }()
	if res.StatusCode/100 != 2 {
		// Its status and headers are the whole answer (HeaderKept, HeaderOff, a refusal): a body that breaks off
		// changes none of it.
		if _, err := io.Copy(io.Discard, io.LimitReader(res.Body, MaxBody)); err != nil {
			s.Log.Warn("an API answer's body broke off", "status", res.StatusCode, "error", err.Error())
		}
		return apiAnswer{}, res.StatusCode, res.Header, nil
	}
	data, err := io.ReadAll(io.LimitReader(res.Body, MaxBody))
	if err != nil {
		return apiAnswer{}, 0, nil, fmt.Errorf("an API answer cut short: %w", err)
	}
	var answer apiAnswer
	if err := json.Unmarshal(data, &answer); err != nil {
		return apiAnswer{}, 0, nil, fmt.Errorf("an API answer that is not JSON: %w", err)
	}
	return answer, res.StatusCode, res.Header, nil
}

// progress is what a delivery got out: the replies sent whole, and the messages (a reply's pieces) of all of them,
// and how many of those went out, in order.
type progress struct {
	Whole, Parts, PartsSent int
}

// deliver sends replies to one person in order, each split to Messenger's length. The quick replies go on the last
// message of the batch (Messenger shows them on the newest message only): those of the last reply that has any. A
// failure Meta may get over is tried again, while ctx allows; it answers what went out.
func (s *Server) deliver(ctx context.Context, page Page, psid string, replies []meta.Reply, tag string) (progress, error) {
	var p progress
	type piece struct {
		reply int
		text  string
	}
	var pieces []piece
	var quick []meta.QuickReplyOut
	for i, r := range replies {
		for _, text := range meta.Split(r.Text) {
			pieces = append(pieces, piece{reply: i, text: text})
		}
		if len(r.QuickReplies) > 0 {
			quick = r.QuickReplies
		}
	}
	// Counted before the wait for the person's other sends: one that never gets its turn still says how many.
	p.Parts = len(pieces)
	unlock, err := s.sending.lock(ctx, page.ID, psid)
	if err != nil {
		return p, err
	}
	defer unlock()
	for j, pc := range pieces {
		out := meta.Reply{Text: pc.text}
		if j == len(pieces)-1 {
			out.QuickReplies = quickReplies(quick)
		}
		if err := s.sendOne(ctx, page.Token, psid, out, tag); err != nil {
			return p, err
		}
		p.PartsSent++
		if j == len(pieces)-1 || pieces[j+1].reply != pc.reply {
			p.Whole++
		}
	}
	return p, nil
}

// sendOne sends one message, trying again what Meta may get over. A call starts only while CallTimeout is left of
// ctx's time: one cut off by the deadline could be a message sent and not counted.
func (s *Server) sendOne(ctx context.Context, token, psid string, r meta.Reply, tag string) error {
	var err error
	for try := 0; try <= len(s.Cfg.Retries); try++ {
		if try > 0 && !wait(ctx, s.Cfg.Retries[try-1]) {
			return errors.Join(errOutOfTime, err)
		}
		if !s.hasTime(ctx) {
			return errors.Join(errOutOfTime, err)
		}
		if _, err = s.Meta.Send(ctx, token, psid, r, tag); err == nil || !meta.Retryable(err) {
			return err
		}
	}
	return err
}

// hasTime is whether a whole call to Meta still fits in ctx's time.
func (s *Server) hasTime(ctx context.Context) bool {
	if ctx.Err() != nil {
		return false
	}
	deadline, ok := ctx.Deadline()
	return !ok || time.Until(deadline) >= s.Cfg.CallTimeout
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

// sendResult is what a send got out: the messages sent whole, the Messenger messages their texts made (a long one
// is several) and how many of those went out, and why the rest did not.
type sendResult struct {
	Sent      int         `json:"sent"`
	Parts     int         `json:"parts"`
	PartsSent int         `json:"parts_sent"`
	Failed    *sendFailed `json:"failed,omitempty"`
}

type sendFailed struct {
	Kind    string `json:"kind"`
	Code    int    `json:"code,omitempty"`
	Subcode int    `json:"subcode,omitempty"`
}

// send is the API's route out: signed like the calls the gateway makes to it. It answers 200 with what went out
// and, when something did not, why (the API tells the agent: the 24 hours passed, the person can't be reached…),
// within SendBudget, the wait for the person's other sends included.
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
	ctx, cancel := context.WithTimeout(r.Context(), s.Cfg.SendBudget)
	defer cancel()
	p, err := s.deliver(ctx, page, call.PSID, call.Messages, call.Tag)
	s.Health.AgentSent.Add(int64(p.Whole))
	out := sendResult{Sent: p.Whole, Parts: p.Parts, PartsSent: p.PartsSent}
	if err != nil {
		f := &sendFailed{Kind: s.Health.sendFailed(err)}
		var se *meta.Error
		if errors.As(err, &se) {
			f.Code, f.Subcode = se.Code, se.Subcode
		}
		out.Failed = f
		s.Log.Warn("message from the API not sent", "who", who(call.PageID, call.PSID), "sent", p.Whole,
			"parts_sent", p.PartsSent, "of", p.Parts, "kind", f.Kind)
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
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"status": "ok", "ready": s.Ready(), "pages": len(s.Cfg.Pages), "pending": s.pending.Load(),
		"queue": map[string]int{"depth": depth, "capacity": capacity}, "counters": s.Health.snapshot(),
	})
}
