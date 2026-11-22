package live

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/Nasalciuc/BBC-AI-Chatbot-Admin-Panel/bbc-live/internal/redistest"
	"github.com/Nasalciuc/BBC-AI-Chatbot-Admin-Panel/bbc-live/internal/resp"
	"github.com/Nasalciuc/BBC-AI-Chatbot-Admin-Panel/bbc-live/internal/token"
)

func TestMain(m *testing.M) {
	code := m.Run()
	redistest.Stop()
	os.Exit(code)
}

var (
	secret = []byte("live-test-secret")
)

const (
	vid1   = "3f1c2a7e-8b4d-4c1e-9a2f-6d5e4c3b2a10"
	vid2   = "4a2d3b8f-9c5e-4d2f-8b3a-7e6f5d4c3b21"
	agentA = "aaaaaaaa-0000-4000-8000-000000000001"
	agentB = "aaaaaaaa-0000-4000-8000-000000000002"
)

type clock struct{ t atomic.Int64 }

func (c *clock) now() time.Time { return time.Unix(c.t.Load(), 0) }
func (c *clock) add(s int64)    { c.t.Add(s) }

func setup(t *testing.T) (*Server, *clock, *resp.Client) {
	t.Helper()
	o, err := resp.ParseURL(redistest.URL(t))
	if err != nil {
		t.Fatal(err)
	}
	rc := resp.New(o)
	t.Cleanup(func() { _ = rc.Close() })
	c := &clock{}
	c.t.Store(time.Now().Unix())
	s := &Server{
		Store: &Store{R: rc, Now: c.now}, Hub: NewHub(), Secret: secret,
		Origins: map[string]bool{"https://www.example-site.test": true}, Now: c.now,
		Log:      slog.New(slog.NewTextHandler(io.Discard, nil)),
		PollWait: 2 * time.Second, Recheck: 300 * time.Millisecond, MinBeatGap: 5 * time.Second,
	}
	return s, c, rc
}

func visitorPass(c *clock, vid string) string {
	n := c.now().Unix()
	return token.IssuePassForTests(vid, n-10, n+86400, secret)
}

func agentToken(c *clock, user, role, tunnel string) string {
	n := c.now().Unix()
	return token.IssueAgentForTests(token.Agent{User: user, Role: role, Tunnel: tunnel, IssuedAt: n, Expires: n + 60}, secret)
}

func do(t *testing.T, h http.Handler, method, path string, headers map[string]string, body any) *httptest.ResponseRecorder {
	t.Helper()
	var rd io.Reader
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			t.Fatal(err)
		}
		rd = bytes.NewReader(b)
	}
	req := httptest.NewRequest(method, path, rd)
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)
	return w
}

func decode(t *testing.T, w *httptest.ResponseRecorder) map[string]any {
	t.Helper()
	var out map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &out); err != nil {
		t.Fatalf("%d %q: %v", w.Code, w.Body.String(), err)
	}
	return out
}

// ── the contract ────────────────────────────────────────────────────

func TestTheScriptsAreTheContracts(t *testing.T) {
	for _, name := range []string{"beat.lua", "list.lua", "claim.lua", "release.lua", "dismiss.lua", "take_inbox.lua"} {
		mine, err := luaFiles.ReadFile("lua/" + name)
		if err != nil {
			t.Fatal(err)
		}
		theirs, err := os.ReadFile(filepath.Join("..", "..", "..", "contracts", "live", name))
		if err != nil {
			t.Fatal(err)
		}
		if !bytes.Equal(mine, theirs) {
			t.Errorf("internal/live/lua/%s differs from contracts/live/%s: copy the contract", name, name)
		}
	}
}

// ── what the widget sends ───────────────────────────────────────────

func TestABeatListsTheVisitorWithOnlyWhatThePanelMayShow(t *testing.T) {
	s, c, _ := setup(t)
	h := s.Handler()
	beat := map[string]any{
		"page": "/flights/new-york-istanbul?email=dana@example.com#top", "from": "JFK", "to": "ist",
		"depart": "2026-12-14", "return": "14 Dec", "cabin": "business", "pax": 2, "source": "kayak",
		"device": "phone", "site": "bbc", "chat_open": false, "has_chat": false, "name": "Dana Example",
	}
	w := do(t, h, "POST", "/v1/beat", map[string]string{"X-Live-Pass": visitorPass(c, vid1)}, beat)
	if w.Code != http.StatusOK {
		t.Fatalf("beat: %d %s", w.Code, w.Body.String())
	}
	c.add(30)
	w = do(t, h, "GET", "/v1/live", map[string]string{"Authorization": "Bearer " + agentToken(c, agentA, "sales", "sales")}, nil)
	if w.Code != http.StatusOK {
		t.Fatalf("list: %d %s", w.Code, w.Body.String())
	}
	body := w.Body.String()
	for _, leak := range []string{"dana@example.com", "Dana Example", "#top", "14 Dec", `"to":"ist"`, "email"} {
		if bytes.Contains([]byte(body), []byte(leak)) {
			t.Errorf("the list carries %q: %s", leak, body)
		}
	}
	out := decode(t, w)
	data := out["data"].([]any)
	if len(data) != 1 {
		t.Fatalf("listed %d", len(data))
	}
	v := data[0].(map[string]any)
	trip := v["trip"].(map[string]any)
	if v["page"] != "/flights/new-york-istanbul" || trip["from"] != "JFK" || trip["to"] != nil ||
		trip["depart"] != "2026-12-14" || trip["pax"] != float64(2) || v["source"] != "kayak" ||
		v["seconds_on_site"] != float64(30) || v["claimed_by"] != nil {
		t.Errorf("listed %v", v)
	}
}

func TestAVisitorGoneQuietLeavesTheList(t *testing.T) {
	s, c, _ := setup(t)
	if err := s.Store.Beat(context.Background(), Visit{ID: vid1, Page: "/", Source: "site"}); err != nil {
		t.Fatal(err)
	}
	c.add(Window - 1)
	if l, err := s.Store.Live(context.Background()); err != nil || len(l) != 1 {
		t.Fatalf("still on the site: %v %v", l, err)
	}
	c.add(2)
	if l, err := s.Store.Live(context.Background()); err != nil || len(l) != 0 {
		t.Fatalf("gone quiet: %v %v", l, err)
	}
}

func TestBeatsTooCloseCountOnce(t *testing.T) {
	s, c, _ := setup(t)
	h := s.Handler()
	hdr := map[string]string{"X-Live-Pass": visitorPass(c, vid1)}
	if w := do(t, h, "POST", "/v1/beat", hdr, map[string]any{"page": "/"}); w.Code != http.StatusOK {
		t.Fatal(w.Code)
	}
	if w := do(t, h, "POST", "/v1/beat", hdr, map[string]any{"page": "/"}); w.Code != http.StatusAccepted || decode(t, w)["counted"] != false {
		t.Fatalf("a beat a second later: %d %s", w.Code, w.Body.String())
	}
	c.add(6)
	if w := do(t, h, "POST", "/v1/beat", hdr, map[string]any{"page": "/"}); w.Code != http.StatusOK {
		t.Fatalf("after the gap: %d", w.Code)
	}
}

func TestTheWidgetRoutesWantAPass(t *testing.T) {
	s, c, _ := setup(t)
	h := s.Handler()
	n := c.now().Unix()
	expired := token.IssuePassForTests(vid1, n-90000, n-1, secret)
	forged := token.IssuePassForTests(vid1, n, n+60, []byte("not-the-secret"))
	oddID := token.IssuePassForTests("live:claims:x", n, n+60, secret)
	for _, tok := range []string{"", expired, forged, oddID, agentToken(c, agentA, "sales", "sales")} {
		for _, route := range []struct{ method, path string }{{"POST", "/v1/beat"}, {"GET", "/v1/inbox"}, {"POST", "/v1/dismiss"}} {
			w := do(t, h, route.method, route.path, map[string]string{"X-Live-Pass": tok}, map[string]any{"page": "/"})
			if w.Code != http.StatusUnauthorized {
				t.Errorf("%s %s with %q: %d", route.method, route.path, tok, w.Code)
			}
		}
	}
	// The chat's own visitor token is not a pass, under either header.
	v1 := "v1." + vid1 + ".1763699900.1763786300.0531d430f604fa6d443d52c7023ad87e985aa10be420abb4c0d3e42ca828d15c"
	for _, hdr := range []string{"X-Live-Pass", "X-Visitor-Token"} {
		if w := do(t, h, "POST", "/v1/beat", map[string]string{hdr: v1}, map[string]any{}); w.Code != http.StatusUnauthorized {
			t.Errorf("a chat token under %s: %d", hdr, w.Code)
		}
	}
	s.Secret = nil
	if w := do(t, h, "POST", "/v1/beat", map[string]string{"X-Live-Pass": visitorPass(c, vid1)}, map[string]any{}); w.Code != http.StatusServiceUnavailable {
		t.Errorf("no secret: %d", w.Code)
	}
}

// ── what the panel asks ─────────────────────────────────────────────

func TestOnlyPanelRolesSeeTheListAndOnlySalesHoldsAVisitor(t *testing.T) {
	s, c, _ := setup(t)
	h := s.Handler()
	if err := s.Store.Beat(context.Background(), Visit{ID: vid1, Page: "/", Source: "site"}); err != nil {
		t.Fatal(err)
	}
	cases := []struct {
		role, tunnel string
		list, claim  int
	}{
		{"sales", "sales", 200, 200}, {"sales", "all", 200, 200}, {"owner", "all", 200, 403},
		{"supervisor", "sales", 200, 403}, {"support", "support", 403, 403}, {"sales", "support", 200, 403},
		{"qa", "all", 403, 403},
	}
	for i, k := range cases {
		user := "aaaaaaaa-0000-4000-8000-00000000010" + strconv.Itoa(i)
		auth := map[string]string{"Authorization": "Bearer " + agentToken(c, user, k.role, k.tunnel)}
		if w := do(t, h, "GET", "/v1/live", auth, nil); w.Code != k.list {
			t.Errorf("%s/%s list: %d", k.role, k.tunnel, w.Code)
		}
		w := do(t, h, "POST", "/v1/claim", auth, map[string]string{"visitor_id": vid1})
		if w.Code != k.claim && !(k.claim == 200 && w.Code == http.StatusConflict) {
			t.Errorf("%s/%s claim: %d %s", k.role, k.tunnel, w.Code, w.Body.String())
		}
	}
	if w := do(t, h, "GET", "/v1/live", map[string]string{"Authorization": "Bearer nope"}, nil); w.Code != http.StatusUnauthorized {
		t.Errorf("a bad token: %d", w.Code)
	}
}

func TestOneVisitorOneAgentEvenAtOnce(t *testing.T) {
	s, _, _ := setup(t)
	ctx := context.Background()
	if err := s.Store.Beat(ctx, Visit{ID: vid1, Page: "/", Source: "site"}); err != nil {
		t.Fatal(err)
	}
	var wg sync.WaitGroup
	results := make(chan ClaimResult, 20)
	for i := 0; i < 20; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			r, err := s.Store.Claim(ctx, "agent-"+strconv.Itoa(i), vid1)
			if err != nil {
				t.Error(err)
			}
			results <- r
		}(i)
	}
	wg.Wait()
	close(results)
	won := 0
	for r := range results {
		switch r {
		case Claimed:
			won++
		case Taken:
		default:
			t.Errorf("unexpected %s", r)
		}
	}
	if won != 1 {
		t.Fatalf("%d agents hold the visitor", won)
	}
}

func TestAnAgentHoldsThreeAtMost(t *testing.T) {
	s, _, rc := setup(t)
	ctx := context.Background()
	vids := []string{"visitor-0001", "visitor-0002", "visitor-0003", "visitor-0004"}
	for _, v := range vids {
		if err := s.Store.Beat(ctx, Visit{ID: v, Page: "/", Source: "site"}); err != nil {
			t.Fatal(err)
		}
	}
	for i, v := range vids {
		r, err := s.Store.Claim(ctx, agentA, v)
		want := Claimed
		if i == 3 {
			want = Full
		}
		if err != nil || r != want {
			t.Fatalf("claim %d: %s %v, want %s", i, r, err, want)
		}
	}
	if r, _ := s.Store.Claim(ctx, agentA, vids[0]); r != Mine {
		t.Fatalf("again: %s", r)
	}
	if ok, err := s.Store.Release(ctx, agentB, vids[0]); err != nil || ok {
		t.Fatalf("a colleague can't release it: %v %v", ok, err)
	}
	if ok, err := s.Store.Release(ctx, agentA, vids[0]); err != nil || !ok {
		t.Fatalf("release: %v %v", ok, err)
	}
	if r, _ := s.Store.Claim(ctx, agentA, vids[3]); r != Claimed {
		t.Fatalf("after a release: %s", r)
	}
	// A claim that ran out frees its place too.
	if _, err := rc.Do(ctx, "DEL", claimKey(vids[1])); err != nil {
		t.Fatal(err)
	}
	if r, _ := s.Store.Claim(ctx, agentA, vids[0]); r != Claimed {
		t.Fatalf("after one ran out: %s", r)
	}
}

func TestAVisitorWhoLeftSaidNotNowOrChatsIsNotHeld(t *testing.T) {
	s, _, _ := setup(t)
	ctx := context.Background()
	if r, _ := s.Store.Claim(ctx, agentA, vid1); r != Gone {
		t.Fatalf("never beat: %s", r)
	}
	if err := s.Store.Beat(ctx, Visit{ID: vid1, Page: "/", Source: "site", HasChat: true}); err != nil {
		t.Fatal(err)
	}
	if r, _ := s.Store.Claim(ctx, agentA, vid1); r != InAChat {
		t.Fatalf("in a chat: %s", r)
	}
	if err := s.Store.Beat(ctx, Visit{ID: vid2, Page: "/", Source: "site"}); err != nil {
		t.Fatal(err)
	}
	if r, _ := s.Store.Claim(ctx, agentA, vid2); r != Claimed {
		t.Fatal(r)
	}
	if holder, err := s.Store.Dismiss(ctx, vid2); err != nil || holder != agentA {
		t.Fatalf("dismiss: %q %v", holder, err)
	}
	if r, _ := s.Store.Claim(ctx, agentB, vid2); r != Declined {
		t.Fatalf("after not now: %s", r)
	}
	l, err := s.Store.Live(ctx)
	if err != nil {
		t.Fatal(err)
	}
	for _, v := range l {
		if v.ID == vid2 && (!v.Dismissed || v.ClaimedBy != "") {
			t.Errorf("listed after not now: %+v", v)
		}
	}
}

// ── the hello reaching the widget ────────────────────────────────────

// send writes a hello the way the API does (contracts/live/send.lua), from the contract itself.
func send(t *testing.T, rc *resp.Client, agent, vid, msg string) int64 {
	t.Helper()
	src, err := os.ReadFile(filepath.Join("..", "..", "..", "contracts", "live", "send.lua"))
	if err != nil {
		t.Fatal(err)
	}
	n, err := resp.Int(rc.Do(context.Background(), "EVAL", string(src), "5", claimKey(vid), dismissedKey(vid),
		inboxKey(vid), "live:hello:h1", "live:said:"+vid, agent, vid, msg, `{"hello":1}`, "900"))
	if err != nil {
		t.Fatal(err)
	}
	return n
}

func example(t *testing.T) string {
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "contracts", "live_inbox_message.json"))
	if err != nil {
		t.Fatal(err)
	}
	var compact bytes.Buffer
	if err := json.Compact(&compact, raw); err != nil {
		t.Fatal(err)
	}
	return compact.String()
}

// pass.lua is the API's (POST /chat/live: may this visitor id have a pass?); it runs here so its Lua meets a
// real Redis too.
func TestThePassScriptHoldsTheRules(t *testing.T) {
	_, _, rc := setup(t)
	ctx := context.Background()
	src, err := os.ReadFile(filepath.Join("..", "..", "..", "contracts", "live", "pass.lua"))
	if err != nil {
		t.Fatal(err)
	}
	ask := func(vid, address string, proven bool, most int) int64 {
		t.Helper()
		shown := "0"
		if proven {
			shown = "1"
		}
		v, err := rc.Do(ctx, "EVAL", string(src), "2", "live:passed:"+vid, "rl:livepass:"+address+":h", shown, "86400", strconv.Itoa(most))
		if err != nil {
			t.Fatal(err)
		}
		return v.Int
	}
	third := "e2e00000-0000-4000-8000-000000000003"
	if n := ask(vid1, "a1", false, 2); n != 1 {
		t.Fatalf("a new id: %d", n)
	}
	if n := ask(vid1, "a1", false, 2); n != -1 {
		t.Fatalf("the id again, no pass shown: %d", n)
	}
	if n := ask(vid1, "a2", true, 2); n != 1 {
		t.Fatalf("its own pass shown: %d", n)
	}
	if ttl, _ := rc.Do(ctx, "TTL", "live:passed:"+vid1); ttl.Int < 86000 {
		t.Fatalf("the id stays bound a day: %d", ttl.Int)
	}
	if n := ask(vid2, "a1", false, 2); n != 1 {
		t.Fatalf("a second new id from one address: %d", n)
	}
	if n := ask(third, "a1", false, 2); n != -2 {
		t.Fatalf("a third: %d", n)
	}
	if ex, _ := rc.Do(ctx, "EXISTS", "live:passed:"+third); ex.Int != 0 {
		t.Fatal("a refused id is not bound")
	}
	if n := ask(vid1, "a1", true, 2); n != 1 {
		t.Fatalf("a renewal is no new id: %d", n)
	}
	if ttl, _ := rc.Do(ctx, "TTL", "rl:livepass:a1:h"); ttl.Int <= 0 || ttl.Int > 3600 {
		t.Fatalf("the count lives an hour: %d", ttl.Int)
	}
}

func TestTheSendScriptHoldsTheRules(t *testing.T) {
	s, _, rc := setup(t)
	ctx := context.Background()
	if err := s.Store.Beat(ctx, Visit{ID: vid1, Page: "/", Source: "site"}); err != nil {
		t.Fatal(err)
	}
	if n := send(t, rc, agentA, vid1, example(t)); n != -1 {
		t.Fatalf("not held: %d", n)
	}
	if r, _ := s.Store.Claim(ctx, agentA, vid1); r != Claimed {
		t.Fatal(r)
	}
	if n := send(t, rc, agentB, vid1, example(t)); n != -1 {
		t.Fatalf("a colleague: %d", n)
	}
	if n := send(t, rc, agentA, vid1, example(t)); n != 1 {
		t.Fatalf("send: %d", n)
	}
	if n := send(t, rc, agentA, vid1, example(t)); n != -3 {
		t.Fatalf("a second hello: %d", n)
	}
	if hello, err := resp.Str(rc.Do(ctx, "GET", "live:hello:h1")); err != nil || hello != `{"hello":1}` {
		t.Fatalf("the hello for /chat/init: %q %v", hello, err)
	}
	src, err := os.ReadFile(filepath.Join("..", "..", "..", "contracts", "live", "take_hello.lua"))
	if err != nil {
		t.Fatal(err)
	}
	first, err := resp.Str(rc.Do(ctx, "EVAL", string(src), "1", "live:hello:h1"))
	if err != nil || first != `{"hello":1}` {
		t.Fatalf("take: %q %v", first, err)
	}
	if _, err := resp.Str(rc.Do(ctx, "EVAL", string(src), "1", "live:hello:h1")); err != resp.ErrNil {
		t.Fatalf("a hello answers one tap: %v", err)
	}
}

func poll(t *testing.T, base string, tok string) (int, string) {
	t.Helper()
	req, err := http.NewRequest("GET", base+"/v1/inbox", nil)
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("X-Live-Pass", tok)
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = res.Body.Close() }()
	b, _ := io.ReadAll(res.Body)
	return res.StatusCode, string(b)
}

func TestAHelloReachesTheWaitingWidgetAtOnceAndOnce(t *testing.T) {
	s, c, rc := setup(t)
	s.Recheck = time.Hour // only pub/sub can wake it
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	subscribers := func() int64 {
		v, _ := rc.Do(context.Background(), "PUBSUB", "NUMSUB", InboxChannel)
		if len(v.Array) != 2 {
			return -1
		}
		return v.Array[1].Int
	}
	before := subscribers() // a Redis shared with a running bbc-live has its subscriber already
	go s.Hub.Listen(ctx, rc, s.Log)
	ts := httptest.NewServer(s.Handler())
	defer ts.Close()
	if err := s.Store.Beat(context.Background(), Visit{ID: vid1, Page: "/", Source: "site"}); err != nil {
		t.Fatal(err)
	}
	if r, _ := s.Store.Claim(context.Background(), agentA, vid1); r != Claimed {
		t.Fatal(r)
	}
	tok := visitorPass(c, vid1)
	type got struct {
		code int
		body string
		at   time.Time
	}
	done := make(chan got, 1)
	go func() {
		code, body := poll(t, ts.URL, tok)
		done <- got{code, body, time.Now()}
	}()
	for deadline := time.Now().Add(2 * time.Second); s.Hub.Waiting() == 0; {
		if time.Now().After(deadline) {
			t.Fatal("the poll never waited")
		}
		time.Sleep(10 * time.Millisecond)
	}
	for deadline := time.Now().Add(2 * time.Second); ; {
		if before >= 0 && subscribers() == before+1 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("the hub never subscribed")
		}
		time.Sleep(10 * time.Millisecond)
	}
	sentAt := time.Now()
	if n := send(t, rc, agentA, vid1, example(t)); n != 1 {
		t.Fatal(n)
	}
	r := <-done
	if r.code != http.StatusOK || r.at.Sub(sentAt) > 500*time.Millisecond {
		t.Fatalf("got %d after %s: %s", r.code, r.at.Sub(sentAt), r.body)
	}
	var out struct {
		Messages []map[string]any `json:"messages"`
	}
	if err := json.Unmarshal([]byte(r.body), &out); err != nil || len(out.Messages) != 1 || out.Messages[0]["agent"] != "Ana" ||
		out.Messages[0]["kind"] != "hello" {
		t.Fatalf("delivered %s (%v)", r.body, err)
	}
	if code, _ := poll(t, ts.URL, tok); code != http.StatusNoContent {
		t.Fatalf("delivered twice: %d", code)
	}
}

func TestWithoutPubSubTheRecheckStillDelivers(t *testing.T) {
	s, c, rc := setup(t)
	ts := httptest.NewServer(s.Handler())
	defer ts.Close()
	tok := visitorPass(c, vid1)
	go func() {
		time.Sleep(200 * time.Millisecond)
		if _, err := rc.Do(context.Background(), "RPUSH", inboxKey(vid1), example(t)); err != nil {
			t.Error(err)
		}
	}()
	start := time.Now()
	code, body := poll(t, ts.URL, tok)
	if code != http.StatusOK || time.Since(start) > time.Second {
		t.Fatalf("%d after %s: %s", code, time.Since(start), body)
	}
}

func TestNothingForTheVisitorIsANoContentAfterTheWait(t *testing.T) {
	s, c, _ := setup(t)
	s.PollWait = 300 * time.Millisecond
	ts := httptest.NewServer(s.Handler())
	defer ts.Close()
	start := time.Now()
	if code, _ := poll(t, ts.URL, visitorPass(c, vid1)); code != http.StatusNoContent || time.Since(start) < 250*time.Millisecond {
		t.Fatalf("%d after %s", code, time.Since(start))
	}
}

func TestAMessageThatIsNotJSONNeverReachesTheWidget(t *testing.T) {
	s, c, rc := setup(t)
	s.PollWait = 300 * time.Millisecond
	ts := httptest.NewServer(s.Handler())
	defer ts.Close()
	if _, err := rc.Do(context.Background(), "RPUSH", inboxKey(vid1), "<script>alert(1)</script>"); err != nil {
		t.Fatal(err)
	}
	if code, body := poll(t, ts.URL, visitorPass(c, vid1)); code != http.StatusNoContent {
		t.Fatalf("%d %s", code, body)
	}
}

func TestTooManyWaitsForOneVisitorAreRefused(t *testing.T) {
	h := NewHub()
	var dones []func()
	for i := 0; i < MaxWaitersPerVisitor; i++ {
		_, done, ok := h.Wait(vid1)
		if !ok {
			t.Fatal("refused too early")
		}
		dones = append(dones, done)
	}
	if _, _, ok := h.Wait(vid1); ok {
		t.Fatal("a fourth tab")
	}
	if _, done, ok := h.Wait(vid2); !ok {
		t.Fatal("another visitor")
	} else {
		done()
	}
	for _, d := range dones {
		d()
		d() // twice is harmless
	}
	if h.Waiting() != 0 {
		t.Fatalf("waiting %d", h.Waiting())
	}
}

func TestAHeadDoesNotTakeTheHello(t *testing.T) {
	s, c, rc := setup(t)
	if err := s.Store.Beat(context.Background(), Visit{ID: vid1, Page: "/", Source: "site"}); err != nil {
		t.Fatal(err)
	}
	if r, _ := s.Store.Claim(context.Background(), agentA, vid1); r != Claimed {
		t.Fatal(r)
	}
	if n := send(t, rc, agentA, vid1, example(t)); n != 1 {
		t.Fatal(n)
	}
	h := s.Handler()
	hdr := map[string]string{"X-Live-Pass": visitorPass(c, vid1)}
	if w := do(t, h, "HEAD", "/v1/inbox", hdr, nil); w.Code != http.StatusMethodNotAllowed {
		t.Fatalf("HEAD: %d", w.Code)
	}
	if w := do(t, h, "GET", "/v1/inbox", hdr, nil); w.Code != http.StatusOK || !strings.Contains(w.Body.String(), `"kind":"hello"`) {
		t.Fatalf("the hello is still there for the GET: %d %s", w.Code, w.Body.String())
	}
}

func TestOfTwoBeatsAtOnceOneCounts(t *testing.T) {
	s, c, _ := setup(t)
	h := s.Handler()
	hdr := map[string]string{"X-Live-Pass": visitorPass(c, vid1)}
	var counted, tooSoon atomic.Int64
	var wg sync.WaitGroup
	for i := 0; i < 20; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			switch w := do(t, h, "POST", "/v1/beat", hdr, map[string]any{"page": "/"}); w.Code {
			case http.StatusOK:
				counted.Add(1)
			case http.StatusAccepted:
				tooSoon.Add(1)
			}
		}()
	}
	wg.Wait()
	if counted.Load() != 1 || tooSoon.Load() != 19 {
		t.Fatalf("counted %d, too soon %d", counted.Load(), tooSoon.Load())
	}
	c.add(6)
	if w := do(t, h, "POST", "/v1/beat", hdr, map[string]any{"page": "/"}); w.Code != http.StatusOK {
		t.Fatalf("after the gap: %d", w.Code)
	}
}

func TestCORSOnlyForTheListedOrigins(t *testing.T) {
	s, _, _ := setup(t)
	h := s.Handler()
	w := do(t, h, "OPTIONS", "/v1/beat", map[string]string{"Origin": "https://www.example-site.test",
		"Access-Control-Request-Method": "POST"}, nil)
	if w.Code != http.StatusNoContent || w.Header().Get("Access-Control-Allow-Origin") != "https://www.example-site.test" {
		t.Fatalf("preflight: %d %v", w.Code, w.Header())
	}
	allowed := map[string]bool{}
	for _, h := range strings.Split(w.Header().Get("Access-Control-Allow-Headers"), ",") {
		allowed[strings.ToLower(strings.TrimSpace(h))] = true
	}
	if !allowed["x-live-pass"] || !allowed["authorization"] || !allowed["content-type"] || allowed["x-visitor-token"] {
		t.Fatalf("the headers a browser may send: %q", w.Header().Get("Access-Control-Allow-Headers"))
	}
	w = do(t, h, "OPTIONS", "/v1/beat", map[string]string{"Origin": "https://evil.test"}, nil)
	if w.Header().Get("Access-Control-Allow-Origin") != "" {
		t.Fatal("an unlisted origin got CORS")
	}
}

func TestHealthSaysWhatItKnows(t *testing.T) {
	s, _, _ := setup(t)
	w := do(t, s.Handler(), "GET", "/health", nil, nil)
	out := decode(t, w)
	if w.Code != http.StatusOK || out["redis"] != true || out["configured"].(map[string]any)["live_token"] != true {
		t.Fatalf("%d %v", w.Code, out)
	}
}
