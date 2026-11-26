package gateway

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/Nasalciuc/BBC-AI-Chatbot-Admin-Panel/bbc-messenger/internal/meta"
)

// Every id, name and text here is invented.
const (
	appSecret = "test-app-secret"
	gwSecret  = "test-gateway-secret"
	pageID    = "PAGE1"
	pageToken = "page-token-1"
	psid      = "PSID-7"
)

// graph is a fake Send API: it records each call and answers what the test says.
type graph struct {
	mu     sync.Mutex
	calls  []map[string]any
	tokens []string
	answer func(call map[string]any) (int, string)
}

// dropAnswer, as a graph answer's status: Meta read the whole call and the connection dropped before its answer.
const dropAnswer = -1

func (g *graph) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	var call map[string]any
	_ = json.NewDecoder(r.Body).Decode(&call)
	g.mu.Lock()
	g.calls = append(g.calls, call)
	g.tokens = append(g.tokens, r.Header.Get("Authorization"))
	answer := g.answer
	g.mu.Unlock()
	status, body := 200, `{"recipient_id":"PSID-7","message_id":"mid.out"}`
	if answer != nil {
		status, body = answer(call)
	}
	if status == dropAnswer {
		if conn, _, err := w.(http.Hijacker).Hijack(); err == nil {
			_ = conn.Close()
		}
		return
	}
	w.WriteHeader(status)
	_, _ = io.WriteString(w, body)
}

// messages is the texts sent, in order (sender actions left out).
func (g *graph) messages() []map[string]any {
	g.mu.Lock()
	defer g.mu.Unlock()
	var out []map[string]any
	for _, c := range g.calls {
		if m, ok := c["message"].(map[string]any); ok {
			out = append(out, m)
		}
	}
	return out
}

func (g *graph) actions() []string {
	g.mu.Lock()
	defer g.mu.Unlock()
	var out []string
	for _, c := range g.calls {
		if a, ok := c["sender_action"].(string); ok {
			out = append(out, a)
		}
	}
	return out
}

// api is a fake API: it checks the gateway's signature, records the events and answers what the test says.
type api struct {
	t       *testing.T
	mu      sync.Mutex
	events  []map[string]any
	badSigs int
	answer  func(n int, event map[string]any) (int, string)
	header  func(n int, event map[string]any) http.Header // the answer's headers, when the test sets some
	raw     func(n int) string                            // an answer written as it is, then the connection closed
}

func (a *api) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	if r.URL.Path != InboundPath || !validSigned([]byte(gwSecret), time.Now(), r.Header.Get("X-Gateway-Time"),
		r.Header.Get("X-Gateway-Signature"), body) {
		a.mu.Lock()
		a.badSigs++
		a.mu.Unlock()
		http.Error(w, "no", http.StatusUnauthorized)
		return
	}
	var ev map[string]any
	_ = json.Unmarshal(body, &ev)
	a.mu.Lock()
	a.events = append(a.events, ev)
	n := len(a.events)
	answer, header, raw := a.answer, a.header, a.raw
	a.mu.Unlock()
	if raw != nil {
		if conn, buf, err := w.(http.Hijacker).Hijack(); err == nil {
			_, _ = buf.WriteString(raw(n))
			_ = buf.Flush()
			_ = conn.Close()
		}
		return
	}
	status, out := 200, `{"replies":[]}`
	if answer != nil {
		status, out = answer(n, ev)
	}
	if header != nil {
		for k, v := range header(n, ev) {
			w.Header()[k] = v
		}
	}
	w.WriteHeader(status)
	_, _ = io.WriteString(w, out)
}

func (a *api) seen() []map[string]any {
	a.mu.Lock()
	defer a.mu.Unlock()
	return append([]map[string]any(nil), a.events...)
}

type rig struct {
	srv   *Server
	web   *httptest.Server
	graph *graph
	api   *api
	logs  *bytes.Buffer
	stop  context.CancelFunc
}

func newRig(t *testing.T, workers, queue int) *rig {
	t.Helper()
	return newRigWith(t, workers, queue, nil)
}

// newRigWith is a rig whose settings the test changes before it starts.
func newRigWith(t *testing.T, workers, queue int, set func(*Config)) *rig {
	t.Helper()
	g, a := &graph{}, &api{t: t}
	gs, as := httptest.NewServer(g), httptest.NewServer(a)
	// Cleanups run last registered first: the webhook closes, then the workers stop, and only then the fake API and
	// Graph close, so no worker is left running against a closed server.
	t.Cleanup(gs.Close)
	t.Cleanup(as.Close)
	logs := &bytes.Buffer{}
	cfg := Config{
		AppSecret: []byte(appSecret), VerifyToken: "verify-me", AppID: "123456789012345",
		Pages:  map[string]Page{pageID: {ID: pageID, Token: pageToken, Site: "bbc"}},
		APIURL: as.URL, GatewaySecret: []byte(gwSecret), Workers: workers, QueueSize: queue,
		APITimeout: 5 * time.Second, Retries: []time.Duration{time.Millisecond, time.Millisecond, time.Millisecond},
		KeepFor: 200 * time.Millisecond, LongRetry: 5 * time.Millisecond,
		SendBudget: 2 * time.Second, CallTimeout: 500 * time.Millisecond, HoldFor: time.Second,
	}
	if set != nil {
		set(&cfg)
	}
	srv := &Server{
		Cfg: cfg,
		// As main.go makes it: a call to Meta waits CallTimeout for its answer.
		Meta: &meta.Client{HTTP: &http.Client{Transport: gs.Client().Transport, Timeout: cfg.CallTimeout}, Base: gs.URL,
			Version: "v26.0"},
		API: as.Client(),
		Log: slog.New(slog.NewJSONHandler(&lockedWriter{w: logs}, nil)),
	}
	ctx, cancel := context.WithCancel(context.Background())
	srv.Start(ctx)
	// After the bounded stop: the workers are told to stop, and waited for, before the fakes close under them.
	t.Cleanup(func() {
		cancel()
		srv.wg.Wait()
	})
	t.Cleanup(func() {
		stop, done := context.WithTimeout(context.Background(), 2*time.Second)
		defer done()
		srv.Stop(stop)
	})
	web := httptest.NewServer(srv.Handler())
	t.Cleanup(web.Close)
	return &rig{srv: srv, web: web, graph: g, api: a, logs: logs, stop: cancel}
}

type lockedWriter struct {
	mu sync.Mutex
	w  *bytes.Buffer
}

func (l *lockedWriter) Write(p []byte) (int, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.w.Write(p)
}

func (r *rig) logText() string {
	time.Sleep(10 * time.Millisecond)
	return r.logs.String()
}

func hubSign(body string) string {
	mac := hmac.New(sha256.New, []byte(appSecret))
	mac.Write([]byte(body))
	return "sha256=" + hex.EncodeToString(mac.Sum(nil))
}

func (r *rig) post(t *testing.T, body string, signature string) int {
	t.Helper()
	req, _ := http.NewRequest(http.MethodPost, r.web.URL+"/webhook", strings.NewReader(body))
	req.Header.Set("X-Hub-Signature-256", signature)
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	_ = res.Body.Close()
	return res.StatusCode
}

func messageEvent(mid, text string, ts int64, extra string) string {
	return fmt.Sprintf(`{"sender":{"id":%q},"recipient":{"id":%q},"timestamp":%d,"message":{"mid":%q,"text":%q%s}}`,
		psid, pageID, ts, mid, text, extra)
}

// messageFrom is a message of another person on the Page.
func messageFrom(sender, mid, text string, ts int64) string {
	return fmt.Sprintf(`{"sender":{"id":%q},"recipient":{"id":%q},"timestamp":%d,"message":{"mid":%q,"text":%q}}`,
		sender, pageID, ts, mid, text)
}

func batch(page string, events ...string) string {
	return fmt.Sprintf(`{"object":"page","entry":[{"id":%q,"time":1,"messaging":[%s]}]}`, page, strings.Join(events, ","))
}

// until waits for cond, at most two seconds.
func until(t *testing.T, cond func() bool, what string) {
	t.Helper()
	for deadline := time.Now().Add(2 * time.Second); time.Now().Before(deadline); time.Sleep(5 * time.Millisecond) {
		if cond() {
			return
		}
	}
	t.Fatalf("timed out waiting for %s", what)
}

func TestVerify(t *testing.T) {
	r := newRig(t, 1, 4)
	get := func(q string) (int, string) {
		res, err := http.Get(r.web.URL + "/webhook?" + q)
		if err != nil {
			t.Fatal(err)
		}
		defer func() { _ = res.Body.Close() }()
		b, _ := io.ReadAll(res.Body)
		return res.StatusCode, string(b)
	}
	if code, body := get("hub.mode=subscribe&hub.verify_token=verify-me&hub.challenge=1158201444"); code != 200 || body != "1158201444" {
		t.Errorf("Meta's check: %d %q", code, body)
	}
	for _, q := range []string{
		"hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=1",
		"hub.mode=unsubscribe&hub.verify_token=verify-me&hub.challenge=1",
		"hub.mode=subscribe&hub.verify_token=verify-me&hub.challenge=%3Cscript%3E",
		"hub.mode=subscribe&hub.verify_token=verify-me",
	} {
		if code, _ := get(q); code != 403 {
			t.Errorf("%s: %d", q, code)
		}
	}
	// No verify token set: nothing passes, not even an empty one.
	r.srv.Cfg.VerifyToken = ""
	if code, _ := get("hub.mode=subscribe&hub.verify_token=&hub.challenge=1"); code != 403 {
		t.Errorf("unset token: %d", code)
	}
}

func TestAMessageGoesToTheAPIAndItsReplyToThePerson(t *testing.T) {
	r := newRig(t, 2, 8)
	r.api.answer = func(int, map[string]any) (int, string) {
		return 200, `{"replies":[{"text":"Hi! Where are you flying?"},{"text":"How would you like us to reach you?",
			"quick_replies":[{"content_type":"text","title":"SMS","payload":"sms"},{"content_type":"user_email"},
			{"content_type":"text","title":"","payload":"nothing"},{"content_type":"location"}]}]}`
	}
	body := batch(pageID, messageEvent("m1", "Hi, flights to Istanbul?", 100, ""))
	if code := r.post(t, body, hubSign(body)); code != 200 {
		t.Fatalf("webhook: %d", code)
	}
	until(t, func() bool { return len(r.graph.messages()) == 2 }, "two replies")
	ev := r.api.seen()[0]
	if ev["kind"] != "message" || ev["page_id"] != pageID || ev["psid"] != psid || ev["mid"] != "m1" ||
		ev["text"] != "Hi, flights to Istanbul?" || ev["site"] != "bbc" {
		t.Errorf("the event the API got: %v", ev)
	}
	msgs := r.graph.messages()
	if msgs[0]["text"] != "Hi! Where are you flying?" || msgs[0]["quick_replies"] != nil {
		t.Errorf("first reply: %v", msgs[0])
	}
	qr, _ := msgs[1]["quick_replies"].([]any)
	if len(qr) != 2 || qr[0].(map[string]any)["payload"] != "sms" || qr[1].(map[string]any)["content_type"] != "user_email" {
		t.Errorf("quick replies kept to Meta's kinds, each with what it needs: %v", qr)
	}
	// The person sees the message read and Skye typing before the answer; the Page's token goes in a header.
	if a := r.graph.actions(); len(a) != 2 || a[0] != "mark_seen" || a[1] != "typing_on" {
		t.Errorf("actions: %v", a)
	}
	for _, tok := range r.graph.tokens {
		if tok != "Bearer "+pageToken {
			t.Errorf("token: %q", tok)
		}
	}
	// The counters move once the sends have answered: waited for, not raced.
	until(t, func() bool { return r.srv.Health.Replies.Load() == 2 }, "the replies counted")
	h := r.srv.Health.snapshot()
	if h["events"] != 1 || h["forwarded"] != 1 || h["replies_sent"] != 2 || h["send_failed"] != 0 {
		t.Errorf("health: %v", h)
	}
	// The logs never carry what the person wrote or their id.
	if l := r.logText(); strings.Contains(l, "Istanbul") || strings.Contains(l, psid) {
		t.Errorf("a log line carries the person's words or id: %s", l)
	}
}

func TestASignatureThatDoesNotHoldIsRefused(t *testing.T) {
	r := newRig(t, 1, 4)
	body := batch(pageID, messageEvent("m1", "Hi", 100, ""))
	for _, sig := range []string{"", "sha256=" + strings.Repeat("0", 64), hubSign(body + " ")} {
		if code := r.post(t, body, sig); code != 403 {
			t.Errorf("%q: %d", sig, code)
		}
	}
	if r.srv.Health.BadSignature.Load() != 3 || len(r.api.seen()) != 0 {
		t.Errorf("counted and not forwarded: %v", r.srv.Health.snapshot())
	}
	// Signed but not a Page's events: 200 (or Meta sends it for an hour), counted, nothing forwarded.
	other := `{"object":"instagram","entry":[]}`
	if code := r.post(t, other, hubSign(other)); code != 200 || r.srv.Health.Unreadable.Load() != 1 {
		t.Errorf("not a Page's: %d", code)
	}
}

func TestADeliveryMetaMakesAgainIsTakenOnce(t *testing.T) {
	r := newRig(t, 1, 4)
	body := batch(pageID, messageEvent("m1", "Hi", 100, ""))
	r.post(t, body, hubSign(body))
	r.post(t, body, hubSign(body))
	until(t, func() bool { return r.srv.Health.Forwarded.Load() == 1 }, "the forward")
	time.Sleep(20 * time.Millisecond)
	if n := len(r.api.seen()); n != 1 || r.srv.Health.Duplicates.Load() != 1 {
		t.Errorf("forwarded %d, duplicates %d", n, r.srv.Health.Duplicates.Load())
	}
}

func TestOnePersonsEventsReachTheAPIInOrder(t *testing.T) {
	r := newRig(t, 4, 16)
	body := batch(pageID,
		messageEvent("m3", "third", 300, ""), messageEvent("m1", "first", 100, ""), messageEvent("m2", "second", 200, ""))
	r.post(t, body, hubSign(body))
	until(t, func() bool { return len(r.api.seen()) == 3 }, "three events")
	var order []string
	for _, ev := range r.api.seen() {
		order = append(order, ev["mid"].(string))
	}
	if strings.Join(order, ",") != "m1,m2,m3" {
		t.Errorf("order: %v", order)
	}
}

func TestAnUnknownPageAndOwnEchoesAreLeftOut(t *testing.T) {
	r := newRig(t, 1, 4)
	echo := fmt.Sprintf(`{"sender":{"id":%q},"recipient":{"id":%q},"timestamp":5,"message":{"mid":"e1","text":"x","is_echo":true,"app_id":123456789012345}}`, pageID, psid)
	body := batch("OTHER-PAGE", messageEvent("m1", "Hi", 100, ""))
	r.post(t, body, hubSign(body))
	body = batch(pageID, echo)
	r.post(t, body, hubSign(body))
	time.Sleep(30 * time.Millisecond)
	if len(r.api.seen()) != 0 || r.srv.Health.UnknownPage.Load() != 1 || r.srv.Health.OwnEchoes.Load() != 1 {
		t.Errorf("health %v, events %d", r.srv.Health.snapshot(), len(r.api.seen()))
	}
}

func TestAnEchoFromThePageInboxIsKeptAndNotAnswered(t *testing.T) {
	r := newRig(t, 1, 4)
	echo := fmt.Sprintf(`{"sender":{"id":%q},"recipient":{"id":%q},"timestamp":5,"message":{"mid":"e2","text":"Ana here","is_echo":true,"app_id":263902037430900}}`, pageID, psid)
	body := batch(pageID, echo)
	r.post(t, body, hubSign(body))
	until(t, func() bool { return len(r.api.seen()) == 1 }, "the echo")
	if ev := r.api.seen()[0]; ev["kind"] != "echo" || ev["psid"] != psid {
		t.Errorf("echo: %v", ev)
	}
	if len(r.graph.actions()) != 0 {
		t.Errorf("no typing for an echo: %v", r.graph.actions())
	}
}

func TestAFullQueueAsksMetaToDeliverAgainAndNothingOvertakesTheRefusedEvent(t *testing.T) {
	r := newRig(t, 1, 1)
	release := make(chan struct{})
	// Released on every way out of the test (a t.Fatalf included): the fake API's handler never blocks its Close.
	free := sync.OnceFunc(func() { close(release) })
	t.Cleanup(free)
	r.api.answer = func(n int, _ map[string]any) (int, string) {
		if n == 1 {
			<-release
		}
		return 200, `{"replies":[]}`
	}
	first := batch(pageID, messageEvent("m1", "a", 100, ""))
	r.post(t, first, hubSign(first))
	until(t, func() bool { return len(r.api.seen()) == 1 }, "the worker busy on m1")
	second := batch(pageID, messageEvent("m2", "b", 200, ""))
	if code := r.post(t, second, hubSign(second)); code != 200 {
		t.Fatalf("m2 queued: %d", code)
	}
	third := batch(pageID, messageEvent("m3", "c", 300, ""))
	if code := r.post(t, third, hubSign(third)); code != 503 || r.srv.Health.QueueFull.Load() != 1 {
		t.Fatalf("full: %d", code)
	}
	free()
	until(t, func() bool { return len(r.api.seen()) == 2 }, "m2")
	// Room again, but the person's later message, in a delivery of its own, waits for m3: refused, never queued
	// ahead of it, whenever Meta delivers m3 again.
	fourth := batch(pageID, messageEvent("m4", "d", 400, ""))
	if code := r.post(t, fourth, hubSign(fourth)); code != 503 || r.srv.Health.Held.Load() != 1 {
		t.Fatalf("m4 before m3: %d, held %d", code, r.srv.Health.Held.Load())
	}
	// Rule 5: another person on the Page does not wait.
	other := batch(pageID, messageFrom("PSID-8", "q1", "hello", 450))
	if code := r.post(t, other, hubSign(other)); code != 200 {
		t.Fatalf("another person: %d", code)
	}
	until(t, func() bool { return len(r.api.seen()) == 3 }, "q1")
	// Meta delivers m3 again: it was never marked, so it is taken now, and the person's m4 after it.
	if code := r.post(t, third, hubSign(third)); code != 200 {
		t.Fatalf("again: %d", code)
	}
	until(t, func() bool { return len(r.api.seen()) == 4 }, "m3")
	if code := r.post(t, fourth, hubSign(fourth)); code != 200 {
		t.Fatalf("m4 after m3: %d", code)
	}
	until(t, func() bool { return len(r.api.seen()) == 5 }, "m4")
	var order []string
	for _, ev := range r.api.seen() {
		if ev["psid"] == psid {
			order = append(order, ev["mid"].(string))
		}
	}
	if strings.Join(order, ",") != "m1,m2,m3,m4" || r.srv.Health.Duplicates.Load() != 0 {
		t.Errorf("the person's order %v, duplicates %d", order, r.srv.Health.Duplicates.Load())
	}
}

func TestPastMostHeldTheWebhookCountsThePeopleItCouldNotHold(t *testing.T) {
	r := newRig(t, 1, 1)
	release := make(chan struct{})
	free := sync.OnceFunc(func() { close(release) })
	t.Cleanup(free)
	r.api.answer = func(n int, _ map[string]any) (int, string) {
		if n == 1 {
			<-release
		}
		return 200, `{"replies":[]}`
	}
	first := batch(pageID, messageEvent("m1", "a", 100, ""))
	r.post(t, first, hubSign(first))
	until(t, func() bool { return len(r.api.seen()) == 1 }, "the worker busy on m1")
	second := batch(pageID, messageEvent("m2", "b", 200, ""))
	if code := r.post(t, second, hubSign(second)); code != 200 {
		t.Fatalf("m2 queued: %d", code)
	}
	r.srv.intake.mu.Lock()
	r.srv.intake.mostHeld = 0
	r.srv.intake.mu.Unlock()
	third := batch(pageID, messageEvent("m3", "c", 300, ""))
	if code := r.post(t, third, hubSign(third)); code != 503 || r.srv.Health.HoldFull.Load() != 1 {
		t.Fatalf("full, nobody held: %d %v", code, r.srv.Health.snapshot())
	}
	free()
	until(t, func() bool { return len(r.api.seen()) == 2 }, "m2")
	// Not held: the person's later message is taken before m3 comes again, counted above (the bound past which
	// the order gives way, contracts/messenger.md).
	fourth := batch(pageID, messageEvent("m4", "d", 400, ""))
	if code := r.post(t, fourth, hubSign(fourth)); code != 200 {
		t.Errorf("m4: %d", code)
	}
}

// admitOne is the intake's verdict on a delivery of one event, its queue with room or not, and how many of its
// person's refused events it found expired.
func admitOne(in *intake, ev meta.Inbound, now time.Time, room bool) (verdict, int) {
	out, _, _ := in.admit([]meta.Inbound{ev}, now, func(meta.Inbound) bool { return room })
	return out[0].v, out[0].expired
}

func inbound(sender, mid string) meta.Inbound {
	return meta.Inbound{Kind: "message", PageID: pageID, PSID: sender, MID: mid}
}

// at is an event of the person at Meta's time ts.
func at(mid string, ts int64) meta.Inbound {
	return meta.Inbound{Kind: "message", PageID: pageID, PSID: psid, MID: mid, Timestamp: ts}
}

func TestARefusedEventMetaNeverDeliversAgainHoldsThePersonNoLongerThanHoldFor(t *testing.T) {
	in := newIntake(time.Hour, 100, time.Minute)
	t0 := time.Unix(1_800_000_000, 0)
	if v, _ := admitOne(in, inbound(psid, "m3"), t0, false); v != refused {
		t.Fatalf("m3: %v", v)
	}
	if v, _ := admitOne(in, inbound(psid, "m4"), t0.Add(30*time.Second), true); v != waiting {
		t.Fatalf("m4 while m3 is awaited: %v", v)
	}
	// m3 never came again: after HoldFor the person's events are taken again (the order's bound in time).
	v, expired := admitOne(in, inbound(psid, "m4"), t0.Add(61*time.Second), true)
	if v != taken || expired != 1 {
		t.Fatalf("m4 after the hold: %v %v", v, expired)
	}
}

func TestEveryRefusedEventOfAPersonKeepsItsPlaceWhicheverMetaDeliversFirst(t *testing.T) {
	in := newIntake(time.Hour, 100, time.Minute)
	t0 := time.Unix(1_800_000_000, 0)
	// m3 refused (its queue full), then m4 and m5, each in a delivery of its own, while m3 is awaited.
	if v, _ := admitOne(in, at("m3", 300), t0, false); v != refused {
		t.Fatalf("m3: %v", v)
	}
	for _, ev := range []meta.Inbound{at("m4", 400), at("m5", 500)} {
		if v, _ := admitOne(in, ev, t0, true); v != waiting {
			t.Fatalf("%s: %v", ev.MID, v)
		}
	}
	// Meta delivers m3 again, then m5 before m4: m5 waits for m4, not only for m3.
	steps := []struct {
		ev   meta.Inbound
		want verdict
	}{{at("m3", 300), taken}, {at("m5", 500), waiting}, {at("m4", 400), taken}, {at("m5", 500), taken}}
	for _, st := range steps {
		if v, _ := admitOne(in, st.ev, t0.Add(time.Second), true); v != st.want {
			t.Fatalf("%s: %v, want %v", st.ev.MID, v, st.want)
		}
	}
	// Meta's time sets the place, whichever was refused first: m7 refused after m8 still goes first.
	for _, ev := range []meta.Inbound{at("m8", 800), at("m7", 700)} {
		if v, _ := admitOne(in, ev, t0, false); v != refused {
			t.Fatalf("%s: %v", ev.MID, v)
		}
	}
	if v, _ := admitOne(in, at("m8", 800), t0, true); v != waiting {
		t.Errorf("m8 before m7: %v", v)
	}
	if h := in.held[person(at("m7", 0))]; len(in.held) != 1 || h == nil || len(h.q) != 2 {
		t.Errorf("held: %v", in.held)
	}
	for _, ev := range []meta.Inbound{at("m7", 700), at("m8", 800)} {
		if v, _ := admitOne(in, ev, t0, true); v != taken {
			t.Fatalf("%s: %v", ev.MID, v)
		}
	}
	// An event from before all of a person's refused ones never waits for them: taken at once; one after still waits.
	if v, _ := admitOne(in, at("m11", 1100), t0, false); v != refused {
		t.Fatalf("m11: %v", v)
	}
	if v, _ := admitOne(in, at("m10", 1000), t0, true); v != taken {
		t.Errorf("m10 waited for a later event: %v", v)
	}
	if v, _ := admitOne(in, at("m12", 1200), t0, true); v != waiting {
		t.Errorf("m12 before m11: %v", v)
	}
}

func TestAPersonIsWaitedForNoLongerThanHoldForInAllHoweverMetaDeliversAgain(t *testing.T) {
	in := newIntake(time.Hour, 100, 15*time.Minute)
	t0 := time.Unix(1_800_000_000, 0)
	// m3 refused (its queue full), then m2, from before it, refused as well; Meta never delivers m2 again.
	if v, _ := admitOne(in, at("m3", 300), t0, false); v != refused {
		t.Fatalf("m3: %v", v)
	}
	if v, _ := admitOne(in, at("m2", 200), t0.Add(10*time.Minute), false); v != refused {
		t.Fatalf("m2: %v", v)
	}
	// Meta delivers m3 and m4 again in turn, a minute apart, for an hour, their queue with room: each refusal waits
	// for an earlier event, and none, however late, makes the person's wait start again.
	var lastRefused time.Time
	takenAt := map[string]time.Duration{}
	expired := 0
	for minute := 11; minute <= 70; minute++ {
		ev := at("m3", 300)
		if minute%2 == 0 {
			ev = at("m4", 400)
		}
		now := t0.Add(time.Duration(minute) * time.Minute)
		v, n := admitOne(in, ev, now, true)
		expired += n
		switch v {
		case waiting, refused:
			lastRefused = now
		case taken:
			takenAt[ev.MID] = now.Sub(t0)
		}
	}
	if !lastRefused.Before(t0.Add(15 * time.Minute)) {
		t.Errorf("still refused %v after the first refusal", lastRefused.Sub(t0))
	}
	// Taken once each, in their order, as soon as the wait was over; m2 and m4 were in it when it ended (m4 came
	// again after, and was taken).
	if takenAt["m3"] != 15*time.Minute || takenAt["m4"] != 16*time.Minute || len(takenAt) != 2 || expired != 2 {
		t.Errorf("taken %v, %d no longer waited for", takenAt, expired)
	}
	if len(in.held) != 0 {
		t.Errorf("still held: %v", in.held)
	}
}

func TestTheMinutesCleanupCountsTheRefusedEventsItDrops(t *testing.T) {
	in := newIntake(time.Hour, 100, time.Minute)
	t0 := time.Unix(1_800_000_000, 0)
	admitOne(in, at("m3", 300), t0, false)
	admitOne(in, at("m4", 400), t0.Add(30*time.Second), true)
	// Rule 5: none past HoldFor yet.
	if n := in.forget(t0.Add(50 * time.Second)); n != 0 || len(in.held) != 1 {
		t.Fatalf("too soon: %d, %v", n, in.held)
	}
	// The person never wrote again: both are dropped, and counted, without a delivery of theirs.
	if n := in.forget(t0.Add(2 * time.Minute)); n != 2 || len(in.held) != 0 {
		t.Errorf("forgot %d, held %v", n, in.held)
	}
}

func TestARefusalHoldsItsPeopleBeforeAnyOtherDeliveryIsTaken(t *testing.T) {
	in := newIntake(time.Hour, 100, time.Minute)
	t0 := time.Unix(1_800_000_000, 0)
	other := make(chan []admission, 1)
	decided := false
	full := func(meta.Inbound) bool {
		// While m3 is refused, another delivery, of the same person's m4, tries to get in, with room for it: it
		// is decided only after the refusal and its holds, never between them.
		go func() {
			out, _, _ := in.admit([]meta.Inbound{inbound(psid, "m4")}, t0, func(meta.Inbound) bool { return true })
			other <- out
		}()
		time.Sleep(20 * time.Millisecond)
		decided = len(other) > 0
		return false
	}
	out, unheld, _ := in.admit([]meta.Inbound{inbound(psid, "m3"), inbound("PSID-8", "q1")}, t0, full)
	if decided {
		t.Fatal("another delivery was decided in the middle of this one")
	}
	if len(out) != 1 || out[0].v != refused || unheld != 0 {
		t.Fatalf("m3: %+v, %d not held", out, unheld)
	}
	if got := <-other; got[0].v != waiting {
		t.Errorf("m4 got in before m3 was held: %v", got[0].v)
	}
	// The rest of the refused delivery waits too, each person for their first event there.
	if v, _ := admitOne(in, inbound("PSID-8", "q2"), t0, true); v != waiting {
		t.Errorf("q2 before q1: %v", v)
	}
	// Rule 5: someone with no event in it does not wait.
	if v, _ := admitOne(in, inbound("PSID-9", "z1"), t0, true); v != taken {
		t.Errorf("another person: %v", v)
	}
}

func TestPastMostHeldARefusedPersonIsNotHeldAndThatIsCounted(t *testing.T) {
	in := newIntake(time.Hour, 100, time.Minute)
	in.mostHeld = 1
	t0 := time.Unix(1_800_000_000, 0)
	out, unheld, _ := in.admit([]meta.Inbound{inbound("PSID-A", "a1"), inbound("PSID-B", "b1"), inbound("PSID-B", "b2")}, t0,
		func(meta.Inbound) bool { return false })
	if out[0].v != refused || unheld != 2 {
		t.Fatalf("a1: %+v, %d not held", out, unheld)
	}
	// A waits for a1; B could not be held: their later event is taken (the order's bound in memory).
	if v, _ := admitOne(in, inbound("PSID-A", "a2"), t0, true); v != waiting {
		t.Errorf("a2: %v", v)
	}
	if v, _ := admitOne(in, inbound("PSID-B", "b3"), t0, true); v != taken {
		t.Errorf("b3: %v", v)
	}
	// One person waits for mostWaiting events at most.
	one := newIntake(time.Hour, 1000, time.Minute)
	var many []meta.Inbound
	for i := range mostWaiting + 1 {
		many = append(many, inbound("PSID-C", fmt.Sprintf("c%d", i)))
	}
	if _, unheld, _ := one.admit(many, t0, func(meta.Inbound) bool { return false }); unheld != 1 {
		t.Errorf("past mostWaiting: %d not held", unheld)
	}
}

func TestAWaitPastHoldForIsDroppedBeforeItsPersonsRoomIsCounted(t *testing.T) {
	in := newIntake(time.Hour, 1000, time.Minute)
	t0 := time.Unix(1_800_000_000, 0)
	of := func(mid string, ts int64) meta.Inbound {
		return meta.Inbound{Kind: "message", PageID: pageID, PSID: "PSID-B", MID: mid, Timestamp: ts}
	}
	// B waits for mostWaiting refused events, and Meta never delivers them again.
	var many []meta.Inbound
	for i := range mostWaiting {
		many = append(many, of(fmt.Sprintf("b%d", i), int64(i)))
	}
	if _, unheld, _ := in.admit(many, t0, func(meta.Inbound) bool { return false }); unheld != 0 {
		t.Fatalf("%d not held", unheld)
	}
	// Past HoldFor, a delivery stops on A's event with B's next one behind it: B's old wait is dropped (counted)
	// before B's room is counted, so their new event takes its place.
	later := t0.Add(2 * time.Minute)
	out, unheld, expired := in.admit([]meta.Inbound{inbound("PSID-A", "a1"), of("b-new", 1000)}, later,
		func(meta.Inbound) bool { return false })
	if out[0].v != refused || unheld != 0 || expired != mostWaiting {
		t.Fatalf("a1: %+v, %d not held, %d no longer waited for", out, unheld, expired)
	}
	// Rule 5: B's next one, with room, waits for it, never goes before it.
	if v, _ := admitOne(in, of("b-newer", 1001), later, true); v != waiting {
		t.Errorf("b-newer before b-new: %v", v)
	}
}

func TestTheIntakeForgetsItsOldestKeysWhenFullWithoutAScan(t *testing.T) {
	in := newIntake(time.Hour, 3, time.Minute)
	t0 := time.Unix(1_800_000_000, 0)
	for i, mid := range []string{"k1", "k2", "k3", "k4"} {
		if v, _ := admitOne(in, inbound(psid, mid), t0.Add(time.Duration(i)*time.Second), true); v != taken {
			t.Fatalf("%s: %v", mid, v)
		}
	}
	// All four are recent: the limit still holds, the oldest went.
	if len(in.at) != 3 {
		t.Fatalf("%d keys kept, the limit is 3", len(in.at))
	}
	if v, _ := admitOne(in, inbound(psid, "k2"), t0.Add(5*time.Second), true); v != duplicate {
		t.Errorf("k2 is still a duplicate: %v", v)
	}
	if v, _ := admitOne(in, inbound(psid, "k1"), t0.Add(5*time.Second), true); v != taken {
		t.Errorf("k1, the oldest, was forgotten: %v", v)
	}
	// Past their time, all go.
	in.forget(t0.Add(2 * time.Hour))
	if len(in.at) != 0 || in.head != len(in.order) {
		t.Errorf("expired keys kept: %d", len(in.at))
	}
	// Rule 5: a refused event is never marked (it is taken when Meta delivers it again).
	if v, _ := admitOne(in, inbound(psid, "k9"), t0.Add(3*time.Hour), false); v != refused {
		t.Fatalf("k9: %v", v)
	}
	if v, _ := admitOne(in, inbound(psid, "k9"), t0.Add(3*time.Hour), true); v != taken {
		t.Errorf("k9 again: %v", v)
	}
}

func TestTheSameWebhookTwiceAtOnceIsTakenOnce(t *testing.T) {
	r := newRig(t, 2, 16)
	body := batch(pageID, messageEvent("m1", "Hi", 100, ""))
	var wg sync.WaitGroup
	for range 8 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			r.post(t, body, hubSign(body))
		}()
	}
	wg.Wait()
	until(t, func() bool { return r.srv.Health.Forwarded.Load() == 1 }, "the forward")
	time.Sleep(20 * time.Millisecond)
	// One taken, seven duplicates: one forward, and the person sees it read and Skye typing once.
	if len(r.api.seen()) != 1 || r.srv.Health.Duplicates.Load() != 7 || len(r.graph.actions()) != 2 {
		t.Errorf("forwarded %d, duplicates %d, actions %v", len(r.api.seen()), r.srv.Health.Duplicates.Load(),
			r.graph.actions())
	}
}

func TestTheAPIIsTriedAgainWhileItIsDownButNotWhenItRefuses(t *testing.T) {
	r := newRig(t, 1, 4)
	r.api.answer = func(n int, ev map[string]any) (int, string) {
		switch ev["mid"] {
		case "m1": // down twice, then up
			if n <= 2 {
				return 503, `{}`
			}
			return 200, `{"replies":[{"text":"Here now"}]}`
		case "m2":
			return 409, `{}` // still on the same event: later
		}
		return 400, `{"detail":"bad"}`
	}
	body := batch(pageID, messageEvent("m1", "a", 100, ""))
	r.post(t, body, hubSign(body))
	until(t, func() bool { return len(r.graph.messages()) == 1 }, "the reply after two retries")
	if r.srv.Health.ForwardFailed.Load() != 0 || r.srv.Health.Forwarded.Load() != 1 {
		t.Errorf("health %v", r.srv.Health.snapshot())
	}
	body = batch(pageID, messageEvent("m9", "z", 900, ""))
	r.post(t, body, hubSign(body))
	until(t, func() bool { return r.srv.Health.ForwardRefused.Load() == 1 }, "the refusal")
	if n := calls(r, "m9"); n != 1 {
		t.Errorf("a refusal is not tried again: %d calls", n)
	}
	// Still on the same event (409) for good: tried like an API that can't be reached, until KeepFor.
	body = batch(pageID, messageEvent("m2", "b", 200, ""))
	r.post(t, body, hubSign(body))
	until(t, func() bool { return r.srv.Health.ForwardFailed.Load() == 1 }, "409 until KeepFor")
	if n := calls(r, "m2"); n <= 4 {
		t.Errorf("409 tried past Retries until KeepFor: %d calls", n)
	}
}

func calls(r *rig, mid string) int {
	n := 0
	for _, ev := range r.api.seen() {
		if ev["mid"] == mid {
			n++
		}
	}
	return n
}

func TestAnEventTheAPIHasNotTakenIsKeptAndTriedAgain(t *testing.T) {
	r := newRigWith(t, 1, 8, func(c *Config) { c.KeepFor = 2 * time.Second })
	r.api.answer = func(_ int, ev map[string]any) (int, string) {
		switch ev["mid"] {
		case "down": // the API's proxy answering for it, more times than Retries allow
			if calls(r, "down") <= 6 {
				return 502, `bad gateway`
			}
			return 200, `{"replies":[{"text":"Here now"}]}`
		case "off", "kept":
			return 503, `{}`
		}
		return 200, `{"replies":[]}`
	}
	r.api.header = func(_ int, ev map[string]any) http.Header {
		switch ev["mid"] {
		case "off":
			return http.Header{HeaderOff: {"1"}}
		case "kept":
			return http.Header{HeaderKept: {"1"}}
		}
		return nil
	}
	body := batch(pageID, messageEvent("down", "a", 100, ""))
	r.post(t, body, hubSign(body))
	until(t, func() bool { return len(r.graph.messages()) == 1 }, "the reply once the API is back")
	if n := calls(r, "down"); n != 7 || r.srv.Health.ForwardFailed.Load() != 0 {
		t.Errorf("kept until the API took it: %d calls, %v", n, r.srv.Health.snapshot())
	}
	// The channel off: final at once, nothing tried again.
	body = batch(pageID, messageEvent("off", "b", 200, ""))
	r.post(t, body, hubSign(body))
	until(t, func() bool { return r.srv.Health.APIOff.Load() == 1 }, "the channel off")
	// Kept by the API (its turn failed, it answers later itself): Retries, then left to it.
	body = batch(pageID, messageEvent("kept", "c", 300, ""))
	r.post(t, body, hubSign(body))
	until(t, func() bool { return r.srv.Health.ForwardKept.Load() == 1 }, "kept by the API")
	if calls(r, "off") != 1 || calls(r, "kept") != 4 || r.srv.Health.ForwardFailed.Load() != 0 {
		t.Errorf("off %d calls, kept %d calls, %v", calls(r, "off"), calls(r, "kept"), r.srv.Health.snapshot())
	}
}

func TestSendFailuresAreCountedByKindAndOnlyMetasOwnTroubleIsTriedAgain(t *testing.T) {
	r := newRig(t, 1, 4)
	r.api.answer = func(int, map[string]any) (int, string) { return 200, `{"replies":[{"text":"Hello"}]}` }
	var tries atomic.Int32
	r.graph.answer = func(call map[string]any) (int, string) {
		if _, ok := call["sender_action"]; ok {
			return 200, `{}`
		}
		if tries.Add(1) == 1 {
			return 500, `{"error":{"message":"oops","code":2}}`
		}
		return 400, `{"error":{"message":"outside the window","code":10,"error_subcode":2018278}}`
	}
	body := batch(pageID, messageEvent("m1", "a", 100, ""))
	r.post(t, body, hubSign(body))
	until(t, func() bool { return r.srv.Health.WindowClosed.Load() == 1 }, "window closed")
	if tries.Load() != 2 || r.srv.Health.SendFailed.Load() != 1 || r.srv.Health.Replies.Load() != 0 {
		t.Errorf("tries %d, %v", tries.Load(), r.srv.Health.snapshot())
	}
}

func TestALongReplyGoesAsSeveralWithTheButtonsOnTheLast(t *testing.T) {
	r := newRig(t, 1, 4)
	long := strings.Repeat("word ", 900) // 4500 characters
	r.api.answer = func(int, map[string]any) (int, string) {
		b, _ := json.Marshal(map[string]any{"replies": []any{map[string]any{"text": long,
			"quick_replies": []any{map[string]any{"content_type": "text", "title": "Yes", "payload": "yes"}}}}})
		return 200, string(b)
	}
	body := batch(pageID, messageEvent("m1", "a", 100, ""))
	r.post(t, body, hubSign(body))
	until(t, func() bool { return len(r.graph.messages()) == 3 }, "three pieces")
	msgs := r.graph.messages()
	if msgs[0]["quick_replies"] != nil || msgs[1]["quick_replies"] != nil || msgs[2]["quick_replies"] == nil {
		t.Errorf("buttons on the last piece only")
	}
	until(t, func() bool { return r.srv.Health.Replies.Load() == 1 }, "one reply sent whole")
}

func TestTheBatchsQuickRepliesGoOnItsLastMessageOnly(t *testing.T) {
	r := newRig(t, 1, 4)
	yes := map[string]any{"content_type": "text", "title": "Yes", "payload": "yes"}
	sms := map[string]any{"content_type": "text", "title": "SMS", "payload": "sms"}
	r.api.answer = func(n int, _ map[string]any) (int, string) {
		replies := []any{ // m1: buttons on a reply before the last one; m2: on two of them
			map[string]any{"text": "Which dates?", "quick_replies": []any{yes}},
			map[string]any{"text": "And how should we reach you?"},
		}
		if n == 2 {
			replies[1] = map[string]any{"text": "And how should we reach you?", "quick_replies": []any{sms}}
		}
		b, _ := json.Marshal(map[string]any{"replies": replies})
		return 200, string(b)
	}
	body := batch(pageID, messageEvent("m1", "a", 100, ""))
	r.post(t, body, hubSign(body))
	until(t, func() bool { return len(r.graph.messages()) == 2 }, "two replies")
	body = batch(pageID, messageEvent("m2", "b", 200, ""))
	r.post(t, body, hubSign(body))
	until(t, func() bool { return len(r.graph.messages()) == 4 }, "two more")
	msgs := r.graph.messages()
	payload := func(m map[string]any) string {
		qr, _ := m["quick_replies"].([]any)
		if len(qr) != 1 {
			return fmt.Sprint(len(qr))
		}
		return qr[0].(map[string]any)["payload"].(string)
	}
	// Messenger shows quick replies on the newest message only: the batch's go on its last one, the last reply's
	// that has any.
	if payload(msgs[0]) != "0" || payload(msgs[1]) != "yes" || payload(msgs[2]) != "0" || payload(msgs[3]) != "sms" {
		t.Errorf("quick replies: %s %s %s %s", payload(msgs[0]), payload(msgs[1]), payload(msgs[2]), payload(msgs[3]))
	}
}

func TestStopCountsAnEventStillWithItsWorker(t *testing.T) {
	r := newRig(t, 1, 4)
	release := make(chan struct{})
	free := sync.OnceFunc(func() { close(release) })
	t.Cleanup(free)
	r.api.answer = func(int, map[string]any) (int, string) {
		<-release
		return 200, `{"replies":[]}`
	}
	body := batch(pageID, messageEvent("m1", "a", 100, ""))
	r.post(t, body, hubSign(body))
	until(t, func() bool { return len(r.api.seen()) == 1 }, "m1 with its worker")
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()
	// The queue is empty, but m1 is not done: the stop that runs out of time counts it.
	r.srv.Stop(ctx)
	if got := r.srv.Health.DroppedAtShutdown.Load(); got != 1 {
		t.Errorf("dropped at shutdown: %d", got)
	}
}

func TestWithoutTheAppsIDTheGatewayIsNotReady(t *testing.T) {
	// Its own messages would come back as echoes the API takes for the Page inbox's: not ready, nothing taken.
	r := newRigWith(t, 1, 4, func(c *Config) { c.AppID = "" })
	body := batch(pageID, messageEvent("m1", "Hi", 100, ""))
	if code := r.post(t, body, hubSign(body)); code != 503 || r.srv.Ready() {
		t.Errorf("no app id: %d, ready %v", code, r.srv.Ready())
	}
}

func TestAWebhookIsRefusedUntilTheGatewayIsSetUp(t *testing.T) {
	r := newRigWith(t, 1, 4, func(c *Config) { c.GatewaySecret = nil })
	body := batch(pageID, messageEvent("m1", "Hi", 100, ""))
	// 503: Meta delivers it again once every variable is set, nothing is taken and dropped meanwhile.
	if code := r.post(t, body, hubSign(body)); code != 503 || r.srv.Health.NotReady.Load() != 1 {
		t.Fatalf("not configured: %d", code)
	}
	time.Sleep(20 * time.Millisecond)
	if len(r.api.seen()) != 0 || r.srv.Health.Events.Load() != 0 {
		t.Errorf("an event was taken: %v", r.srv.Health.snapshot())
	}
	// Meta's check when the webhook is saved, and /health, still answer during the setup.
	res, err := http.Get(r.web.URL + "/webhook?hub.mode=subscribe&hub.verify_token=verify-me&hub.challenge=42")
	if err != nil {
		t.Fatal(err)
	}
	_ = res.Body.Close()
	h, err := http.Get(r.web.URL + "/health")
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = h.Body.Close() }()
	var out map[string]any
	_ = json.NewDecoder(h.Body).Decode(&out)
	if res.StatusCode != 200 || out["ready"] != false {
		t.Errorf("verify %d, health %v", res.StatusCode, out)
	}
}

func signedSend(t *testing.T, r *rig, body string, at time.Time, secret string) (int, map[string]any) {
	t.Helper()
	req, _ := http.NewRequest(http.MethodPost, r.web.URL+"/v1/send", strings.NewReader(body))
	req.Header.Set("X-Gateway-Time", strconv.FormatInt(at.Unix(), 10))
	req.Header.Set("X-Gateway-Signature", Sign([]byte(secret), at.Unix(), []byte(body)))
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = res.Body.Close() }()
	var out map[string]any
	_ = json.NewDecoder(res.Body).Decode(&out)
	return res.StatusCode, out
}

func TestTheAPISendsAnAgentsMessageThroughTheGateway(t *testing.T) {
	r := newRig(t, 1, 4)
	call := fmt.Sprintf(`{"page_id":%q,"psid":%q,"messages":[{"text":"Hi, I'm Ana from the team."},{"text":"Which dates suit you?"}]}`, pageID, psid)
	code, out := signedSend(t, r, call, time.Now(), gwSecret)
	if code != 200 || out["sent"] != float64(2) || out["parts"] != float64(2) || out["parts_sent"] != float64(2) ||
		out["failed"] != nil {
		t.Fatalf("send: %d %v", code, out)
	}
	if msgs := r.graph.messages(); len(msgs) != 2 || msgs[1]["text"] != "Which dates suit you?" {
		t.Errorf("sent: %v", msgs)
	}
	// Refused: another secret, a call recorded and played again later, an unknown page, a tag Meta would refuse.
	if code, _ := signedSend(t, r, call, time.Now(), "another-secret"); code != 401 {
		t.Errorf("other secret: %d", code)
	}
	if code, _ := signedSend(t, r, call, time.Now().Add(-10*time.Minute), gwSecret); code != 401 {
		t.Errorf("played again: %d", code)
	}
	unknown := strings.Replace(call, pageID, "OTHER", 1)
	if code, _ := signedSend(t, r, unknown, time.Now(), gwSecret); code != 404 {
		t.Errorf("unknown page: %d", code)
	}
	tagged := strings.Replace(call, `"messages"`, `"tag":"ACCOUNT_UPDATE","messages"`, 1)
	if code, _ := signedSend(t, r, tagged, time.Now(), gwSecret); code != 400 {
		t.Errorf("tag: %d", code)
	}
	extra := strings.Replace(call, `"messages"`, `"access_token":"x","messages"`, 1)
	if code, _ := signedSend(t, r, extra, time.Now(), gwSecret); code != 400 {
		t.Errorf("unknown field: %d", code)
	}
	// Past the 24 hours: 200 with why, so the API can tell the agent.
	r.graph.answer = func(map[string]any) (int, string) {
		return 400, `{"error":{"message":"outside the window","code":10,"error_subcode":2018278}}`
	}
	code, out = signedSend(t, r, call, time.Now(), gwSecret)
	failed, _ := out["failed"].(map[string]any)
	if code != 200 || out["sent"] != float64(0) || failed["kind"] != "window_closed" || failed["subcode"] != float64(2018278) {
		t.Errorf("window closed: %d %v", code, out)
	}
}

func agentCall(text string) string {
	b, _ := json.Marshal(map[string]any{"page_id": pageID, "psid": psid, "messages": []any{map[string]any{"text": text}}})
	return string(b)
}

func TestALongAgentsMessageCutShortSaysHowMuchWentOut(t *testing.T) {
	r := newRig(t, 1, 4)
	var n atomic.Int32
	r.graph.answer = func(map[string]any) (int, string) {
		if n.Add(1) == 1 {
			return 200, `{"message_id":"mid.1"}`
		}
		return 400, `{"error":{"message":"outside the window","code":10,"error_subcode":2018278}}`
	}
	code, out := signedSend(t, r, agentCall(strings.Repeat("word ", 900)), time.Now(), gwSecret) // 3 pieces
	failed, _ := out["failed"].(map[string]any)
	// Not one message whole, but its first piece is with the person: the API tells the agent so, never "send again".
	if code != 200 || out["sent"] != float64(0) || out["parts"] != float64(3) || out["parts_sent"] != float64(1) ||
		failed["kind"] != "window_closed" {
		t.Errorf("cut short: %d %v", code, out)
	}
}

func TestAnAgentsSendKeepsToItsBudgetAndSaysWhatWentOut(t *testing.T) {
	r := newRigWith(t, 1, 4, func(c *Config) {
		c.SendBudget = 300 * time.Millisecond
		c.CallTimeout = 50 * time.Millisecond
		c.Retries = []time.Duration{200 * time.Millisecond, 200 * time.Millisecond, 200 * time.Millisecond}
	})
	var n atomic.Int32
	r.graph.answer = func(map[string]any) (int, string) {
		if n.Add(1) == 1 {
			return 200, `{"message_id":"mid.1"}`
		}
		return 500, `{"error":{"message":"oops","code":2}}`
	}
	start := time.Now()
	code, out := signedSend(t, r, agentCall(strings.Repeat("word ", 900)), time.Now(), gwSecret)
	failed, _ := out["failed"].(map[string]any)
	if code != 200 || out["sent"] != float64(0) || out["parts_sent"] != float64(1) || failed["kind"] != "timeout" {
		t.Errorf("out of time: %d %v", code, out)
	}
	if took := time.Since(start); took > time.Second {
		t.Errorf("the send outlived its budget: %v", took)
	}
	// The wait for the person's lock is in the budget: a send stuck behind another of theirs answers in time, with
	// nothing sent.
	unlock, err := r.srv.sending.lock(context.Background(), pageID, psid)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(sync.OnceFunc(unlock))
	before := n.Load()
	code, out = signedSend(t, r, agentCall("second"), time.Now(), gwSecret)
	failed, _ = out["failed"].(map[string]any)
	if code != 200 || out["parts"] != float64(1) || out["parts_sent"] != float64(0) || failed["kind"] != "timeout" ||
		n.Load() != before {
		t.Errorf("behind another send: %d %v", code, out)
	}
	if r.srv.Health.OutOfTime.Load() != 2 {
		t.Errorf("out of time counted: %v", r.srv.Health.snapshot())
	}
}

func TestAnAgentsSendMetaMayHaveCarriedOutIsNeverSentTwice(t *testing.T) {
	r := newRig(t, 1, 4)
	r.graph.answer = func(map[string]any) (int, string) { return dropAnswer, "" }
	code, out := signedSend(t, r, agentCall("Hi, I'm Ana."), time.Now(), gwSecret)
	failed, _ := out["failed"].(map[string]any)
	if code != 200 || out["sent"] != float64(0) || failed["kind"] != "unknown" {
		t.Errorf("unconfirmed: %d %v", code, out)
	}
	if got := len(r.graph.messages()); got != 1 || r.srv.Health.Unconfirmed.Load() != 1 {
		t.Errorf("sent %d times: %v", got, r.srv.Health.snapshot())
	}
}

func TestASendWithNoSureAnswerIsUnknownAndNeverSentAgain(t *testing.T) {
	for name, answer := range map[string]func() (int, string){
		"a proxy's 504":           func() (int, string) { return 504, "upstream timed out" },
		"a 500 that does not say": func() (int, string) { return 500, `{"error":{"message":"An unknown error occurred","code":1}}` },
		// A proxy's 503 when the server it passed the call to went away: Meta may have sent it.
		"a 503 that does not say": func() (int, string) { return 503, "upstream connect error or disconnect/reset before headers" },
	} {
		t.Run(name, func(t *testing.T) {
			r := newRig(t, 1, 4)
			r.graph.answer = func(map[string]any) (int, string) { return answer() }
			code, out := signedSend(t, r, agentCall("Hi, I'm Ana."), time.Now(), gwSecret)
			failed, _ := out["failed"].(map[string]any)
			if code != 200 || out["sent"] != float64(0) || failed["kind"] != "unknown" {
				t.Errorf("unconfirmed: %d %v", code, out)
			}
			if got := len(r.graph.messages()); got != 1 || r.srv.Health.Unconfirmed.Load() != 1 {
				t.Errorf("sent %d times: %v", got, r.srv.Health.snapshot())
			}
		})
	}
	// Rule 5: Meta itself saying it did not send it, for now (a 503 with its code 2), is tried again.
	r := newRig(t, 1, 4)
	var n atomic.Int32
	r.graph.answer = func(map[string]any) (int, string) {
		if n.Add(1) == 1 {
			return 503, `{"error":{"message":"Service temporarily unavailable","code":2}}`
		}
		return 200, `{"message_id":"mid.1"}`
	}
	if code, out := signedSend(t, r, agentCall("Hi, I'm Ana."), time.Now(), gwSecret); code != 200 || out["sent"] != float64(1) || n.Load() != 2 {
		t.Errorf("503 code 2 then sent: %d %v, %d calls", code, out, n.Load())
	}
}

func TestASendWhoseAnswerOutlivedTheCallTimeoutIsUnknownNotOutOfTime(t *testing.T) {
	r := newRigWith(t, 1, 4, func(c *Config) { c.CallTimeout = 100 * time.Millisecond })
	release := make(chan struct{})
	free := sync.OnceFunc(func() { close(release) })
	t.Cleanup(free)
	r.graph.answer = func(map[string]any) (int, string) {
		<-release
		return 200, `{"message_id":"mid.late"}`
	}
	code, out := signedSend(t, r, agentCall("Hi, I'm Ana."), time.Now(), gwSecret)
	failed, _ := out["failed"].(map[string]any)
	// Meta had it whole: the wait running out is a message that may be with the person, never "not sent".
	if code != 200 || failed["kind"] != "unknown" || r.srv.Health.OutOfTime.Load() != 0 {
		t.Errorf("no answer in time: %d %v", code, out)
	}
	if got := len(r.graph.messages()); got != 1 || r.srv.Health.Unconfirmed.Load() != 1 {
		t.Errorf("sent %d times: %v", got, r.srv.Health.snapshot())
	}
}

func TestALongAgentsMessageWhoseNextPieceIsUnconfirmedSaysSo(t *testing.T) {
	r := newRig(t, 1, 4)
	var n atomic.Int32
	r.graph.answer = func(map[string]any) (int, string) {
		if n.Add(1) == 1 {
			return 200, `{"message_id":"mid.1"}`
		}
		return 504, "upstream timed out"
	}
	code, out := signedSend(t, r, agentCall(strings.Repeat("word ", 900)), time.Now(), gwSecret) // 3 pieces
	failed, _ := out["failed"].(map[string]any)
	// The first piece is with the person and the second may be: neither is sent again, and the API is told
	// which is which (it tells the agent).
	if code != 200 || out["parts"] != float64(3) || out["parts_sent"] != float64(1) || failed["kind"] != "unknown" ||
		n.Load() != 2 {
		t.Errorf("cut short, unconfirmed: %d %v, %d calls", code, out, n.Load())
	}
}

// clock is a test's time: it moves only when the server waits.
type clock struct {
	mu  sync.Mutex
	now time.Time
}

func (c *clock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.now
}

func (c *clock) sleep(ctx context.Context, d time.Duration) bool {
	c.mu.Lock()
	c.now = c.now.Add(d)
	c.mu.Unlock()
	return ctx.Err() == nil
}

// recordingTransport answers each call to the API the same, and keeps when (by the test's clock) each one started.
type recordingTransport struct {
	clock  *clock
	status int
	starts []time.Time
}

func (rt *recordingTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	rt.starts = append(rt.starts, rt.clock.Now())
	return &http.Response{StatusCode: rt.status, Header: http.Header{}, Body: io.NopCloser(strings.NewReader("busy")),
		Request: r}, nil
}

func TestNoCallToTheAPIStartsOnceKeepForIsOver(t *testing.T) {
	t0 := time.Unix(1_800_000_000, 0)
	c := &clock{now: t0}
	rt := &recordingTransport{clock: c, status: http.StatusInternalServerError}
	s := &Server{
		Cfg: Config{APIURL: "https://api.example.test", GatewaySecret: []byte(gwSecret), APITimeout: time.Second,
			Retries: []time.Duration{2 * time.Second, 5 * time.Second, 15 * time.Second}, KeepFor: time.Hour,
			LongRetry: time.Minute},
		API: &http.Client{Transport: rt}, Log: slog.New(slog.NewTextHandler(io.Discard, nil)), Now: c.Now,
		sleep: c.sleep,
	}
	if _, err := s.forward(context.Background(), inbound(psid, "m1"), "bbc"); err == nil || s.Health.ForwardFailed.Load() != 1 {
		t.Fatalf("given up: %v, forward_failed %d", err, s.Health.ForwardFailed.Load())
	}
	// At 0, after the Retries (2, 7, 22 s), then every LongRetry: 82 s, ... 3562 s, just before the hour; the last wait
	// is cut to what was left of it (38 s), and at the hour no call starts.
	last := rt.starts[len(rt.starts)-1].Sub(t0)
	if len(rt.starts) != 63 || last != 3562*time.Second || c.Now().Sub(t0) != time.Hour {
		t.Errorf("%d calls, the last at %v, the clock at %v", len(rt.starts), last, c.Now().Sub(t0))
	}
	for i, at := range rt.starts {
		if at.Sub(t0) >= time.Hour {
			t.Errorf("call %d started at %v, KeepFor over", i, at.Sub(t0))
		}
	}
}

func TestAnAPIAnswerWhoseBodyBreaksOffIsStillItsStatus(t *testing.T) {
	r := newRig(t, 1, 4)
	r.api.raw = func(n int) string {
		if n == 1 {
			return "HTTP/1.1 503 Service Unavailable\r\nX-Messenger-Off: 1\r\nContent-Length: 50\r\n\r\noff"
		}
		return "HTTP/1.1 401 Unauthorized\r\nContent-Length: 50\r\n\r\nno"
	}
	body := batch(pageID, messageEvent("m1", "a", 100, ""))
	r.post(t, body, hubSign(body))
	until(t, func() bool { return r.srv.Health.APIOff.Load() == 1 }, "the channel off")
	body = batch(pageID, messageEvent("m2", "b", 200, ""))
	r.post(t, body, hubSign(body))
	until(t, func() bool { return r.srv.Health.ForwardRefused.Load() == 1 }, "a refusal")
	// Each read once, as the API answered it: never tried again because its body broke off.
	time.Sleep(20 * time.Millisecond)
	if len(r.api.seen()) != 2 || r.srv.Health.ForwardFailed.Load() != 0 {
		t.Errorf("%d calls, %v", len(r.api.seen()), r.srv.Health.snapshot())
	}
}

func TestHealthSaysWhatItHasAndNoSecret(t *testing.T) {
	r := newRig(t, 2, 8)
	res, err := http.Get(r.web.URL + "/health")
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = res.Body.Close() }()
	b, _ := io.ReadAll(res.Body)
	var out map[string]any
	_ = json.Unmarshal(b, &out)
	if res.StatusCode != 200 || out["ready"] != true || out["pages"] != float64(1) {
		t.Errorf("health: %s", b)
	}
	for _, secret := range []string{appSecret, gwSecret, pageToken, "verify-me"} {
		if strings.Contains(string(b), secret) {
			t.Errorf("health shows a secret")
		}
	}
	if q := out["queue"].(map[string]any); q["capacity"] != float64(16) {
		t.Errorf("queue: %v", q)
	}
}

func TestStopTakesTheQueuedEventsToTheAPI(t *testing.T) {
	r := newRig(t, 1, 8)
	body := batch(pageID, messageEvent("m1", "a", 100, ""), messageEvent("m2", "b", 200, ""), messageEvent("m3", "c", 300, ""))
	r.post(t, body, hubSign(body))
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	r.srv.Stop(ctx)
	if n := len(r.api.seen()); n != 3 || r.srv.Health.DroppedAtShutdown.Load() != 0 {
		t.Errorf("forwarded %d before stopping", n)
	}
	// After the stop, Meta's events are refused (503: delivered again to the next process).
	more := batch(pageID, messageEvent("m4", "d", 400, ""))
	if code := r.post(t, more, hubSign(more)); code != 503 {
		t.Errorf("after stop: %d", code)
	}
}

// The API signs and checks the same way (bbc-chatbot-api tests/test_messenger_channel.py holds the same vector).
func TestTheSignatureIsTheAPIs(t *testing.T) {
	got := Sign([]byte("shared-test-secret"), 1700000000, []byte(`{"page_id":"PAGE1"}`))
	if got != "sha256=6ba3016f066046b9feae73d4fc707c60be6500545ede51024b6037601f8bb434" {
		t.Errorf("signature: %s", got)
	}
}
