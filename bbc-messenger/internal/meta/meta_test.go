package meta

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"net/http/httptrace"
	"strings"
	"sync/atomic"
	"testing"
	"time"
	"unicode/utf8"
)

func sign(secret, body string) string {
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(body))
	return "sha256=" + hex.EncodeToString(mac.Sum(nil))
}

func TestSignature(t *testing.T) {
	body := `{"object":"page"}`
	good := sign("app-secret", body)
	cases := []struct {
		name, header, secret string
		want                 bool
	}{
		{"right", good, "app-secret", true},
		{"upper-case hex", "sha256=" + strings.ToUpper(strings.TrimPrefix(good, "sha256=")), "app-secret", true},
		{"other secret", good, "another-secret", false},
		{"no prefix", strings.TrimPrefix(good, "sha256="), "app-secret", false},
		{"sha1", "sha1=" + strings.TrimPrefix(good, "sha256="), "app-secret", false},
		{"short", good[:20], "app-secret", false},
		{"not hex", "sha256=" + strings.Repeat("z", 64), "app-secret", false},
		{"no secret", good, "", false},
		{"none", "", "app-secret", false},
	}
	for _, c := range cases {
		if got := ValidSignature([]byte(body), c.header, []byte(c.secret)); got != c.want {
			t.Errorf("%s: got %v", c.name, got)
		}
	}
	// One byte changed in the body: refused.
	if ValidSignature([]byte(body+" "), good, []byte("app-secret")) {
		t.Error("a changed body passed")
	}
}

const payload = `{"object":"page","entry":[{"id":"PAGE1","time":1,"messaging":[
 {"sender":{"id":"U1"},"recipient":{"id":"PAGE1"},"timestamp":300,"message":{"mid":"m3","text":"Two of us","quick_reply":{"payload":"yes"}}},
 {"sender":{"id":"U1"},"recipient":{"id":"PAGE1"},"timestamp":100,"message":{"mid":"m1","text":"Hi","attachments":[{"type":"image"}]},"referral":{"ref":"kayak-nyc","source":"ADS","type":"OPEN_THREAD","ad_id":"ad9"}},
 {"sender":{"id":"PAGE1"},"recipient":{"id":"U1"},"timestamp":200,"message":{"mid":"m2","text":"Skye here","is_echo":true,"app_id":123456789012345}},
 {"sender":{"id":"PAGE1"},"recipient":{"id":"U2"},"timestamp":250,"message":{"mid":"m4","text":"Ana from the team","is_echo":true,"app_id":263902037430900}},
 {"sender":{"id":"U3"},"recipient":{"id":"PAGE1"},"timestamp":50,"postback":{"mid":"p1","title":"Get Started","payload":"GET_STARTED","referral":{"ref":"site-footer","source":"SHORTLINK"}}},
 {"sender":{"id":"U4"},"recipient":{"id":"PAGE1"},"timestamp":60,"referral":{"ref":"promo","source":"SHORTLINK","type":"OPEN_THREAD"}},
 {"sender":{"id":"U1"},"recipient":{"id":"PAGE1"},"timestamp":400,"read":{"watermark":399}},
 {"sender":{"id":"U1"},"recipient":{"id":"PAGE1"},"timestamp":401,"delivery":{"mids":["m3"]}}
],"standby":[
 {"sender":{"id":"U5"},"recipient":{"id":"PAGE1"},"timestamp":70,"message":{"mid":"s1","text":"Hello?"}}
]}]}`

func TestEvents(t *testing.T) {
	var p Payload
	if err := json.Unmarshal([]byte(payload), &p); err != nil {
		t.Fatal(err)
	}
	got, skipped := Events(p, "123456789012345")
	if skipped.OwnEchoes != 1 || skipped.Other != 2 {
		t.Fatalf("skipped %+v", skipped)
	}
	var keys []string
	for _, in := range got {
		keys = append(keys, in.Kind+":"+in.Key())
	}
	want := "postback:p1 referral:referral:PAGE1:U4:60 message:s1 message:m1 echo:m4 message:m3"
	if strings.Join(keys, " ") != want {
		t.Fatalf("oldest first, own echo and read/delivery left out:\n got %s\nwant %s", strings.Join(keys, " "), want)
	}
	byKey := map[string]Inbound{}
	for _, in := range got {
		byKey[in.Key()] = in
	}
	if m := byKey["m1"]; m.PSID != "U1" || m.PageID != "PAGE1" || m.Text != "Hi" || m.Referral == nil ||
		m.Referral.AdID != "ad9" || len(m.Attachments) != 1 || m.Attachments[0] != "image" || !m.Answered() {
		t.Errorf("m1: %+v", m)
	}
	if m := byKey["m3"]; m.QuickReply != "yes" {
		t.Errorf("the quick reply's payload is kept: %+v", m)
	}
	// An echo from the Page's inbox: the person is the recipient, and it is kept, never answered.
	if m := byKey["m4"]; m.PSID != "U2" || m.Text != "Ana from the team" || m.Answered() {
		t.Errorf("m4: %+v", m)
	}
	if m := byKey["p1"]; m.Postback != "GET_STARTED" || m.PostbackTitle != "Get Started" || m.Referral.Ref != "site-footer" || !m.Answered() {
		t.Errorf("p1: %+v", m)
	}
	if m := byKey["referral:PAGE1:U4:60"]; m.Referral.Ref != "promo" || m.Answered() {
		t.Errorf("a referral alone is kept, not answered: %+v", m)
	}
	if m := byKey["s1"]; !m.Standby || m.Answered() {
		t.Errorf("heard in standby: kept, never answered: %+v", m)
	}
	// Rule 5: without our app id, no echo is taken for our own.
	got, skipped = Events(p, "")
	if skipped.OwnEchoes != 0 || len(got) != 7 {
		t.Errorf("no app id: %d events, %+v", len(got), skipped)
	}
}

func TestErrorKinds(t *testing.T) {
	cases := []struct {
		e     Error
		kind  string
		retry bool
	}{
		{Error{Status: 400, Code: 10, Subcode: 2018278}, "window_closed", false},
		{Error{Status: 400, Code: 10, Subcode: 2534022}, "window_closed", false},
		{Error{Status: 400, Code: 551, Subcode: 1545041}, "unavailable", false},
		{Error{Status: 400, Code: 200, Subcode: 1545041}, "unavailable", false},
		{Error{Status: 400, Code: 10, Subcode: 2018108}, "unavailable", false},
		{Error{Status: 400, Code: 10, Subcode: 2018300}, "thread_owned", false},
		{Error{Status: 400, Code: 100, Subcode: 2534037}, "thread_owned", false},
		{Error{Status: 400, Code: 190}, "token", false},
		{Error{Status: 400, Code: 613}, "rate", true},
		{Error{Status: 400, Code: 4}, "rate", true},
		// Meta says it did not send it, for now: tried again.
		{Error{Status: 500, Code: 2}, "temporary", true},
		{Error{Status: 400, Code: 2}, "temporary", true},
		{Error{Status: 500, Code: 1200}, "temporary", true},
		{Error{Status: 503, Code: 2}, "temporary", true},
		// A 5xx that does not say (RFC 9110: a server or a proxy failed once it may have had the call; a proxy
		// answers 503 when the server it passed the call to went away): maybe sent, never sent again.
		{Error{Status: 500}, "unknown", false},
		{Error{Status: 500, Code: 1}, "unknown", false},
		{Error{Status: 503}, "unknown", false},
		{Error{Status: 503, Code: 1}, "unknown", false},
		{Error{Status: 502}, "unknown", false},
		{Error{Status: 504}, "unknown", false},
		{Error{Status: 400, Code: 100}, "other", false},
		// HTTP 429 with no Graph code (an empty body): a rate refusal all the same; with a code, the code says.
		{Error{Status: 429}, "rate", true},
		{Error{Status: 429, Code: 100}, "other", false},
		// Rule 5: a structured refusal keeps its own kind whatever the status.
		{Error{Status: 429, Code: 10, Subcode: 2018278}, "window_closed", false},
	}
	for _, c := range cases {
		if c.e.Kind() != c.kind || c.e.Retry() != c.retry {
			t.Errorf("%+v: %s %v", c.e, c.e.Kind(), c.e.Retry())
		}
	}
}

func client(url string) *Client {
	return &Client{HTTP: &http.Client{Timeout: 2 * time.Second}, Base: url, Version: "v26.0"}
}

func TestASendWhoseAnswerNeverCameIsNeverSentAgain(t *testing.T) {
	var calls atomic.Int32
	// Meta has the whole request, then the connection drops before its answer: it may be with the person.
	dropped := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		_, _ = io.ReadAll(r.Body)
		conn, _, err := w.(http.Hijacker).Hijack()
		if err == nil {
			_ = conn.Close()
		}
	}))
	defer dropped.Close()
	_, err := client(dropped.URL).Send(context.Background(), "tok", "PSID-1", Reply{Text: "Hi"}, "")
	var ue *Unconfirmed
	if !errors.As(err, &ue) || KindOf(err) != "unknown" || Retryable(err) {
		t.Errorf("unconfirmed, never tried again: %v %s %v", err, KindOf(err), Retryable(err))
	}
	if calls.Load() != 1 {
		t.Errorf("sent %d times", calls.Load())
	}

	// Rule 5: nothing reached Meta (no one listens there): a network failure, tried again.
	gone := httptest.NewServer(http.NotFoundHandler())
	url := gone.URL
	gone.Close()
	_, err = client(url).Send(context.Background(), "tok", "PSID-1", Reply{Text: "Hi"}, "")
	if err == nil || errors.As(err, &ue) || KindOf(err) != "temporary" || !Retryable(err) {
		t.Errorf("before the request went out: %v %s %v", err, KindOf(err), Retryable(err))
	}

	// Meta answered 200 with a body that is not JSON: sent, nothing to send again.
	odd := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.WriteString(w, "not json")
	}))
	defer odd.Close()
	if _, err := client(odd.URL).Send(context.Background(), "tok", "PSID-1", Reply{Text: "Hi"}, ""); err != nil {
		t.Errorf("a 200 is a message sent: %v", err)
	}

	// A 429 with no body: a rate refusal, tried again.
	busy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusTooManyRequests)
	}))
	defer busy.Close()
	_, err = client(busy.URL).Send(context.Background(), "tok", "PSID-1", Reply{Text: "Hi"}, "")
	if KindOf(err) != "rate" || !Retryable(err) {
		t.Errorf("429: %v %s %v", err, KindOf(err), Retryable(err))
	}

	// A proxy's 504: Meta may have sent it, so it is never sent again.
	var proxied atomic.Int32
	timeout := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		proxied.Add(1)
		http.Error(w, "upstream timed out", http.StatusGatewayTimeout)
	}))
	defer timeout.Close()
	_, err = client(timeout.URL).Send(context.Background(), "tok", "PSID-1", Reply{Text: "Hi"}, "")
	if KindOf(err) != "unknown" || Retryable(err) || proxied.Load() != 1 {
		t.Errorf("504: %v %s %v", err, KindOf(err), Retryable(err))
	}

	// Meta had the whole call and the wait for its answer ran out (the client's own timeout): unconfirmed.
	release := make(chan struct{})
	slow := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.ReadAll(r.Body)
		<-release
	}))
	defer slow.Close()
	defer close(release)
	short := &Client{HTTP: &http.Client{Timeout: 50 * time.Millisecond}, Base: slow.URL, Version: "v26.0"}
	_, err = short.Send(context.Background(), "tok", "PSID-1", Reply{Text: "Hi"}, "")
	if !errors.As(err, &ue) || KindOf(err) != "unknown" || Retryable(err) {
		t.Errorf("no answer in time: %v %s %v", err, KindOf(err), Retryable(err))
	}
}

// tracedTransport tells a call's tries as the real transport may, in the order given (each try a connection had,
// then its write's end, failed or whole, or not told yet; a try's end told after the next one began), then gives
// back err: Do comes back on a deadline or a lost connection, whatever was told by then.
type tracedTransport struct {
	told []string // "conn", "wrote", "write failed"
	err  error
}

func (tt *tracedTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	trace := httptrace.ContextClientTrace(r.Context())
	for _, step := range tt.told {
		switch step {
		case "conn":
			trace.GotConn(httptrace.GotConnInfo{})
		case "wrote":
			trace.WroteRequest(httptrace.WroteRequestInfo{})
		case "write failed":
			trace.WroteRequest(httptrace.WroteRequestInfo{Err: io.ErrUnexpectedEOF})
		}
	}
	return nil, tt.err
}

func TestACallIsSentAgainOnlyWhenNoneOfItsTriesCanBeWithMeta(t *testing.T) {
	cases := []struct {
		name        string
		told        []string
		err         error
		unconfirmed bool
	}{
		{"a connection, then the deadline, no word of the write", []string{"conn"}, context.DeadlineExceeded, true},
		// The transport tried again after a write it knew failed; the second try had a connection and no word of
		// its write when the deadline came: it may be with Meta.
		{"a failed write, a second try, then the deadline", []string{"conn", "write failed", "conn"}, context.DeadlineExceeded, true},
		{"the first try's failed write told after the second began", []string{"conn", "conn", "write failed"}, context.DeadlineExceeded, true},
		{"a try that wrote, then one whose write failed", []string{"conn", "wrote", "conn", "write failed"}, io.ErrUnexpectedEOF, true},
		// Rule 5: no try can be with Meta: tried again.
		{"a write that failed", []string{"conn", "write failed"}, io.ErrUnexpectedEOF, false},
		{"every try's write failed", []string{"conn", "write failed", "conn", "write failed"}, io.ErrUnexpectedEOF, false},
		{"never a connection", nil, errors.New("dial tcp: connection refused"), false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			cl := &Client{HTTP: &http.Client{Transport: &tracedTransport{told: c.told, err: c.err}},
				Base: "https://graph.example.test", Version: "v26.0"}
			_, err := cl.Send(context.Background(), "tok", "PSID-1", Reply{Text: "Hi"}, "")
			var ue *Unconfirmed
			if errors.As(err, &ue) != c.unconfirmed || Retryable(err) == c.unconfirmed ||
				(KindOf(err) == "unknown") != c.unconfirmed {
				t.Errorf("%v: %s, tried again %v", err, KindOf(err), Retryable(err))
			}
		})
	}
}

func TestSplit(t *testing.T) {
	if got := Split("  Hi there  "); len(got) != 1 || got[0] != "Hi there" {
		t.Errorf("short: %q", got)
	}
	if got := Split("   "); len(got) != 0 {
		t.Errorf("nothing to send: %q", got)
	}
	para := strings.Repeat("a", 1500)
	got := Split(para + "\n\n" + para + "\n\n" + para)
	if len(got) != 3 || got[0] != para {
		t.Errorf("at the paragraphs: %d pieces", len(got))
	}
	// No break anywhere: cut at the limit, in characters, never in the middle of one.
	long := strings.Repeat("ș", 4500)
	got = Split(long)
	if len(got) != 3 || utf8.RuneCountInString(got[0]) != MaxText || !utf8.ValidString(got[0]) {
		t.Errorf("hard cut: %d pieces, first %d", len(got), utf8.RuneCountInString(got[0]))
	}
	total := 0
	for _, g := range got {
		total += utf8.RuneCountInString(g)
		if utf8.RuneCountInString(g) > MaxText {
			t.Errorf("a piece of %d", utf8.RuneCountInString(g))
		}
	}
	if total != 4500 {
		t.Errorf("lost characters: %d", total)
	}
	if Clip("Send me an SMS please now") != "Send me an SMS plea…" || Clip("SMS") != "SMS" {
		t.Errorf("clip: %q", Clip("Send me an SMS please now"))
	}
}
