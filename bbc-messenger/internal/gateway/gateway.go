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
	"slices"
	"sort"
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
	NotReady, Closing, Held, HoldExpired, HoldFull, ForwardKept, APIOff    atomic.Int64
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
		"hold_full": h.HoldFull.Load(), "forward_kept": h.ForwardKept.Load(), "api_off": h.APIOff.Load(),
		"unconfirmed": h.Unconfirmed.Load(), "out_of_time": h.OutOfTime.Load(),
	}
}

// errOutOfTime is a send stopped because the time it was given ran out before the next call to Meta could start:
// nothing was in flight, so what went out is exactly what was counted.
var errOutOfTime = errors.New("out of time")

// kindOf is the kind of a send's failure (meta.KindOf), "timeout" when its time ran out before a call to Meta went
// out. A call Meta had whole stays "unknown" whatever ended the wait for its answer (Meta client's own timeout
// included, which is a deadline too): it may be with the person.
func kindOf(err error) string {
	var ue *meta.Unconfirmed
	if errors.As(err, &ue) {
		return "unknown"
	}
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
// deliver one again) is a duplicate. A person with refused events waits for them, each in its place: the earliest
// (Meta's time) must be taken first, then the next, and any other event of theirs is refused until they all are,
// so none overtakes an earlier one, whichever Meta delivers first. The rest go to their worker, and only an event
// its queue took is marked taken (one refused is taken when Meta delivers it again). One process's memory: the API
// checks again by Meta's message id, across restarts.
//
// The order is kept within bounds, past which Messenger staying up wins over it: a refused event is waited for
// HoldFor at most (hold_expired; Meta unsubscribes an app whose deliveries fail for an hour, so no one's are
// refused near that long), at most mostHeld people wait at once and each for mostWaiting events (memory:
// hold_full). Past them, a later event may reach the API before a refused one, which the API still answers once.
type intake struct {
	mu   sync.Mutex
	ttl  time.Duration
	most int
	at   map[string]time.Time // key → when it was taken
	// The keys in the order they were taken: the oldest go first, when they expire or when room is needed, so a
	// full map never costs a scan.
	order    []stamp
	head     int
	holdFor  time.Duration
	held     map[string][]refusal // person → their refused events still to take, earliest first
	mostHeld int
}

type stamp struct {
	key string
	at  time.Time
}

// refusal is an event refused and still to take: its key, Meta's time (its place among the person's), and when it
// was first refused (it is waited for HoldFor from then).
type refusal struct {
	key string
	ts  int64
	at  time.Time
}

// mostHeld is the most people who wait for refused events at once, and mostWaiting the most events one of them
// waits for; past either a refused batch still answers 503, and what did not fit is not waited for (counted).
const (
	mostHeld    = 10_000
	mostWaiting = 100
)

func newIntake(ttl time.Duration, most int, holdFor time.Duration) *intake {
	return &intake{at: map[string]time.Time{}, ttl: ttl, most: most, holdFor: holdFor,
		held: map[string][]refusal{}, mostHeld: mostHeld}
}

// person is one person on one Page: a hold's key.
func person(in meta.Inbound) string {
	return in.PageID + "\x00" + in.PSID
}

// admission is one event of a delivery as the intake took it: its verdict, and how many of its person's refused
// events that outlived HoldFor were dropped (Meta never delivered them again).
type admission struct {
	ev      meta.Inbound
	v       verdict
	expired int
}

// admit is one delivery's check and queueing, as one step under the intake's lock. Each event in order is a
// duplicate (left out), taken (enqueue took it, and only then is its key marked), or it stops the delivery
// (refused: its queue was full or the gateway is closing; waiting: its person waits for an earlier event). In that
// same step each event of the rest, from the one that stopped it on, takes its place among its person's refused
// ones: no delivery made meanwhile, whenever it comes, can overtake it. unheld is how many events of the rest could
// not be waited for (mostHeld or mostWaiting reached).
func (in *intake) admit(events []meta.Inbound, now time.Time, enqueue func(meta.Inbound) bool) (out []admission, unheld int) {
	in.mu.Lock()
	defer in.mu.Unlock()
	out = make([]admission, 0, len(events))
	for i, ev := range events {
		v, expired := in.admitLocked(ev, now, enqueue)
		out = append(out, admission{ev: ev, v: v, expired: expired})
		if v == waiting || v == refused {
			return out, in.holdLocked(events[i:], now)
		}
	}
	return out, 0
}

func (in *intake) admitLocked(ev meta.Inbound, now time.Time, enqueue func(meta.Inbound) bool) (v verdict, expired int) {
	key, p := ev.Key(), person(ev)
	if t, ok := in.at[key]; ok && now.Sub(t) < in.ttl {
		return duplicate, 0
	}
	q, expired := in.liveLocked(p, now)
	if len(q) > 0 && q[0].key != key {
		return waiting, expired // an earlier refused event of theirs is still to take
	}
	if !enqueue(ev) {
		return refused, expired
	}
	if len(q) > 0 {
		in.setLocked(p, q[1:]) // it was their earliest: the next one is now
	}
	in.markLocked(key, now)
	return taken, expired
}

// liveLocked is a person's refused events still waited for, those past HoldFor dropped (how many).
func (in *intake) liveLocked(p string, now time.Time) ([]refusal, int) {
	q := in.held[p]
	live := q[:0]
	for _, r := range q {
		if now.Sub(r.at) < in.holdFor {
			live = append(live, r)
		}
	}
	dropped := len(q) - len(live)
	if dropped > 0 {
		in.setLocked(p, live)
	}
	return live, dropped
}

func (in *intake) setLocked(p string, q []refusal) {
	if len(q) == 0 {
		delete(in.held, p)
		return
	}
	in.held[p] = q
}

// holdLocked gives each event in rest its place among its person's refused events (by Meta's time; the same time,
// after those already there), unless it was taken before or is there already. It answers how many it could not.
func (in *intake) holdLocked(rest []meta.Inbound, now time.Time) (unheld int) {
	for _, ev := range rest {
		key, p := ev.Key(), person(ev)
		if t, ok := in.at[key]; ok && now.Sub(t) < in.ttl {
			continue // taken before: a duplicate when Meta delivers the batch again
		}
		q, ok := in.held[p]
		if slices.ContainsFunc(q, func(r refusal) bool { return r.key == key }) {
			continue
		}
		if (!ok && len(in.held) >= in.mostHeld) || len(q) >= mostWaiting {
			unheld++
			continue
		}
		i := sort.Search(len(q), func(i int) bool { return q[i].ts > ev.Timestamp })
		in.held[p] = slices.Insert(q, i, refusal{key: key, ts: ev.Timestamp, at: now})
	}
	return unheld
}

// forget drops what expired: keys past their time, refused events past HoldFor (how many: Meta never delivered
// them again, and their person may never write again to find out).
func (in *intake) forget(now time.Time) (expired int) {
	in.mu.Lock()
	defer in.mu.Unlock()
	in.expireLocked(now)
	for p := range in.held {
		_, n := in.liveLocked(p, now)
		expired += n
	}
	return expired
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
