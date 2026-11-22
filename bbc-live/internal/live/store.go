package live

import (
	"context"
	"crypto/sha1"
	"embed"
	"encoding/hex"
	"errors"
	"fmt"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/Nasalciuc/BBC-AI-Chatbot-Admin-Panel/bbc-live/internal/resp"
)

// The Redis keys and the timings (contracts/live.md).
const (
	VisitorsKey  = "live:visitors"
	InboxChannel = "live:inbox"

	VisitTTL   = 60   // a visit lives this long after its last beat
	Window     = 45   // on the site now: a beat in the last 45 s
	ClaimTTL   = 600  // an agent holds a visitor this long to say hello
	MaxClaims  = 3    // visitors an agent holds at a time
	DismissTTL = 3600 // after "not now", nobody says hello for an hour
	ListLimit  = 500
)

func visitKey(vid string) string     { return "live:v:" + vid }
func claimKey(vid string) string     { return "live:claim:" + vid }
func claimsKey(agent string) string  { return "live:claims:" + agent }
func dismissedKey(vid string) string { return "live:dismissed:" + vid }
func inboxKey(vid string) string     { return "live:inbox:" + vid }

//go:embed lua/*.lua
var luaFiles embed.FS

type script struct {
	src string
	sha string
}

func load(name string) script {
	b, err := luaFiles.ReadFile("lua/" + name)
	if err != nil {
		panic(fmt.Sprintf("live: the script %s is missing: %v", name, err))
	}
	sum := sha1.Sum(b)
	return script{src: string(b), sha: hex.EncodeToString(sum[:])}
}

var (
	beatLua      = load("beat.lua")
	listLua      = load("list.lua")
	claimLua     = load("claim.lua")
	releaseLua   = load("release.lua")
	dismissLua   = load("dismiss.lua")
	takeInboxLua = load("take_inbox.lua")
)

// Store is bbc-live's whole view of Redis.
type Store struct {
	R   *resp.Client
	Now func() time.Time
}

func (s *Store) now() time.Time {
	if s.Now != nil {
		return s.Now()
	}
	return time.Now()
}

// eval runs a script by its SHA and loads it once when Redis doesn't know it yet.
func (s *Store) eval(ctx context.Context, sc script, keys []string, args ...string) (resp.Value, error) {
	cmd := append([]string{"EVALSHA", sc.sha, strconv.Itoa(len(keys))}, keys...)
	v, err := s.R.Do(ctx, append(cmd, args...)...)
	var replyErr resp.Error
	if errors.As(err, &replyErr) && strings.HasPrefix(string(replyErr), "NOSCRIPT") {
		cmd[0], cmd[1] = "EVAL", sc.src
		return s.R.Do(ctx, append(cmd, args...)...)
	}
	return v, err
}

// Trip is what the visitor's page says they are looking for. Codes and dates only.
type Trip struct {
	From   string `json:"from,omitempty"`
	To     string `json:"to,omitempty"`
	Depart string `json:"depart,omitempty"`
	Return string `json:"return,omitempty"`
	Cabin  string `json:"cabin,omitempty"`
	Pax    int    `json:"pax,omitempty"`
}

// Visit is one visitor's beat, already cleaned (see cleanBeat): nothing typed by the visitor.
type Visit struct {
	ID       string
	Page     string
	Trip     Trip
	Source   string
	Device   string
	Site     string
	ChatOpen bool
	HasChat  bool
	// What the site says the page is, and the steps the visitor took there (";" between them).
	PageType string
	Activity string
	// The owner's agent-first test: when Alex's card may come (unix seconds), 0 when he waits for nobody.
	AlexAt int64
}

func flag(b bool) string {
	if b {
		return "1"
	}
	return "0"
}

// Beat records a visit.
func (s *Store) Beat(ctx context.Context, v Visit) error {
	pax := ""
	if v.Trip.Pax > 0 {
		pax = strconv.Itoa(v.Trip.Pax)
	}
	alexAt := ""
	if v.AlexAt > 0 {
		alexAt = strconv.FormatInt(v.AlexAt, 10)
	}
	fields := []string{
		"page", v.Page, "from", v.Trip.From, "to", v.Trip.To, "depart", v.Trip.Depart,
		"return", v.Trip.Return, "cabin", v.Trip.Cabin, "pax", pax, "source", v.Source,
		"device", v.Device, "site", v.Site, "chat_open", flag(v.ChatOpen), "has_chat", flag(v.HasChat),
		"page_type", v.PageType, "activity", v.Activity, "alex_at", alexAt,
	}
	args := append([]string{v.ID, strconv.FormatInt(s.now().Unix(), 10), strconv.Itoa(VisitTTL)}, fields...)
	_, err := s.eval(ctx, beatLua, []string{visitKey(v.ID), VisitorsKey}, args...)
	return err
}

// Listed is a visitor on the site now, as the panel sees them.
type Listed struct {
	ID        string `json:"visitor_id"`
	FirstAt   int64  `json:"first_at"`
	LastAt    int64  `json:"last_at"`
	OnSite    int64  `json:"seconds_on_site"`
	Page      string `json:"page"`
	Trip      *Trip  `json:"trip,omitempty"`
	Source    string `json:"source"`
	Device    string `json:"device,omitempty"`
	Site      string `json:"site,omitempty"`
	ChatOpen  bool   `json:"chat_open"`
	HasChat   bool   `json:"has_chat"`
	ClaimedBy string `json:"claimed_by,omitempty"`
	Dismissed bool   `json:"dismissed"`
	// The kind of page (home, search, quote, ...) and the visitor's last steps on the site, oldest first.
	PageType string   `json:"page_type,omitempty"`
	Activity []string `json:"activity,omitempty"`
	// The agent-first test: when Alex's card may come (unix seconds), while he waits for an agent.
	AlexAt int64 `json:"alex_at,omitempty"`
}

// Live lists who beat in the last Window seconds, longest on the site first.
func (s *Store) Live(ctx context.Context) ([]Listed, error) {
	now := s.now().Unix()
	v, err := s.eval(ctx, listLua, []string{VisitorsKey}, strconv.FormatInt(now-Window, 10), strconv.Itoa(ListLimit))
	if err != nil {
		return nil, err
	}
	if v.Kind != '*' || len(v.Array)%4 != 0 {
		return nil, fmt.Errorf("live: list reply of %d entries", len(v.Array))
	}
	out := make([]Listed, 0, len(v.Array)/4)
	for i := 0; i < len(v.Array); i += 4 {
		h := map[string]string{}
		fields := v.Array[i+1].Array
		for j := 0; j+1 < len(fields); j += 2 {
			h[fields[j].Str] = fields[j+1].Str
		}
		first, _ := strconv.ParseInt(h["first_at"], 10, 64)
		last, _ := strconv.ParseInt(h["last_at"], 10, 64)
		pax, _ := strconv.Atoi(h["pax"])
		l := Listed{
			ID: v.Array[i].Str, FirstAt: first, LastAt: last, Page: h["page"], Source: h["source"],
			Device: h["device"], Site: h["site"], ChatOpen: h["chat_open"] == "1", HasChat: h["has_chat"] == "1",
			ClaimedBy: v.Array[i+2].Str, Dismissed: v.Array[i+3].Int == 1, PageType: h["page_type"],
		}
		if h["activity"] != "" {
			l.Activity = strings.Split(h["activity"], ";")
		}
		if at, err := strconv.ParseInt(h["alex_at"], 10, 64); err == nil && at > 0 {
			l.AlexAt = at
		}
		if first > 0 {
			l.OnSite = now - first
		}
		t := Trip{From: h["from"], To: h["to"], Depart: h["depart"], Return: h["return"], Cabin: h["cabin"], Pax: pax}
		if t != (Trip{}) {
			l.Trip = &t
		}
		out = append(out, l)
	}
	sort.SliceStable(out, func(a, b int) bool { return out[a].FirstAt < out[b].FirstAt })
	return out, nil
}

// ClaimResult is what a claim came to.
type ClaimResult string

const (
	Claimed   ClaimResult = "claimed"
	Mine      ClaimResult = "mine"
	Gone      ClaimResult = "gone"
	Taken     ClaimResult = "taken"
	Full      ClaimResult = "full"
	Declined  ClaimResult = "declined"
	InAChat   ClaimResult = "in_a_chat"
	unknownCR ClaimResult = ""
)

var claimCodes = map[int64]ClaimResult{1: Claimed, 0: Mine, -1: Gone, -2: Taken, -3: Full, -4: Declined, -5: InAChat}

// Claim gives the visitor to the agent, if nobody has them and the agent holds fewer than MaxClaims.
func (s *Store) Claim(ctx context.Context, agent, vid string) (ClaimResult, error) {
	n, err := resp.Int(s.eval(ctx, claimLua, []string{visitKey(vid), claimKey(vid), claimsKey(agent), dismissedKey(vid)},
		agent, vid, strconv.Itoa(MaxClaims), strconv.Itoa(ClaimTTL)))
	if err != nil {
		return unknownCR, err
	}
	r, ok := claimCodes[n]
	if !ok {
		return unknownCR, fmt.Errorf("live: claim answered %d", n)
	}
	return r, nil
}

// Release lets the visitor go; false when the agent didn't hold them.
func (s *Store) Release(ctx context.Context, agent, vid string) (bool, error) {
	n, err := resp.Int(s.eval(ctx, releaseLua, []string{claimKey(vid), claimsKey(agent)}, agent, vid))
	return n == 1, err
}

// Dismiss is the visitor's "not now"; it returns the agent who held them ("" for nobody).
func (s *Store) Dismiss(ctx context.Context, vid string) (string, error) {
	holder, err := resp.Str(s.eval(ctx, dismissLua, []string{dismissedKey(vid), claimKey(vid), inboxKey(vid)},
		vid, strconv.Itoa(DismissTTL)))
	if errors.Is(err, resp.ErrNil) {
		return "", nil
	}
	return holder, err
}

// TakeInbox takes what waits for the visitor (the API's messages, JSON), once.
func (s *Store) TakeInbox(ctx context.Context, vid string) ([]string, error) {
	return resp.Strings(s.eval(ctx, takeInboxLua, []string{inboxKey(vid)}))
}

// InboxLen says how much waits, without taking it.
func (s *Store) InboxLen(ctx context.Context, vid string) (int64, error) {
	return resp.Int(s.R.Do(ctx, "LLEN", inboxKey(vid)))
}

// Ping says whether Redis answers.
func (s *Store) Ping(ctx context.Context) error {
	_, err := s.R.Do(ctx, "PING")
	return err
}
