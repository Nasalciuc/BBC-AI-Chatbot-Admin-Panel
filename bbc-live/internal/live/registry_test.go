package live

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/Nasalciuc/BBC-AI-Chatbot-Admin-Panel/bbc-live/internal/resp"
)

const chatID = "0b5e2c1a-7f3d-4e8a-9b6c-2d4f6a8c0e12"

func hash(t *testing.T, rc *resp.Client, key string) map[string]string {
	t.Helper()
	all, err := resp.Strings(rc.Do(context.Background(), "HGETALL", key))
	if err != nil {
		t.Fatal(err)
	}
	out := map[string]string{}
	for i := 0; i+1 < len(all); i += 2 {
		out[all[i]] = all[i+1]
	}
	return out
}

func snapshot(over map[string]any) map[string]any {
	v := map[string]any{
		"page_id": "pg-first-1", "page": "/flights/new-york-istanbul?email=dana@example.com#top",
		"page_type": "route", "page_s": 12, "seen_s": 10, "from": "JFK", "to": "IST", "depart": "2026-12-14",
		"cabin": "business", "pax": 2, "source": "kayak", "channel": "kayak", "utm_source": "kayak",
		"utm_medium": "cpc", "utm_campaign": "us-route-istanbul", "referrer": "WWW.KAYAK.COM", "device": "phone",
		"site": "bbc", "lang": "en-US", "tz": "America/New_York", "country": "US",
		"ph_session": "01937a2b-4c5d-7e6f-8a9b-0c1d2e3f4a5b",
		"events":     "card:kayak:card;card_yes:yes;chat_msg*3", "steps": "search:JFK-IST:2026-12-14;quote_opened",
	}
	for k, x := range over {
		v[k] = x
	}
	return v
}

func visitReq(t *testing.T, s *Server, c *clock, vid string, body map[string]any, headers map[string]string) *httptest.ResponseRecorder {
	t.Helper()
	h := map[string]string{"X-Live-Pass": visitorPass(c, vid), "X-Forwarded-For": "198.51.100.9, 203.0.113.7",
		"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X)\x07"}
	for k, v := range headers {
		h[k] = v
	}
	return do(t, s.Handler(), "POST", "/v1/visit", h, body)
}

func TestAVisitIsKeptWithItsPagesAndNeverWhatWasTyped(t *testing.T) {
	s, c, rc := setup(t)
	w := visitReq(t, s, c, vid1, snapshot(map[string]any{
		"name": "Dana Example", "email": "dana@example.com",
		// An event that carries a number or an address, a step that is a sentence: dropped, the rest kept.
		"events":          "card:kayak:card;contact:dana@example.com;phone:+14155550123;card_yes:yes",
		"steps":           "search:JFK-IST;call me at 415 555 0123;quote_opened",
		"utm_campaign":    "dana@example.com",
		"conversation_id": chatID,
		"card":            map[string]any{"trigger": "kayak", "variant": "beat", "surface": "card", "state": "shown", "s": 4},
	}), nil)
	if w.Code != http.StatusOK {
		t.Fatalf("visit: %d %s", w.Code, w.Body.String())
	}
	v := hash(t, rc, regKey(vid1))
	for _, leak := range []string{"dana@example.com", "Dana Example", "415 555", "+14155550123", "#top", "\x07"} {
		for k, x := range v {
			if strings.Contains(x, leak) {
				t.Errorf("the registry keeps %q in %s: %q", leak, k, x)
			}
		}
	}
	now := c.now().Unix()
	want := map[string]string{
		"landing": "/flights/new-york-istanbul", "landing_type": "route", "last_page": "/flights/new-york-istanbul",
		"source": "kayak", "channel": "kayak", "utm_source": "kayak", "utm_medium": "cpc", "utm_campaign": "",
		"referrer": "www.kayak.com", "device": "phone", "site": "bbc", "lang": "en-US", "tz": "America/New_York",
		"country": "US", "ip": "203.0.113.7", "ph_session": "01937a2b-4c5d-7e6f-8a9b-0c1d2e3f4a5b",
		"conversation_id": chatID, "from": "JFK", "to": "IST", "depart": "2026-12-14", "cabin": "business", "pax": "2",
		"card_trigger": "kayak", "card_variant": "beat", "card_surface": "card", "card_state": "shown",
		"pages": "pg-first-1",
	}
	for k, x := range want {
		if v[k] != x {
			t.Errorf("%s = %q, want %q", k, v[k], x)
		}
	}
	if v["ua"] != "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X)" {
		t.Errorf("ua %q", v["ua"])
	}
	if v["first_at"] == "" || v["first_at"] != v["last_at"] || v["card_at"] != itoa(now-4) {
		t.Errorf("times: first %s last %s card %s (now %d)", v["first_at"], v["last_at"], v["card_at"], now)
	}
	if _, ok := v["has_chat"]; ok {
		t.Errorf("no chat yet, and has_chat written: %q", v["has_chat"])
	}
	var page VisitPage
	if err := json.Unmarshal([]byte(v["pg:pg-first-1"]), &page); err != nil {
		t.Fatal(err)
	}
	if page.Path != "/flights/new-york-istanbul" || page.Type != "route" || page.S != 12 || page.Seen != 10 ||
		strings.Join(page.Events, ";") != "card:kayak:card;card_yes:yes" ||
		strings.Join(page.Steps, ";") != "search:JFK-IST;quote_opened" || page.At != now {
		t.Errorf("page %+v", page)
	}
	score, err := resp.Int(rc.Do(context.Background(), "ZSCORE", RegKey, vid1))
	if err != nil || score != now {
		t.Errorf("open visit listed at %d (%v), want %d", score, err, now)
	}
}

func itoa(n int64) string { b, _ := json.Marshal(n); return string(b) }

func TestWhereTheyCameFromIsTheFirstSnapshotsTheTripTheLatest(t *testing.T) {
	s, c, rc := setup(t)
	if w := visitReq(t, s, c, vid1, snapshot(nil), nil); w.Code != http.StatusOK {
		t.Fatal(w.Body.String())
	}
	c.add(40)
	second := snapshot(map[string]any{"page_id": "pg-second-2", "page": "/deals", "page_type": "deals",
		"source": "site", "channel": "direct", "device": "computer", "country": "GB", "to": "LHR", "depart": "",
		"has_chat": true, "conversation_id": chatID, "events": "", "steps": ""})
	if w := visitReq(t, s, c, vid1, second, map[string]string{"X-Forwarded-For": "192.0.2.44"}); w.Code != http.StatusOK {
		t.Fatal(w.Body.String())
	}
	c.add(20)
	// A snapshot from before the chat, landing late: the chat stays.
	if w := visitReq(t, s, c, vid1, snapshot(map[string]any{"page_id": "pg-second-2", "page": "/deals", "has_chat": false}), nil); w.Code != http.StatusOK {
		t.Fatal(w.Body.String())
	}
	v := hash(t, rc, regKey(vid1))
	if v["landing"] != "/flights/new-york-istanbul" || v["source"] != "kayak" || v["device"] != "phone" ||
		v["country"] != "US" || v["ip"] != "203.0.113.7" {
		t.Errorf("first touch replaced: %v", v)
	}
	if v["last_page"] != "/deals" || v["to"] != "IST" || v["depart"] != "2026-12-14" || v["has_chat"] != "1" ||
		v["conversation_id"] != chatID {
		t.Errorf("latest: last_page %q to %q depart %q chat %q conv %q", v["last_page"], v["to"], v["depart"],
			v["has_chat"], v["conversation_id"])
	}
	if v["pages"] != "pg-first-1,pg-second-2" || v["last_at"] != itoa(c.now().Unix()) {
		t.Errorf("pages %q last_at %q", v["pages"], v["last_at"])
	}
}

func TestWhatCameOfTheCardOnlyMovesForward(t *testing.T) {
	s, c, rc := setup(t)
	card := func(state string, sec, answerS float64, answer string) map[string]any {
		return snapshot(map[string]any{"card": map[string]any{"trigger": "ads", "variant": "", "surface": "panel",
			"state": state, "s": sec, "answer": answer, "answer_s": answerS}})
	}
	if w := visitReq(t, s, c, vid1, card("shown", 2, 0, ""), nil); w.Code != http.StatusOK {
		t.Fatal(w.Body.String())
	}
	shownAt := c.now().Unix() - 2
	c.add(10)
	if w := visitReq(t, s, c, vid1, card("engaged", 12, 3, "yes"), nil); w.Code != http.StatusOK {
		t.Fatal(w.Body.String())
	}
	answeredAt := c.now().Unix() - 3
	c.add(10)
	// Closed afterwards: the answer stays the answer.
	if w := visitReq(t, s, c, vid1, card("dismissed", 22, 1, "close"), nil); w.Code != http.StatusOK {
		t.Fatal(w.Body.String())
	}
	v := hash(t, rc, regKey(vid1))
	if v["card_state"] != "engaged" || v["card_at"] != itoa(shownAt) || v["card_answer_at"] != itoa(answeredAt) {
		t.Errorf("card %q at %q answered %q (want engaged, %d, %d)", v["card_state"], v["card_at"],
			v["card_answer_at"], shownAt, answeredAt)
	}
	if v["card_trigger"] != "ads" || v["card_surface"] != "panel" || v["card_variant"] != "" {
		t.Errorf("card %v", v)
	}
	// Its answer stays the engagement's: a close that comes after it writes nothing (CodeRabbit #282, round 3).
	if v["card_answer"] != "yes" {
		t.Errorf("card_answer %q after a later close (want yes)", v["card_answer"])
	}
	// A state it doesn't know, or seconds out of range, write nothing of the card (the message that takes no branch).
	if w := visitReq(t, s, c, vid2, snapshot(map[string]any{"card": map[string]any{"trigger": "ads", "state": "loved", "s": 2}}), nil); w.Code != http.StatusOK {
		t.Fatal(w.Body.String())
	}
	if w := visitReq(t, s, c, vid2, snapshot(map[string]any{"card": map[string]any{"trigger": "ads", "state": "shown", "s": 1.5}}), nil); w.Code != http.StatusOK {
		t.Fatal(w.Body.String())
	}
	v2 := hash(t, rc, regKey(vid2))
	for _, k := range []string{"card_state", "card_at", "card_trigger"} {
		if _, ok := v2[k]; ok {
			t.Errorf("%s written from a card it can't read: %v", k, v2)
		}
	}
}

func TestTheLastSnapshotOfAPageCarriesItsPassInTheBody(t *testing.T) {
	// sendBeacon sends no header: the pass rides in the body, text/plain (no preflight).
	s, c, rc := setup(t)
	b, _ := json.Marshal(snapshot(map[string]any{"pass": visitorPass(c, vid1)}))
	req := httptest.NewRequest("POST", "/v1/visit", bytes.NewReader(b))
	req.Header.Set("Content-Type", "text/plain;charset=UTF-8")
	w := httptest.NewRecorder()
	s.Handler().ServeHTTP(w, req)
	if w.Code != http.StatusOK || hash(t, rc, regKey(vid1))["landing"] == "" {
		t.Fatalf("beacon: %d %s", w.Code, w.Body.String())
	}
	// Another visitor's pass in the body is that visitor's, never this one's: the pass names the visit.
	b, _ = json.Marshal(snapshot(map[string]any{"pass": visitorPass(c, vid2), "page_id": "pg-other-9"}))
	req = httptest.NewRequest("POST", "/v1/visit", bytes.NewReader(b))
	w = httptest.NewRecorder()
	s.Handler().ServeHTTP(w, req)
	if w.Code != http.StatusOK || strings.Contains(hash(t, rc, regKey(vid1))["pages"], "pg-other-9") {
		t.Fatalf("a pass names its own visit: %d %v", w.Code, hash(t, rc, regKey(vid1)))
	}
}

func TestASnapshotWithoutAPassOrAPageIsRefused(t *testing.T) {
	s, c, rc := setup(t)
	if w := visitReq(t, s, c, vid1, snapshot(nil), map[string]string{"X-Live-Pass": ""}); w.Code != http.StatusUnauthorized {
		t.Errorf("no pass: %d", w.Code)
	}
	if w := visitReq(t, s, c, vid1, snapshot(map[string]any{"page_id": "x"}), nil); w.Code != http.StatusBadRequest {
		t.Errorf("no page id: %d", w.Code)
	}
	req := httptest.NewRequest("POST", "/v1/visit", strings.NewReader("not json"))
	req.Header.Set("X-Live-Pass", visitorPass(c, vid1))
	w := httptest.NewRecorder()
	s.Handler().ServeHTTP(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("not JSON: %d", w.Code)
	}
	if n := s.Counters.VisitsRefused.Load(); n != 3 {
		t.Errorf("refused counted %d, want 3", n)
	}
	if len(hash(t, rc, regKey(vid1))) != 0 {
		t.Errorf("a refused snapshot wrote")
	}
}

func TestAVisitorSendsTwentyAtOnceThenOneEveryThreeSeconds(t *testing.T) {
	s, c, _ := setup(t)
	for i := 0; i < VisitBurst; i++ {
		if w := visitReq(t, s, c, vid1, snapshot(nil), nil); w.Code != http.StatusOK {
			t.Fatalf("snapshot %d: %d", i+1, w.Code)
		}
	}
	if w := visitReq(t, s, c, vid1, snapshot(nil), nil); w.Code != http.StatusTooManyRequests {
		t.Fatalf("the 21st at once: %d", w.Code)
	}
	// Another visitor is not held by this one's pace.
	if w := visitReq(t, s, c, vid2, snapshot(nil), nil); w.Code != http.StatusOK {
		t.Fatalf("another visitor: %d", w.Code)
	}
	c.add(3)
	if w := visitReq(t, s, c, vid1, snapshot(nil), nil); w.Code != http.StatusOK {
		t.Fatalf("after 3 s: %d", w.Code)
	}
	if s.Counters.VisitsTooMany.Load() != 1 || s.Counters.Visits.Load() != VisitBurst+2 {
		t.Errorf("counted %d too many, %d kept", s.Counters.VisitsTooMany.Load(), s.Counters.Visits.Load())
	}
	// Quiet long enough, its pace is forgotten.
	c.add(VisitBurst * VisitRefillS * 3)
	s.ForgetOldBeats()
	s.visitMu.Lock()
	left := len(s.visitBuckets)
	s.visitMu.Unlock()
	if left != 0 {
		t.Errorf("%d paces kept", left)
	}
}

func TestAVisitGoneQuietGoesToTheQueue(t *testing.T) {
	s, c, rc := setup(t)
	ctx := context.Background()
	if w := visitReq(t, s, c, vid1, snapshot(nil), nil); w.Code != http.StatusOK {
		t.Fatal(w.Body.String())
	}
	first := c.now().Unix()
	if w := visitReq(t, s, c, vid2, snapshot(nil), nil); w.Code != http.StatusOK {
		t.Fatal(w.Body.String())
	}
	c.add(VisitQuiet - 60)
	// vid2 is still on the site: its visit stays open.
	if w := visitReq(t, s, c, vid2, snapshot(map[string]any{"page_id": "pg-later-3"}), nil); w.Code != http.StatusOK {
		t.Fatal(w.Body.String())
	}
	c.add(61)
	s.CloseQuietVisits(ctx)
	if s.Counters.VisitsEnded.Load() != 1 || s.Counters.VisitsLost.Load() != 0 {
		t.Fatalf("ended %d lost %d", s.Counters.VisitsEnded.Load(), s.Counters.VisitsLost.Load())
	}
	queued, err := resp.Strings(rc.Do(ctx, "LRANGE", EndedKey, "0", "-1"))
	want := "live:ended:" + vid1 + ":" + itoa(first)
	if err != nil || len(queued) != 1 || queued[0] != want {
		t.Fatalf("queue %v (%v), want [%s]", queued, err, want)
	}
	ended := hash(t, rc, want)
	if ended["landing"] != "/flights/new-york-istanbul" || ended["pages"] != "pg-first-1" {
		t.Errorf("the ended visit lost its fields: %v", ended)
	}
	ttl, _ := resp.Int(rc.Do(ctx, "TTL", want))
	if ttl <= VisitOpenTTL || ttl > VisitEndedTTL {
		t.Errorf("the ended visit lives %d s in the queue", ttl)
	}
	if n, _ := resp.Int(rc.Do(ctx, "EXISTS", regKey(vid1))); n != 0 {
		t.Errorf("the open visit is still there")
	}
	if n, _ := resp.Int(rc.Do(ctx, "ZCARD", RegKey)); n != 1 {
		t.Errorf("%d open visits listed, want vid2 only", n)
	}
	// The visitor comes back: a new visit.
	if w := visitReq(t, s, c, vid1, snapshot(map[string]any{"page_id": "pg-back-44"}), nil); w.Code != http.StatusOK {
		t.Fatal(w.Body.String())
	}
	if v := hash(t, rc, regKey(vid1)); v["pages"] != "pg-back-44" || v["first_at"] != itoa(c.now().Unix()) {
		t.Errorf("back on the site: %v", v)
	}
}

func TestASnapshotAfterTheQuietEndsTheOldVisitFirst(t *testing.T) {
	// The sweep didn't run (bbc-live restarted, Redis away): the visitor's next snapshot ends the old visit itself.
	s, c, rc := setup(t)
	ctx := context.Background()
	if w := visitReq(t, s, c, vid1, snapshot(nil), nil); w.Code != http.StatusOK {
		t.Fatal(w.Body.String())
	}
	first := c.now().Unix()
	c.add(VisitQuiet + 1)
	if w := visitReq(t, s, c, vid1, snapshot(map[string]any{"page_id": "pg-next-77"}), nil); w.Code != http.StatusOK {
		t.Fatal(w.Body.String())
	}
	queued, _ := resp.Strings(rc.Do(ctx, "LRANGE", EndedKey, "0", "-1"))
	if len(queued) != 1 || queued[0] != "live:ended:"+vid1+":"+itoa(first) || s.Counters.VisitsEnded.Load() != 1 {
		t.Fatalf("queue %v, ended %d", queued, s.Counters.VisitsEnded.Load())
	}
	if v := hash(t, rc, regKey(vid1)); v["pages"] != "pg-next-77" {
		t.Errorf("the new visit: %v", v)
	}
	// Under the quiet (the message that takes no branch): the same visit goes on.
	c.add(VisitQuiet)
	if w := visitReq(t, s, c, vid1, snapshot(map[string]any{"page_id": "pg-same-78"}), nil); w.Code != http.StatusOK {
		t.Fatal(w.Body.String())
	}
	if v := hash(t, rc, regKey(vid1)); v["pages"] != "pg-next-77,pg-same-78" {
		t.Errorf("the same visit: %v", v)
	}
}

func TestPagesPastTheLimitAreCountedNotKept(t *testing.T) {
	s, c, rc := setup(t)
	for i := 0; i <= VisitPages; i++ {
		c.add(VisitRefillS)
		id := "pg-many-" + itoa(int64(i))
		if w := visitReq(t, s, c, vid1, snapshot(map[string]any{"page_id": id}), nil); w.Code != http.StatusOK {
			t.Fatalf("page %d: %d", i, w.Code)
		}
		if i == VisitPages {
			// The same page past the limit again: counted once.
			c.add(VisitRefillS)
			visitReq(t, s, c, vid1, snapshot(map[string]any{"page_id": id}), nil)
		}
	}
	v := hash(t, rc, regKey(vid1))
	if n := len(strings.Split(v["pages"], ",")); n != VisitPages || v["pages_over"] != "1" {
		t.Errorf("%d pages kept, %q over", n, v["pages_over"])
	}
	if _, kept := v["pg:pg-many-"+itoa(VisitPages)]; kept {
		t.Errorf("the page past the limit was kept")
	}
}

func TestTheVisitorsAddressIsWhatTheEdgeSaw(t *testing.T) {
	req := httptest.NewRequest("POST", "/v1/visit", nil)
	req.RemoteAddr = "10.0.0.5:4431"
	if ip := clientIP(req); ip != "10.0.0.5" {
		t.Errorf("no headers: %q", ip)
	}
	req.Header.Set("X-Real-IP", "192.0.2.10")
	if ip := clientIP(req); ip != "192.0.2.10" {
		t.Errorf("X-Real-IP: %q", ip)
	}
	// The client writes what stands before the edge's own entry; the last one is the edge's.
	req.Header.Add("X-Forwarded-For", "1.2.3.4, 198.51.100.7")
	req.Header.Add("X-Forwarded-For", "2001:db8::1")
	if ip := clientIP(req); ip != "2001:db8::1" {
		t.Errorf("X-Forwarded-For: %q", ip)
	}
	req.Header.Set("X-Forwarded-For", "203.0.113.7, not-an-address")
	if ip := clientIP(req); ip != "192.0.2.10" {
		t.Errorf("a last entry that is no address falls back: %q", ip)
	}
}

func TestHealthCountsTheRegistry(t *testing.T) {
	s, c, _ := setup(t)
	visitReq(t, s, c, vid1, snapshot(nil), nil)
	out := decode(t, do(t, s.Handler(), "GET", "/health", nil, nil))
	counters := out["counters"].(map[string]any)
	for _, k := range []string{"visits", "visits_refused", "visits_too_many", "visits_failed", "visits_ended",
		"visits_lost", "visits_close_failed"} {
		if _, ok := counters[k]; !ok {
			t.Errorf("rule 2: %s not in /health", k)
		}
	}
	reg := out["registry"].(map[string]any)
	if reg["open"] != float64(1) || reg["queued"] != float64(0) {
		t.Errorf("registry %v", reg)
	}
}

func TestAnEngagementAfterNotNowTakesItsOwnTimeAndAnswer(t *testing.T) {
	// CodeRabbit #282, round 3: the answer and its time go with the state that won.
	s, c, rc := setup(t)
	card := func(state string, sec, answerS float64, answer string) map[string]any {
		return snapshot(map[string]any{"card": map[string]any{"trigger": "kayak", "surface": "card",
			"state": state, "s": sec, "answer": answer, "answer_s": answerS}})
	}
	if w := visitReq(t, s, c, vid1, card("dismissed", 8, 1, "not_now"), nil); w.Code != http.StatusOK {
		t.Fatal(w.Body.String())
	}
	c.add(20)
	if w := visitReq(t, s, c, vid1, card("engaged", 28, 2, "channel"), nil); w.Code != http.StatusOK {
		t.Fatal(w.Body.String())
	}
	engagedAt := c.now().Unix() - 2
	v := hash(t, rc, regKey(vid1))
	if v["card_state"] != "engaged" || v["card_answer"] != "channel" || v["card_answer_at"] != itoa(engagedAt) {
		t.Errorf("card %q answer %q at %q (want engaged, channel, %d)", v["card_state"], v["card_answer"],
			v["card_answer_at"], engagedAt)
	}
	// The "not now" sent again late, by a snapshot that crossed the engagement on the way: nothing changes.
	c.add(5)
	if w := visitReq(t, s, c, vid1, card("dismissed", 33, 26, "not_now"), nil); w.Code != http.StatusOK {
		t.Fatal(w.Body.String())
	}
	v = hash(t, rc, regKey(vid1))
	if v["card_state"] != "engaged" || v["card_answer"] != "channel" || v["card_answer_at"] != itoa(engagedAt) {
		t.Errorf("after a late not now: %q %q %q", v["card_state"], v["card_answer"], v["card_answer_at"])
	}
}

func TestAnOlderSnapshotOfAPageNeverReplacesANewerOne(t *testing.T) {
	// A fetch and the page's last beacon can cross on the way (CodeRabbit #282, round 3).
	s, c, rc := setup(t)
	page := func() VisitPage {
		var p VisitPage
		if err := json.Unmarshal([]byte(hash(t, rc, regKey(vid1))["pg:pg-first-1"]), &p); err != nil {
			t.Fatal(err)
		}
		return p
	}
	send := func(pageS, seenS int, events string) {
		t.Helper()
		if w := visitReq(t, s, c, vid1, snapshot(map[string]any{"page_s": pageS, "seen_s": seenS, "events": events}), nil); w.Code != http.StatusOK {
			t.Fatal(w.Body.String())
		}
	}
	send(65, 60, "card:kayak:card;chat_msg")
	c.add(1)
	send(60, 58, "card:kayak:card") // the earlier one, arriving last
	if p := page(); p.S != 65 || p.Seen != 60 || len(p.Events) != 2 {
		t.Errorf("the older snapshot replaced the page: %+v", p)
	}
	c.add(1)
	send(65, 60, "card:kayak:card") // the same second, fewer events: older too
	if p := page(); len(p.Events) != 2 {
		t.Errorf("a snapshot with fewer events replaced the page: %+v", p)
	}
	c.add(1)
	send(90, 80, "card:kayak:card;chat_msg*2") // newer: it replaces
	if p := page(); p.S != 90 || p.Seen != 80 {
		t.Errorf("a newer snapshot did not replace the page: %+v", p)
	}
}
