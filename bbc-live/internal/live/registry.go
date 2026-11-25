package live

// The visit registry (contracts/live.md "The visit registry"; the owner, 25 Nov 2026: every visit, with the
// visitor's address, kept for analysis). The widget sends a snapshot of the page it is on (POST /v1/visit): at
// the page's start, after what happened on it, every minute while it is seen and when it is hidden. bbc-live
// merges the snapshots of one visitor into one open visit in Redis (visit.lua); a visit with no snapshot for
// VisitQuiet seconds has ended and goes to the queue (close.lua, every 30 s, or visit.lua when the visitor comes
// back later); the API takes the queue into the database. bbc-live never touches the database.
//
// What a visit keeps: the pages (path, kind, seconds, the widget's events and the site's steps on each), where
// the visitor came from, the screen, the language and time zone, the country the API saw, the visitor's address
// and browser (as Railway's edge hands them over), the PostHog session, the card (its moment, words, place, what
// came of it and when), the chat's id. Never a name, an e-mail, a phone or anything the visitor typed.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"net"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/Nasalciuc/BBC-AI-Chatbot-Admin-Panel/bbc-live/internal/resp"
)

// The registry's keys and numbers.
const (
	RegKey   = "live:reg"   // sorted set: the open visits, visitor id → unix seconds of the last snapshot
	EndedKey = "live:ended" // list: the ended visits' keys, oldest first, for the API to take

	VisitQuiet    = 1800      // a visit with no snapshot for 30 minutes has ended
	VisitOpenTTL  = 6 * 3600  // an open visit lives this long after its last snapshot (the sweep ends it at 30 min)
	VisitEndedTTL = 3 * 86400 // an ended visit waits this long in the queue for the API
	VisitPages    = 40        // pages one visit keeps; more are counted
	VisitEvents   = 40        // the widget's events one page keeps
	VisitSteps    = 30        // the site's steps one page keeps
	VisitCloseMax = 200       // visits one sweep ends
	VisitBodyMax  = 8192
	// A visitor's snapshots: 20 at once, then one per 3 s (the widget sends a few a minute).
	VisitBurst   = 20
	VisitRefillS = 3.0
)

func regKey(vid string) string { return "live:reg:" + vid }

var (
	visitLua = load("visit.lua")
	closeLua = load("close.lua")
)

// VisitPage is one page of a visit as the registry keeps it (JSON in the visit's hash, `pg:<page id>`).
type VisitPage struct {
	Path   string   `json:"path"`
	Type   string   `json:"type,omitempty"`
	S      int64    `json:"s"`    // seconds since the page loaded
	Seen   int64    `json:"seen"` // seconds it was in sight
	Events []string `json:"ev,omitempty"`
	Steps  []string `json:"st,omitempty"`
	At     int64    `json:"at"` // when bbc-live last heard of it (unix seconds)
}

// VisitCard is what a snapshot says of the card.
type VisitCard struct {
	State    string // shown, dismissed, engaged or ""
	At       int64  // when it showed, unix seconds by bbc-live's clock; 0 unknown
	AnswerAt int64  // when it was answered or dismissed; 0 unknown
}

// VisitSnap is one cleaned snapshot.
type VisitSnap struct {
	ID     string
	PageID string
	Page   VisitPage
	Card   VisitCard
	// First: kept from the visit's first snapshot with a value. Last: the latest value wins. Empty values never write.
	First [][2]string
	Last  [][2]string
}

// Visit merges a snapshot into the visitor's open visit; true when a visit gone quiet went to the queue first.
func (s *Store) Visit(ctx context.Context, v VisitSnap) (bool, error) {
	page, err := json.Marshal(v.Page)
	if err != nil {
		return false, err
	}
	at := func(t int64) string {
		if t > 0 {
			return strconv.FormatInt(t, 10)
		}
		return ""
	}
	args := []string{v.ID, strconv.FormatInt(s.now().Unix(), 10), strconv.Itoa(VisitQuiet), strconv.Itoa(VisitOpenTTL),
		strconv.Itoa(VisitEndedTTL), strconv.Itoa(VisitPages), v.PageID, string(page), v.Card.State, at(v.Card.At),
		at(v.Card.AnswerAt), strconv.Itoa(len(v.First))}
	for _, kv := range v.First {
		args = append(args, kv[0], kv[1])
	}
	args = append(args, strconv.Itoa(len(v.Last)))
	for _, kv := range v.Last {
		args = append(args, kv[0], kv[1])
	}
	n, err := resp.Int(s.eval(ctx, visitLua, []string{regKey(v.ID), RegKey, EndedKey}, args...))
	return n == 2, err
}

// CloseQuiet sends the visits gone quiet to the queue: how many ended, and how many were listed with nothing left.
func (s *Store) CloseQuiet(ctx context.Context) (int64, int64, error) {
	v, err := s.eval(ctx, closeLua, []string{RegKey, EndedKey}, strconv.FormatInt(s.now().Unix(), 10),
		strconv.Itoa(VisitQuiet), strconv.Itoa(VisitEndedTTL), strconv.Itoa(VisitCloseMax))
	if err != nil {
		return 0, 0, err
	}
	if v.Kind != '*' || len(v.Array) != 2 {
		return 0, 0, fmt.Errorf("live: close answered %d entries", len(v.Array))
	}
	return v.Array[0].Int, v.Array[1].Int, nil
}

// Registry says how many visits are open and how many wait for the API (/health).
func (s *Store) Registry(ctx context.Context) (open, queued int64, err error) {
	if open, err = resp.Int(s.R.Do(ctx, "ZCARD", RegKey)); err != nil {
		return 0, 0, err
	}
	queued, err = resp.Int(s.R.Do(ctx, "LLEN", EndedKey))
	return open, queued, err
}

// ── what the widget sends ───────────────────────────────────────────

type visitCardIn struct {
	Trigger string  `json:"trigger"`
	Variant string  `json:"variant"`
	Surface string  `json:"surface"`
	State   string  `json:"state"`
	S       float64 `json:"s"`        // seconds since it showed, by the visitor's clock
	Answer  string  `json:"answer"`   // the reply's kind or how it was closed
	AnswerS float64 `json:"answer_s"` // seconds since the answer
}

type visitIn struct {
	// The pass, for the page's last snapshot (sendBeacon sends no header); X-Live-Pass otherwise.
	Pass        string       `json:"pass"`
	PageID      string       `json:"page_id"`
	Page        string       `json:"page"`
	PageType    string       `json:"page_type"`
	PageS       float64      `json:"page_s"`
	SeenS       float64      `json:"seen_s"`
	From        string       `json:"from"`
	To          string       `json:"to"`
	Depart      string       `json:"depart"`
	Return      string       `json:"return"`
	Cabin       string       `json:"cabin"`
	Pax         int          `json:"pax"`
	Source      string       `json:"source"`
	Channel     string       `json:"channel"`
	UtmSource   string       `json:"utm_source"`
	UtmMedium   string       `json:"utm_medium"`
	UtmCampaign string       `json:"utm_campaign"`
	Referrer    string       `json:"referrer"`
	Device      string       `json:"device"`
	Site        string       `json:"site"`
	Lang        string       `json:"lang"`
	TZ          string       `json:"tz"`
	Country     string       `json:"country"`
	PHSession   string       `json:"ph_session"`
	Events      string       `json:"events"`
	Steps       string       `json:"steps"`
	Card        *visitCardIn `json:"card"`
	HasChat     bool         `json:"has_chat"`
	Chat        string       `json:"conversation_id"`
}

var (
	pageID   = regexp.MustCompile(`^[A-Za-z0-9_-]{6,32}$`)
	channels = map[string]bool{"kayak": true, "google": true, "meta": true, "bing": true, "ads": true,
		"search": true, "referral": true, "direct": true, "other": true}
	// An ad's tag: letters, digits, spaces and a few signs. An @ or anything else drops the whole tag.
	utmTag    = regexp.MustCompile(`^[A-Za-z0-9 _.,:|+/()-]{1,80}$`)
	hostName  = regexp.MustCompile(`^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$`)
	langTag   = regexp.MustCompile(`^[a-z]{2,3}(-[A-Za-z0-9]{2,8}){0,2}$`)
	timeZone  = regexp.MustCompile(`^[A-Za-z][A-Za-z_]{0,19}(/[A-Za-z0-9_+-]{1,30}){0,2}$`)
	country   = regexp.MustCompile(`^[A-Z]{2}$`)
	uuidShape = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)
	surfaces  = map[string]bool{"card": true, "panel": true, "row": true}
	// A card's words: the variant the offer answer named (the note's bandit, row, contact, beat, a group's words).
	variantTag = regexp.MustCompile(`^[a-z0-9_-]{1,32}$`)
	answerTag  = regexp.MustCompile(`^[a-z_]{1,24}$`)
	// One of the widget's events on the page (visitsRead.ts eventToken): a name, at most two qualifiers from fixed
	// lists, and how many times in a row. Words only: no number of a phone, no address, no text fits.
	eventTag = regexp.MustCompile(`^[a-z][a-z_]{0,31}(:[a-z0-9_]{1,24}){0,2}(\*[1-9][0-9]{0,2})?$`)
)

// wholeSeconds keeps a whole number of seconds within a day; 0 otherwise.
func wholeSeconds(v float64) int64 {
	if v < 0 || v > 86400 || v != math.Trunc(v) {
		return 0
	}
	return int64(v)
}

// tokens keeps the items that match, at most max of them, in order.
func tokens(line string, re *regexp.Regexp, max int) []string {
	if line == "" || len(line) > 4000 {
		return nil
	}
	var out []string
	for _, t := range strings.Split(line, ";") {
		if len(out) == max {
			break
		}
		if re.MatchString(t) {
			out = append(out, t)
		}
	}
	return out
}

// cleanUA keeps the browser's own line, printable and at most 300 characters.
func cleanUA(ua string) string {
	if !utf8.ValidString(ua) {
		return ""
	}
	var b strings.Builder
	n := 0
	for _, r := range ua {
		if n == 300 {
			break
		}
		if !unicode.IsPrint(r) {
			continue
		}
		b.WriteRune(r)
		n++
	}
	return strings.TrimSpace(b.String())
}

// clientIP is the visitor's address as Railway's edge saw it: the last X-Forwarded-For entry (the edge adds its own
// at the end; whatever stands before it, the client wrote), else X-Real-IP, else the connection's.
func clientIP(r *http.Request) string {
	if all := strings.Split(strings.Join(r.Header.Values("X-Forwarded-For"), ","), ","); len(all) > 0 {
		if ip := net.ParseIP(strings.TrimSpace(all[len(all)-1])); ip != nil {
			return ip.String()
		}
	}
	if ip := net.ParseIP(strings.TrimSpace(r.Header.Get("X-Real-IP"))); ip != nil {
		return ip.String()
	}
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		host = r.RemoteAddr
	}
	if ip := net.ParseIP(host); ip != nil {
		return ip.String()
	}
	return ""
}

func refHost(v string) string {
	v = strings.ToLower(strings.TrimSpace(v))
	if len(v) > 100 || !hostName.MatchString(v) {
		return ""
	}
	return v
}

func tag(re *regexp.Regexp, v string) string {
	v = strings.TrimSpace(v)
	if re.MatchString(v) {
		return v
	}
	return ""
}

// cleanVisit keeps what the registry may hold; ok is false when the snapshot names no page id.
func cleanVisit(vid string, in visitIn, ip, ua string, now time.Time) (VisitSnap, bool) {
	if !pageID.MatchString(in.PageID) {
		return VisitSnap{}, false
	}
	b := cleanBeat(vid, beatIn{Page: in.Page, From: in.From, To: in.To, Depart: in.Depart, Return: in.Return,
		Cabin: in.Cabin, Pax: in.Pax, Source: in.Source, Device: in.Device, Site: in.Site, PageType: in.PageType})
	snap := VisitSnap{
		ID:     vid,
		PageID: in.PageID,
		Page: VisitPage{Path: b.Page, Type: b.PageType, S: wholeSeconds(in.PageS), Seen: wholeSeconds(in.SeenS),
			Events: tokens(in.Events, eventTag, VisitEvents), Steps: tokens(in.Steps, activityStep, VisitSteps),
			At: now.Unix()},
	}
	pax := ""
	if b.Trip.Pax > 0 {
		pax = strconv.Itoa(b.Trip.Pax)
	}
	chat := ""
	if uuidShape.MatchString(in.Chat) {
		chat = in.Chat
	}
	var trigger, variant, surface, answer string
	if c := in.Card; c != nil {
		state, at, trig := cleanAlexCard(c.State, c.S, c.Trigger, now)
		if state != "" {
			snap.Card = VisitCard{State: state, At: at}
			trigger, variant, surface = trig, tag(variantTag, c.Variant), pick(surfaces, c.Surface)
			if state != "shown" {
				// When it was answered: a whole number of seconds ago, within a day (0 is now).
				if c.AnswerS >= 0 && c.AnswerS <= 86400 && c.AnswerS == math.Trunc(c.AnswerS) {
					snap.Card.AnswerAt = now.Unix() - int64(c.AnswerS)
				}
				answer = tag(answerTag, c.Answer)
			}
		}
	}
	snap.First = [][2]string{
		{"landing", b.Page}, {"landing_type", b.PageType}, {"source", b.Source}, {"channel", pick(channels, in.Channel)},
		{"utm_source", tag(utmTag, in.UtmSource)}, {"utm_medium", tag(utmTag, in.UtmMedium)},
		{"utm_campaign", tag(utmTag, in.UtmCampaign)}, {"referrer", refHost(in.Referrer)}, {"device", b.Device},
		{"site", b.Site}, {"lang", tag(langTag, in.Lang)}, {"tz", tag(timeZone, in.TZ)},
		{"country", tag(country, in.Country)}, {"ip", ip}, {"ua", cleanUA(ua)},
		{"ph_session", tag(uuidShape, strings.ToLower(in.PHSession))}, {"conversation_id", chat},
		{"card_trigger", trigger}, {"card_variant", variant}, {"card_surface", surface},
	}
	hasChat := ""
	if in.HasChat {
		hasChat = "1"
	}
	snap.Last = [][2]string{
		{"last_page", b.Page}, {"from", b.Trip.From}, {"to", b.Trip.To}, {"depart", b.Trip.Depart},
		{"return", b.Trip.Return}, {"cabin", b.Trip.Cabin}, {"pax", pax}, {"has_chat", hasChat},
		{"card_answer", answer},
	}
	return snap, true
}

// takeVisitTurn says whether this visitor may send a snapshot now (VisitBurst at once, then one per VisitRefillS).
func (s *Server) takeVisitTurn(vid string, now time.Time) bool {
	s.visitMu.Lock()
	defer s.visitMu.Unlock()
	if s.visitBuckets == nil {
		s.visitBuckets = map[string]visitBucket{}
	}
	b, seen := s.visitBuckets[vid]
	if !seen {
		b = visitBucket{tokens: VisitBurst, at: now}
	}
	b.tokens = math.Min(VisitBurst, b.tokens+now.Sub(b.at).Seconds()/VisitRefillS)
	b.at = now
	ok := b.tokens >= 1
	if ok {
		b.tokens--
	}
	s.visitBuckets[vid] = b
	return ok
}

type visitBucket struct {
	tokens float64
	at     time.Time
}

// forgetVisitTurns drops the buckets of visitors quiet long enough for theirs to be full again.
func (s *Server) forgetVisitTurns(now time.Time) {
	cut := now.Add(-time.Duration(VisitBurst*VisitRefillS*2) * time.Second)
	s.visitMu.Lock()
	defer s.visitMu.Unlock()
	for vid, b := range s.visitBuckets {
		if b.at.Before(cut) {
			delete(s.visitBuckets, vid)
		}
	}
}

func (s *Server) visit(w http.ResponseWriter, r *http.Request) {
	var in visitIn
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, VisitBodyMax)).Decode(&in); err != nil {
		s.Counters.VisitsRefused.Add(1)
		fail(w, http.StatusBadRequest, "bad visit")
		return
	}
	pass := r.Header.Get("X-Live-Pass")
	if pass == "" {
		pass = in.Pass
	}
	vid, ok := s.visitorOf(w, pass)
	if !ok {
		s.Counters.VisitsRefused.Add(1)
		return
	}
	now := s.now()
	if !s.takeVisitTurn(vid, now) {
		s.Counters.VisitsTooMany.Add(1)
		fail(w, http.StatusTooManyRequests, "too many")
		return
	}
	snap, ok := cleanVisit(vid, in, clientIP(r), r.UserAgent(), now)
	if !ok {
		s.Counters.VisitsRefused.Add(1)
		fail(w, http.StatusBadRequest, "page_id")
		return
	}
	queued, err := s.Store.Visit(r.Context(), snap)
	if err != nil {
		s.Counters.VisitsFailed.Add(1)
		s.redisFailed(w, "visit", err)
		return
	}
	if queued {
		s.Counters.VisitsEnded.Add(1)
	}
	s.Counters.Visits.Add(1)
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}

// CloseQuietVisits ends the visits gone quiet (run it every 30 s). Errors are counted and logged.
func (s *Server) CloseQuietVisits(ctx context.Context) {
	ended, lost, err := s.Store.CloseQuiet(ctx)
	if err != nil {
		if errors.Is(err, context.Canceled) {
			return
		}
		s.Counters.VisitsCloseFailed.Add(1)
		s.Log.Error("redis failed", "where", "close visits", "error", err.Error())
		return
	}
	s.Counters.VisitsEnded.Add(ended)
	s.Counters.VisitsLost.Add(lost)
}
