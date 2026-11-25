package live

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"math"
	"net/http"
	"regexp"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/Nasalciuc/BBC-AI-Chatbot-Admin-Panel/bbc-live/internal/token"
)

// Server is bbc-live's HTTP side (contracts/live.md):
//
//	POST /v1/beat     the widget: a visitor is on the site        X-Live-Pass: <b1 pass>
//	GET  /v1/inbox    the widget: wait for an agent's hello        X-Live-Pass: <b1 pass>
//	POST /v1/dismiss  the widget: "not now"                        X-Live-Pass: <b1 pass>
//	GET  /v1/live     the panel: who is on the site now            Authorization: Bearer <l1 token>
//	POST /v1/claim    the panel: hold a visitor to say hello       Authorization: Bearer <l1 token>
//	POST /v1/release  the panel: let them go                       Authorization: Bearer <l1 token>
//	GET  /health
type Server struct {
	Store *Store
	Hub   *Hub
	// LIVE_TOKEN_SECRET: the API signs the visitor's pass and the agent's token with it.
	Secret  []byte
	Origins map[string]bool
	Log     *slog.Logger
	Now     func() time.Time

	// The inbox poll: how long it waits, and how often it looks again on its own.
	PollWait time.Duration
	Recheck  time.Duration
	// The shortest time between two beats of one visitor that both count.
	MinBeatGap time.Duration

	lastBeat sync.Map // vid → time.Time
	Counters Counters
}

// Counters are this process's, in /health.
type Counters struct {
	Beats, BeatsTooSoon, BeatsRefused   atomic.Int64
	Polls, Delivered, PollsRefused      atomic.Int64
	Dismissed                           atomic.Int64
	Lists, Claims, ClaimsRefused        atomic.Int64
	Releases, AgentsRefused, RedisFails atomic.Int64
	// The agent-first test: a read of the list by someone who may say hello, not noted (Alex doesn't wait for them).
	GreetersFailed atomic.Int64
}

// Roles that see the list, and the ones that may hold a visitor to say hello (the sales line).
var (
	listRoles  = map[string]bool{"sales": true, "owner": true, "admin": true, "dev": true, "supervisor": true}
	claimRoles = map[string]bool{"sales": true}
	claimLines = map[string]bool{"sales": true, "all": true}
)

// mayHold says whether the agent may hold a visitor to say hello.
func mayHold(a token.Agent) bool { return claimRoles[a.Role] && claimLines[a.Tunnel] }

func (s *Server) now() time.Time {
	if s.Now != nil {
		return s.Now()
	}
	return time.Now()
}

// Handler is the whole service, CORS included.
func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /v1/beat", s.beat)
	mux.HandleFunc("GET /v1/inbox", s.inbox)
	mux.HandleFunc("POST /v1/dismiss", s.dismiss)
	mux.HandleFunc("GET /v1/live", s.list)
	mux.HandleFunc("POST /v1/claim", s.claim)
	mux.HandleFunc("POST /v1/release", s.release)
	mux.HandleFunc("GET /health", s.health)
	return s.cors(mux)
}

func (s *Server) cors(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if origin := r.Header.Get("Origin"); origin != "" && s.Origins[origin] {
			h := w.Header()
			h.Set("Access-Control-Allow-Origin", origin)
			h.Add("Vary", "Origin")
			h.Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
			h.Set("Access-Control-Allow-Headers", "Content-Type, X-Live-Pass, Authorization")
			h.Set("Access-Control-Max-Age", "600")
		}
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func fail(w http.ResponseWriter, status int, why string) {
	writeJSON(w, status, map[string]any{"ok": false, "error": why})
}

func (s *Server) visitor(w http.ResponseWriter, r *http.Request) (string, bool) {
	vid, err := token.ReadPass(r.Header.Get("X-Live-Pass"), s.Secret, s.now())
	if err == nil && !visitorID.MatchString(vid) {
		err = token.ErrInvalid // the API checks ids before it signs; keys are built from this one
	}
	switch {
	case err == nil:
		return vid, true
	case errors.Is(err, token.ErrNoKey):
		fail(w, http.StatusServiceUnavailable, "not configured")
	default:
		fail(w, http.StatusUnauthorized, "pass "+reason(err))
	}
	return "", false
}

func (s *Server) agent(w http.ResponseWriter, r *http.Request) (token.Agent, bool) {
	raw := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
	a, err := token.ReadAgent(raw, s.Secret, s.now())
	switch {
	case err == nil && listRoles[a.Role]:
		return a, true
	case err == nil:
		s.Counters.AgentsRefused.Add(1)
		fail(w, http.StatusForbidden, "not for this role")
	case errors.Is(err, token.ErrNoKey):
		fail(w, http.StatusServiceUnavailable, "not configured")
	default:
		s.Counters.AgentsRefused.Add(1)
		fail(w, http.StatusUnauthorized, "live token "+reason(err))
	}
	return token.Agent{}, false
}

func reason(err error) string {
	switch {
	case errors.Is(err, token.ErrMissing):
		return "missing"
	case errors.Is(err, token.ErrExpired):
		return "expired"
	}
	return "invalid"
}

func (s *Server) redisFailed(w http.ResponseWriter, where string, err error) {
	s.Counters.RedisFails.Add(1)
	s.Log.Error("redis failed", "where", where, "error", err.Error())
	fail(w, http.StatusServiceUnavailable, "try again")
}

// ── the widget ──────────────────────────────────────────────────────

type beatIn struct {
	Page     string `json:"page"`
	From     string `json:"from"`
	To       string `json:"to"`
	Depart   string `json:"depart"`
	Return   string `json:"return"`
	Cabin    string `json:"cabin"`
	Pax      int    `json:"pax"`
	Source   string `json:"source"`
	Device   string `json:"device"`
	Site     string `json:"site"`
	ChatOpen bool   `json:"chat_open"`
	HasChat  bool   `json:"has_chat"`
	PageType string `json:"page_type"`
	Activity string `json:"activity"`
	// The owner's agent-first test: when Alex's card may come (unix seconds), while he waits for an agent.
	AlexAt float64 `json:"alex_at"`
	// What came of Alex's card on this visit (the widget's agentTest.ts): shown, dismissed ("Not now" or its ✕) or
	// engaged (the visitor answered it); how many seconds ago it showed, by the visitor's clock; the moment that
	// showed it. The panel lists the visitors Alex's card didn't start a chat with.
	AlexCard    string  `json:"alex_card"`
	AlexCardS   float64 `json:"alex_card_s"`
	AlexTrigger string  `json:"alex_trigger"`
}

var (
	airport = regexp.MustCompile(`^[A-Z]{3}$`)
	isoDay  = regexp.MustCompile(`^\d{4}-\d{2}-\d{2}$`)
	pathOK  = regexp.MustCompile(`^/[A-Za-z0-9/_\-.~%]*$`)
	cabins  = map[string]bool{"economy": true, "premium": true, "business": true, "first": true}
	sources = map[string]bool{"kayak": true, "ads": true, "site": true}
	devices = map[string]bool{"phone": true, "computer": true}
	sites   = map[string]bool{"bbc": true, "bct": true}
	// What came of Alex's card, and the moments that show it (the widget's triggers.ts OpeningTrigger).
	alexCards    = map[string]bool{"shown": true, "dismissed": true, "engaged": true}
	alexTriggers = map[string]bool{"kayak": true, "ads": true, "social": true, "quote": true, "reader": true, "exit": true, "note": true}
	// What the site says the page is (the widget's siteSignals.ts PAGE_TYPES).
	pageTypes = map[string]bool{"home": true, "route": true, "destination": true, "origin": true, "airline": true,
		"deals": true, "search": true, "flight": true, "quote": true, "thank_you": true, "blog": true, "about": true,
		"legal": true, "other": true}
	// One step of what the visitor did on the site (the widget's siteSignals.ts activityToken): codes, dates, a
	// step number, kinds of fields and channels from fixed lists. Never what they typed. The same shapes as the
	// API's app/security/metadata.py _ACTIVITY_TOKEN (a route with one known end at least, steps 1 to 20, six
	// kinds at most); the widget's tests check its steps against both.
	activityStep = regexp.MustCompile(`^(?:search(?::(?:[A-Z]{3}-(?:[A-Z]{3}|\?)|\?-[A-Z]{3}))?(?::[0-9]{4}-[0-9]{2}-[0-9]{2})?` +
		`|quote_opened|quote_step(?::(?:[1-9]|1[0-9]|20))?` +
		`|quote_invalid(?::(?:name|email|phone|origin|destination|dates|passengers|cabin|trip_type|message|consent|captcha|other)` +
		`(?:\|(?:name|email|phone|origin|destination|dates|passengers|cabin|trip_type|message|consent|captcha|other)){0,5})?` +
		`|quote_submitting|quote_failed:(?:server|network|captcha|other)|quote_submitted` +
		`|contact(?::(?:phone|whatsapp|email))?)$`)
)

// ActivitySteps is how many of the visitor's last steps the list keeps.
const ActivitySteps = 12

// cleanActivity keeps the steps of the known shapes, the last ActivitySteps of them, in order; anything else
// in the line (an address, a sentence, a step of a shape it doesn't know) is dropped.
func cleanActivity(line string) string {
	if line == "" || len(line) > 600 {
		return ""
	}
	kept := make([]string, 0, ActivitySteps)
	for _, step := range strings.Split(line, ";") {
		if activityStep.MatchString(step) {
			kept = append(kept, step)
		}
	}
	if len(kept) > ActivitySteps {
		kept = kept[len(kept)-ActivitySteps:]
	}
	return strings.Join(kept, ";")
}

func pick(ok map[string]bool, v string) string {
	if ok[v] {
		return v
	}
	return ""
}

func match(re *regexp.Regexp, v string) string {
	if re.MatchString(v) {
		return v
	}
	return ""
}

// cleanBeat keeps what the panel may show: a path without its query (a query can carry an e-mail),
// airport codes, dates, a cabin, a count, the kind of page, the steps the visitor took on the site.
// Nothing the visitor typed passes.
func cleanBeat(vid string, in beatIn) Visit {
	page := in.Page
	if i := strings.IndexAny(page, "?#"); i >= 0 {
		page = page[:i]
	}
	if len(page) > 120 || !pathOK.MatchString(page) {
		page = "/"
	}
	pax := in.Pax
	if pax < 0 || pax > 9 {
		pax = 0
	}
	source := pick(sources, in.Source)
	if source == "" {
		source = "site"
	}
	return Visit{
		ID:   vid,
		Page: page,
		Trip: Trip{From: match(airport, in.From), To: match(airport, in.To), Depart: match(isoDay, in.Depart),
			Return: match(isoDay, in.Return), Cabin: pick(cabins, in.Cabin), Pax: pax},
		Source: source, Device: pick(devices, in.Device), Site: pick(sites, in.Site),
		ChatOpen: in.ChatOpen, HasChat: in.HasChat,
		PageType: pick(pageTypes, in.PageType), Activity: cleanActivity(in.Activity),
	}
}

// AlexAtSlack is how far from now a beat's alex_at may be: the API's longest wait (agent_first_test.MAX_HOLD_S,
// 600 s) and some clock between the visitor and here.
const AlexAtSlack = 900

// cleanAlexAt keeps the time Alex's card may come when it is a whole number of seconds near now; 0 otherwise.
func cleanAlexAt(at float64, now time.Time) int64 {
	if at <= 0 || at != math.Trunc(at) {
		return 0
	}
	n := now.Unix()
	if at < float64(n-AlexAtSlack) || at > float64(n+AlexAtSlack) {
		return 0
	}
	return int64(at)
}

// AlexCardOldest is how long ago Alex's card may have shown for a beat to say so: a visit of a day.
const AlexCardOldest = 86400

// cleanAlexCard keeps what came of Alex's card when it is a known state, shown a whole number of seconds ago within
// a day: the time it showed is set by bbc-live's clock (the visitor's may be off; only the time since counts), and
// the moment only from the known ones. Anything else leaves the visit without it.
func cleanAlexCard(state string, secondsAgo float64, trigger string, now time.Time) (string, int64, string) {
	if !alexCards[state] || secondsAgo < 0 || secondsAgo > AlexCardOldest || secondsAgo != math.Trunc(secondsAgo) {
		return "", 0, ""
	}
	return state, now.Unix() - int64(secondsAgo), pick(alexTriggers, trigger)
}

// takeBeatTurn says whether this visitor's beat at now counts, and if so records it, in one step: of two beats
// at once, only one passes the gap.
func (s *Server) takeBeatTurn(vid string, now time.Time) bool {
	for {
		last, seen := s.lastBeat.Load(vid)
		if !seen {
			if _, taken := s.lastBeat.LoadOrStore(vid, now); !taken {
				return true
			}
			continue // another beat stored first: judge against it
		}
		if now.Sub(last.(time.Time)) < s.MinBeatGap {
			return false
		}
		if s.lastBeat.CompareAndSwap(vid, last, now) {
			return true
		}
	}
}

func (s *Server) beat(w http.ResponseWriter, r *http.Request) {
	vid, ok := s.visitor(w, r)
	if !ok {
		s.Counters.BeatsRefused.Add(1)
		return
	}
	var in beatIn
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096)).Decode(&in); err != nil {
		s.Counters.BeatsRefused.Add(1)
		fail(w, http.StatusBadRequest, "bad beat")
		return
	}
	if !s.takeBeatTurn(vid, s.now()) {
		s.Counters.BeatsTooSoon.Add(1)
		writeJSON(w, http.StatusAccepted, map[string]any{"ok": true, "counted": false})
		return
	}
	visit := cleanBeat(vid, in)
	visit.AlexAt = cleanAlexAt(in.AlexAt, s.now())
	visit.AlexCard, visit.AlexCardAt, visit.AlexTrigger = cleanAlexCard(in.AlexCard, in.AlexCardS, in.AlexTrigger, s.now())
	if err := s.Store.Beat(r.Context(), visit); err != nil {
		s.redisFailed(w, "beat", err)
		return
	}
	s.Counters.Beats.Add(1)
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "counted": true})
}

// ForgetOldBeats drops the beat gaps of visitors gone quiet (run it now and then).
func (s *Server) ForgetOldBeats() {
	cut := s.now().Add(-2 * VisitTTL * time.Second)
	s.lastBeat.Range(func(k, v any) bool {
		if v.(time.Time).Before(cut) {
			s.lastBeat.Delete(k)
		}
		return true
	})
}

func (s *Server) deliver(w http.ResponseWriter, msgs []string) bool {
	out := make([]json.RawMessage, 0, len(msgs))
	for _, m := range msgs {
		if json.Valid([]byte(m)) {
			out = append(out, json.RawMessage(m))
		}
	}
	if len(out) == 0 {
		return false
	}
	s.Counters.Delivered.Add(int64(len(out)))
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "messages": out})
	return true
}

func (s *Server) inbox(w http.ResponseWriter, r *http.Request) {
	// "GET /v1/inbox" also matches HEAD, whose answer has no body: a HEAD would take the hello and lose it.
	if r.Method == http.MethodHead {
		w.Header().Set("Allow", "GET")
		fail(w, http.StatusMethodNotAllowed, "GET only")
		return
	}
	vid, ok := s.visitor(w, r)
	if !ok {
		s.Counters.PollsRefused.Add(1)
		return
	}
	s.Counters.Polls.Add(1)
	ctx := r.Context()
	msgs, err := s.Store.TakeInbox(ctx, vid)
	if err != nil {
		s.redisFailed(w, "inbox", err)
		return
	}
	if s.deliver(w, msgs) {
		return
	}
	wake, done, ok := s.Hub.Wait(vid)
	defer done()
	if !ok {
		s.Counters.PollsRefused.Add(1)
		fail(w, http.StatusTooManyRequests, "too many waits")
		return
	}
	deadline := time.NewTimer(s.PollWait)
	defer deadline.Stop()
	recheck := time.NewTicker(s.Recheck)
	defer recheck.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-deadline.C:
			w.WriteHeader(http.StatusNoContent)
			return
		case <-wake:
		case <-recheck.C:
			if n, err := s.Store.InboxLen(ctx, vid); err != nil || n == 0 {
				continue
			}
		}
		msgs, err := s.Store.TakeInbox(ctx, vid)
		if err != nil {
			if ctx.Err() != nil {
				return
			}
			s.redisFailed(w, "inbox", err)
			return
		}
		if s.deliver(w, msgs) {
			return
		}
	}
}

func (s *Server) dismiss(w http.ResponseWriter, r *http.Request) {
	vid, ok := s.visitor(w, r)
	if !ok {
		return
	}
	if _, err := s.Store.Dismiss(r.Context(), vid); err != nil {
		s.redisFailed(w, "dismiss", err)
		return
	}
	s.Counters.Dismissed.Add(1)
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}

// ── the panel ───────────────────────────────────────────────────────

func (s *Server) list(w http.ResponseWriter, r *http.Request) {
	a, ok := s.agent(w, r)
	if !ok {
		return
	}
	visitors, err := s.Store.Live(r.Context())
	if err != nil {
		s.redisFailed(w, "list", err)
		return
	}
	s.Counters.Lists.Add(1)
	if mayHold(a) {
		// The owner's agent-first test: someone who may say hello is looking at the list, so Alex's card may wait
		// for them (the API reads live:greeters). Not noted, Alex doesn't wait; the list answers all the same.
		if err := s.Store.Greeter(r.Context(), a.User); err != nil {
			s.Counters.GreetersFailed.Add(1)
			s.Log.Warn("greeter not noted", "where", "list", "error", err.Error())
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "data": visitors, "count": len(visitors),
		"max_claims": MaxClaims, "claim_seconds": ClaimTTL})
}

type visitorIn struct {
	VisitorID string `json:"visitor_id"`
}

var visitorID = regexp.MustCompile(`^[A-Za-z0-9_-]{8,64}$`)

func (s *Server) readVisitorID(w http.ResponseWriter, r *http.Request) (string, bool) {
	var in visitorIn
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1024)).Decode(&in); err != nil || !visitorID.MatchString(in.VisitorID) {
		fail(w, http.StatusBadRequest, "visitor_id")
		return "", false
	}
	return in.VisitorID, true
}

var claimStatus = map[ClaimResult]int{
	Claimed: http.StatusOK, Mine: http.StatusOK, Gone: http.StatusGone, Taken: http.StatusConflict,
	Full: http.StatusConflict, Declined: http.StatusConflict, InAChat: http.StatusConflict,
}

func (s *Server) claim(w http.ResponseWriter, r *http.Request) {
	a, ok := s.agent(w, r)
	if !ok {
		return
	}
	if !mayHold(a) {
		s.Counters.ClaimsRefused.Add(1)
		fail(w, http.StatusForbidden, "saying hello first is for the sales line")
		return
	}
	vid, ok := s.readVisitorID(w, r)
	if !ok {
		return
	}
	res, err := s.Store.Claim(r.Context(), a.User, vid)
	if err != nil {
		s.redisFailed(w, "claim", err)
		return
	}
	if res == Claimed || res == Mine {
		s.Counters.Claims.Add(1)
	} else {
		s.Counters.ClaimsRefused.Add(1)
	}
	writeJSON(w, claimStatus[res], map[string]any{"ok": res == Claimed || res == Mine, "state": string(res),
		"claim_seconds": ClaimTTL})
}

func (s *Server) release(w http.ResponseWriter, r *http.Request) {
	a, ok := s.agent(w, r)
	if !ok {
		return
	}
	vid, ok := s.readVisitorID(w, r)
	if !ok {
		return
	}
	released, err := s.Store.Release(r.Context(), a.User, vid)
	if err != nil {
		s.redisFailed(w, "release", err)
		return
	}
	s.Counters.Releases.Add(1)
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "released": released})
}

func (s *Server) health(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), time.Second)
	defer cancel()
	redisOK := s.Store.Ping(ctx) == nil
	c := &s.Counters
	status := http.StatusOK
	if !redisOK {
		status = http.StatusServiceUnavailable
	}
	writeJSON(w, status, map[string]any{
		"ok": redisOK, "redis": redisOK,
		"configured": map[string]bool{"live_token": len(s.Secret) > 0},
		"waiting":    s.Hub.Waiting(),
		"counters": map[string]int64{
			"beats": c.Beats.Load(), "beats_too_soon": c.BeatsTooSoon.Load(), "beats_refused": c.BeatsRefused.Load(),
			"polls": c.Polls.Load(), "delivered": c.Delivered.Load(), "polls_refused": c.PollsRefused.Load(),
			"dismissed": c.Dismissed.Load(), "lists": c.Lists.Load(), "claims": c.Claims.Load(),
			"claims_refused": c.ClaimsRefused.Load(), "releases": c.Releases.Load(),
			"agents_refused": c.AgentsRefused.Load(), "redis_failures": c.RedisFails.Load(),
			"subscription_restarts": s.Hub.Reconnects.Load(), "wakes": s.Hub.Wakes.Load(),
			"greeters_failed": c.GreetersFailed.Load(),
		},
	})
}
