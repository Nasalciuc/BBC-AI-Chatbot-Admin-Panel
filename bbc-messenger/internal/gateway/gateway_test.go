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
	answer, header := a.answer, a.header
	a.mu.Unlock()
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
		SendBudget: 2 * time.Second, CallTimeout: 100 * time.Millisecond, HoldFor: time.Second,
	}
	if set != nil {
		set(&cfg)
	}
	srv := &Server{
		Cfg:  cfg,
		Meta: &meta.Client{HTTP: gs.Client(), Base: gs.URL, Version: "v26.0"},
		API:  as.Client(),
		Log:  slog.New(slog.NewJSONHandler(&lockedWriter{w: logs}, nil)),
	}
	ctx, cancel := context.WithCancel(context.Background())
	srv.Start(ctx)
	t.Cleanup(cancel)
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

func TestARefusedEventMetaNeverDeliversAgainHoldsThePersonNoLongerThanHoldFor(t *testing.T) {
	in := newIntake(time.Hour, 100, time.Minute)
	t0 := time.Unix(1_800_000_000, 0)
	ev := func(mid string) meta.Inbound {
		return meta.Inbound{Kind: "message", PageID: pageID, PSID: psid, MID: mid}
	}
	full := func() bool { return false }
	room := func() bool { return true }
	if v, _ := in.admit(ev("m3"), t0, full); v != refused {
		t.Fatalf("m3: %v", v)
	}
	in.holdRest([]meta.Inbound{ev("m3")}, t0)
	if v, _ := in.admit(ev("m4"), t0.Add(30*time.Second), room); v != waiting {
		t.Fatalf("m4 while m3 is awaited: %v", v)
	}
	// m3 never came again: after HoldFor the person's events are taken again.
	v, expired := in.admit(ev("m4"), t0.Add(61*time.Second), room)
	if v != taken || !expired {
		t.Fatalf("m4 after the hold: %v %v", v, expired)
	}
}

func TestTheIntakeForgetsItsOldestKeysWhenFullWithoutAScan(t *testing.T) {
	in := newIntake(time.Hour, 3, time.Minute)
	t0 := time.Unix(1_800_000_000, 0)
	room := func() bool { return true }
	ev := func(mid string) meta.Inbound {
		return meta.Inbound{Kind: "message", PageID: pageID, PSID: psid, MID: mid}
	}
	for i, mid := range []string{"k1", "k2", "k3", "k4"} {
		if v, _ := in.admit(ev(mid), t0.Add(time.Duration(i)*time.Second), room); v != taken {
			t.Fatalf("%s: %v", mid, v)
		}
	}
	// All four are recent: the limit still holds, the oldest went.
	if len(in.at) != 3 {
		t.Fatalf("%d keys kept, the limit is 3", len(in.at))
	}
	if v, _ := in.admit(ev("k2"), t0.Add(5*time.Second), room); v != duplicate {
		t.Errorf("k2 is still a duplicate: %v", v)
	}
	if v, _ := in.admit(ev("k1"), t0.Add(5*time.Second), room); v != taken {
		t.Errorf("k1, the oldest, was forgotten: %v", v)
	}
	// Past their time, all go.
	in.forget(t0.Add(2 * time.Hour))
	if len(in.at) != 0 || in.head != len(in.order) {
		t.Errorf("expired keys kept: %d", len(in.at))
	}
	// Rule 5: a refused event is never marked (it is taken when Meta delivers it again).
	if v, _ := in.admit(ev("k9"), t0.Add(3*time.Hour), func() bool { return false }); v != refused {
		t.Fatalf("k9: %v", v)
	}
	if v, _ := in.admit(ev("k9"), t0.Add(3*time.Hour), room); v != taken {
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
	if code != 200 || out["parts_sent"] != float64(0) || failed["kind"] != "timeout" || n.Load() != before {
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
