// Package gateway is bbc-messenger's work (contracts/messenger.md): Meta's webhook in, each person's events in
// order to the API, Skye's and the agents' replies out through the Send API.
package gateway

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"hash/fnv"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/Nasalciuc/BBC-AI-Chatbot-Admin-Panel/bbc-messenger/internal/meta"
)

// Page is one Facebook Page the app answers for: its id, its token (secret), and the site it speaks for (bbc or
// bct), so Skye answers as that brand.
type Page struct {
	ID    string
	Token string
	Site  string
}

// Config is everything the gateway needs from its environment (main.go).
type Config struct {
	AppSecret     []byte // Meta's app secret: the webhook's signature
	VerifyToken   string // the webhook's verify token, set in the app's webhook settings
	AppID         string // the app's own echoes are left out
	Pages         map[string]Page
	APIURL        string // the API's base address
	GatewaySecret []byte // shared with the API: each call between the two is signed with it
	Workers       int    // events of one person go to one worker, in order
	QueueSize     int    // per worker; a full queue answers Meta 503 and Meta tries again
	APITimeout    time.Duration
	Retries       []time.Duration // the waits before each retry of a call to the API or Meta
}

// Health is the counters /health shows. Every failure on the way to a reply is counted: one the gateway does not
// count is one a client finds.
type Health struct {
	Events, Forwarded, Replies, AgentSent                                  atomic.Int64
	BadSignature, Unreadable, TooLarge, UnknownPage, Duplicates, QueueFull atomic.Int64
	OwnEchoes, Skipped, ForwardFailed, ForwardRefused, SendFailed          atomic.Int64
	WindowClosed, Unavailable, ThreadOwned, TokenFailed, RateLimited       atomic.Int64
	ActionFailed, BadCalls, DroppedAtShutdown                              atomic.Int64
}

func (h *Health) snapshot() map[string]int64 {
	return map[string]int64{
		"events": h.Events.Load(), "forwarded": h.Forwarded.Load(), "replies_sent": h.Replies.Load(),
		"agent_sent": h.AgentSent.Load(), "bad_signature": h.BadSignature.Load(), "unreadable": h.Unreadable.Load(),
		"too_large": h.TooLarge.Load(), "unknown_page": h.UnknownPage.Load(), "duplicates": h.Duplicates.Load(),
		"queue_full": h.QueueFull.Load(), "own_echoes": h.OwnEchoes.Load(), "skipped": h.Skipped.Load(),
		"forward_failed": h.ForwardFailed.Load(), "forward_refused": h.ForwardRefused.Load(),
		"send_failed": h.SendFailed.Load(), "window_closed": h.WindowClosed.Load(),
		"unavailable": h.Unavailable.Load(), "thread_owned": h.ThreadOwned.Load(),
		"token_failed": h.TokenFailed.Load(), "rate_limited": h.RateLimited.Load(),
		"action_failed": h.ActionFailed.Load(), "bad_calls": h.BadCalls.Load(),
		"dropped_at_shutdown": h.DroppedAtShutdown.Load(),
	}
}

// sendFailed counts a Send API failure by its kind.
func (h *Health) sendFailed(err error) string {
	h.SendFailed.Add(1)
	kind := meta.KindOf(err)
	switch kind {
	case "window_closed":
		h.WindowClosed.Add(1)
	case "unavailable":
		h.Unavailable.Add(1)
	case "thread_owned":
		h.ThreadOwned.Add(1)
	case "token":
		h.TokenFailed.Add(1)
	case "rate":
		h.RateLimited.Add(1)
	}
	return kind
}

// Sign is the signature of a call between the gateway and the API, both ways: "sha256=" + hex(HMAC-SHA256(the
// shared secret, unix seconds + "." + the raw body)).
func Sign(secret []byte, unix int64, body []byte) string {
	mac := hmac.New(sha256.New, secret)
	mac.Write([]byte(strconv.FormatInt(unix, 10)))
	mac.Write([]byte("."))
	mac.Write(body)
	return "sha256=" + hex.EncodeToString(mac.Sum(nil))
}

// SignedSkew is how far a signed call's time may be from ours: a call recorded and played again later is refused.
const SignedSkew = 5 * time.Minute

// validSigned checks a signed call: its time near now, its signature right.
func validSigned(secret []byte, now time.Time, unixHeader, sigHeader string, body []byte) bool {
	if len(secret) == 0 {
		return false
	}
	unix, err := strconv.ParseInt(strings.TrimSpace(unixHeader), 10, 64)
	if err != nil {
		return false
	}
	if d := now.Sub(time.Unix(unix, 0)); d > SignedSkew || d < -SignedSkew {
		return false
	}
	return hmac.Equal([]byte(Sign(secret, unix, body)), []byte(strings.TrimSpace(sigHeader)))
}

// seen is the events already taken, by key, for as long as Meta may deliver one again. One process's memory: the
// API checks again by Meta's message id, across restarts.
type seen struct {
	mu   sync.Mutex
	at   map[string]time.Time
	ttl  time.Duration
	most int
}

func newSeen(ttl time.Duration, most int) *seen {
	return &seen{at: map[string]time.Time{}, ttl: ttl, most: most}
}

func (s *seen) has(key string, now time.Time) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	t, ok := s.at[key]
	return ok && now.Sub(t) < s.ttl
}

// mark records a key once its event is queued (never before: an event refused for a full queue must be taken
// when Meta delivers it again).
func (s *seen) mark(key string, now time.Time) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if len(s.at) >= s.most {
		s.forgetLocked(now)
	}
	if len(s.at) >= s.most {
		// Still full of live keys: the oldest half goes (a duplicate then reaches the API, which checks again).
		cut := now.Add(-s.ttl / 2)
		for k, t := range s.at {
			if t.Before(cut) {
				delete(s.at, k)
			}
		}
	}
	s.at[key] = now
}

func (s *seen) forget(now time.Time) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.forgetLocked(now)
}

func (s *seen) forgetLocked(now time.Time) {
	for k, t := range s.at {
		if now.Sub(t) >= s.ttl {
			delete(s.at, k)
		}
	}
}

// shard is the worker one person's events go to, so they reach the API in the order Meta sent them.
func shard(pageID, psid string, n int) int {
	h := fnv.New32a()
	h.Write([]byte(pageID))
	h.Write([]byte{0})
	h.Write([]byte(psid))
	return int(h.Sum32() % uint32(n))
}

// who is a person as the logs name them: a short hash of the page and PSID, never the id itself.
func who(pageID, psid string) string {
	sum := sha256.Sum256([]byte(pageID + ":" + psid))
	return hex.EncodeToString(sum[:4])
}

// stripes serialise the sends to one person (a reply and an agent's message never cross), with a fixed number of
// locks whatever the number of people.
type stripes [256]sync.Mutex

func (s *stripes) lock(pageID, psid string) func() {
	m := &s[shard(pageID, psid, len(s))]
	m.Lock()
	return m.Unlock
}

// wait sleeps for d, or less if ctx ends first (then false).
func wait(ctx context.Context, d time.Duration) bool {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-t.C:
		return true
	}
}

// logAttrs is what every log line about one event carries: never a text, a name or a contact.
func logAttrs(in meta.Inbound) []any {
	return []any{"kind", in.Kind, "who", who(in.PageID, in.PSID), "page", in.PageID}
}

// decodeStrict reads JSON that must be one object of the given shape.
func decodeStrict(data []byte, out any) error {
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	return dec.Decode(out)
}
