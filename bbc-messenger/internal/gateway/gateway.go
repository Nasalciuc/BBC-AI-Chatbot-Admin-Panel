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
	"errors"
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
	// An event the API has not taken (it could not be reached) is tried again every LongRetry after Retries, until
	// KeepFor has passed since its first try.
	KeepFor   time.Duration
	LongRetry time.Duration
	// SendBudget is the whole time one /v1/send may take, the wait for the person's send lock included; a call to
	// Meta starts only while CallTimeout (the Meta client's own timeout) is left of it.
	SendBudget  time.Duration
	CallTimeout time.Duration
	// HoldFor is how long a person whose event was refused waits for Meta to deliver it again: their later events are
	// refused meanwhile, so none overtakes it.
	HoldFor time.Duration
}

// Health is the counters /health shows. Every failure on the way to a reply is counted: one the gateway does not
// count is one a client finds.
type Health struct {
	Events, Forwarded, Replies, AgentSent                                  atomic.Int64
	BadSignature, Unreadable, TooLarge, UnknownPage, Duplicates, QueueFull atomic.Int64
	OwnEchoes, Skipped, ForwardFailed, ForwardRefused, SendFailed          atomic.Int64
	WindowClosed, Unavailable, ThreadOwned, TokenFailed, RateLimited       atomic.Int64
	ActionFailed, BadCalls, DroppedAtShutdown                              atomic.Int64
	NotReady, Closing, Held, HoldExpired, ForwardKept, APIOff              atomic.Int64
	Unconfirmed, OutOfTime                                                 atomic.Int64
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
		"dropped_at_shutdown": h.DroppedAtShutdown.Load(), "not_ready": h.NotReady.Load(),
		"closing": h.Closing.Load(), "held": h.Held.Load(), "hold_expired": h.HoldExpired.Load(),
		"forward_kept": h.ForwardKept.Load(), "api_off": h.APIOff.Load(), "unconfirmed": h.Unconfirmed.Load(),
		"out_of_time": h.OutOfTime.Load(),
	}
}

// errOutOfTime is a send stopped because the time it was given ran out before the next call to Meta could start:
// nothing was in flight, so what went out is exactly what was counted.
var errOutOfTime = errors.New("out of time")

// kindOf is the kind of a send's failure (meta.KindOf), "timeout" when its time ran out.
func kindOf(err error) string {
	if errors.Is(err, errOutOfTime) || errors.Is(err, context.DeadlineExceeded) {
		return "timeout"
	}
	return meta.KindOf(err)
}

// sendFailed counts a Send API failure by its kind.
func (h *Health) sendFailed(err error) string {
	h.SendFailed.Add(1)
	kind := kindOf(err)
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
	case "unknown":
		h.Unconfirmed.Add(1)
	case "timeout":
		h.OutOfTime.Add(1)
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

// verdict is what the intake did with one event.
type verdict int

const (
	taken     verdict = iota // queued for its worker, and marked taken
	duplicate                // taken before: left out
	refused                  // its worker's queue was full, or the gateway is stopping
	waiting                  // an earlier event of the same person was refused: this one waits for it
)

// intake decides, under one lock, what a webhook takes. An event already taken (by key, for as long as Meta may
// deliver one again) is a duplicate. A person with a refused event waits for it: their later events are refused
// too until Meta delivers it again, so none of them can overtake it, whenever Meta's deliveries come. The rest go
// to their worker, and only an event its queue took is marked taken (one refused is taken when Meta delivers it
// again). One process's memory: the API checks again by Meta's message id, across restarts.
type intake struct {
	mu   sync.Mutex
	ttl  time.Duration
	most int
	at   map[string]time.Time // key → when it was taken
	// The keys in the order they were taken: the oldest go first, when they expire or when room is needed, so a
	// full map never costs a scan.
	order   []stamp
	head    int
	holdFor time.Duration
	held    map[string]hold // person → their first event still to take
}

type stamp struct {
	key string
	at  time.Time
}

type hold struct {
	key string
	at  time.Time
}

// mostHeld is the most people who wait for a refused event at once; past it a refused batch still answers 503, and
// its people are not held.
const mostHeld = 10_000

func newIntake(ttl time.Duration, most int, holdFor time.Duration) *intake {
	return &intake{at: map[string]time.Time{}, ttl: ttl, most: most, holdFor: holdFor, held: map[string]hold{}}
}

// person is one person on one Page: a hold's key.
func person(in meta.Inbound) string {
	return in.PageID + "\x00" + in.PSID
}

// admit is one event's check and queueing, as one step: a duplicate is left out; a person waiting for an earlier
// event is refused (waiting); otherwise enqueue is tried, and the key is marked only when it took the event.
// expired says a hold that outlived HoldFor was dropped (Meta never delivered its event again).
func (in *intake) admit(ev meta.Inbound, now time.Time, enqueue func() bool) (v verdict, expired bool) {
	key, p := ev.Key(), person(ev)
	in.mu.Lock()
	defer in.mu.Unlock()
	if t, ok := in.at[key]; ok && now.Sub(t) < in.ttl {
		return duplicate, false
	}
	if h, ok := in.held[p]; ok {
		if now.Sub(h.at) >= in.holdFor {
			delete(in.held, p)
			expired = true
		} else if h.key != key {
			return waiting, false
		}
	}
	if !enqueue() {
		return refused, expired
	}
	delete(in.held, p)
	in.markLocked(key, now)
	return taken, expired
}

// holdRest makes each person with an event in rest (a batch's events from the one refused on) wait for their first
// one there, unless they already wait for an earlier one.
func (in *intake) holdRest(rest []meta.Inbound, now time.Time) {
	in.mu.Lock()
	defer in.mu.Unlock()
	for _, ev := range rest {
		key, p := ev.Key(), person(ev)
		if t, ok := in.at[key]; ok && now.Sub(t) < in.ttl {
			continue // taken before: a duplicate when Meta delivers the batch again
		}
		if h, ok := in.held[p]; ok && now.Sub(h.at) < in.holdFor {
			continue
		}
		if len(in.held) >= mostHeld {
			continue
		}
		in.held[p] = hold{key: key, at: now}
	}
}

// forget drops what expired: keys past their time, holds past HoldFor.
func (in *intake) forget(now time.Time) {
	in.mu.Lock()
	defer in.mu.Unlock()
	in.expireLocked(now)
	for p, h := range in.held {
		if now.Sub(h.at) >= in.holdFor {
			delete(in.held, p)
		}
	}
}

func (in *intake) markLocked(key string, now time.Time) {
	in.expireLocked(now)
	// Still full of live keys: the oldest go (a duplicate then reaches the API, which checks again).
	for len(in.at) >= in.most && in.head < len(in.order) {
		in.dropOldestLocked()
	}
	in.at[key] = now
	in.order = append(in.order, stamp{key: key, at: now})
}

func (in *intake) expireLocked(now time.Time) {
	for in.head < len(in.order) && now.Sub(in.order[in.head].at) >= in.ttl {
		in.dropOldestLocked()
	}
	if in.head > 1024 && in.head*2 > len(in.order) {
		in.order = append([]stamp(nil), in.order[in.head:]...)
		in.head = 0
	}
}

func (in *intake) dropOldestLocked() {
	s := in.order[in.head]
	in.order[in.head] = stamp{}
	in.head++
	// A key taken again since (it had expired) has a newer stamp further on: this one is stale.
	if t, ok := in.at[s.key]; ok && t.Equal(s.at) {
		delete(in.at, s.key)
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
// locks whatever the number of people. A lock is waited for no longer than the caller's context.
type stripes struct {
	once sync.Once
	sem  [256]chan struct{}
}

func (s *stripes) lock(ctx context.Context, pageID, psid string) (func(), error) {
	s.once.Do(func() {
		for i := range s.sem {
			s.sem[i] = make(chan struct{}, 1)
		}
	})
	ch := s.sem[shard(pageID, psid, len(s.sem))]
	select {
	case ch <- struct{}{}:
		return func() { <-ch }, nil
	case <-ctx.Done():
		return nil, ctx.Err()
	}
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
