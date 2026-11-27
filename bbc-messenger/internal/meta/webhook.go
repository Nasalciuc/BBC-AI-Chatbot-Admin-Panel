// Package meta is what bbc-messenger knows of Meta's Messenger Platform: the webhook's signature and payload,
// and the Send API (contracts/messenger.md).
package meta

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"sort"
	"strconv"
	"strings"
)

// ValidSignature: Meta signs each webhook POST with the app secret, X-Hub-Signature-256 =
// "sha256=" + hex(HMAC-SHA256(app secret, the raw body)). No secret, no valid signature.
func ValidSignature(body []byte, header string, appSecret []byte) bool {
	if len(appSecret) == 0 {
		return false
	}
	sig, ok := strings.CutPrefix(strings.TrimSpace(header), "sha256=")
	if !ok || len(sig) != sha256.Size*2 {
		return false
	}
	got, err := hex.DecodeString(sig)
	if err != nil {
		return false
	}
	mac := hmac.New(sha256.New, appSecret)
	mac.Write(body)
	return hmac.Equal(got, mac.Sum(nil))
}

// Payload is a webhook POST: one or more entries, each a Page's events.
type Payload struct {
	Object string  `json:"object"`
	Entry  []Entry `json:"entry"`
}

// Entry is one Page's events. Messaging is what the app answers; Standby is what it hears while another app (the
// Page Inbox) owns the thread (conversation routing): heard, never answered.
type Entry struct {
	ID        string  `json:"id"`
	Time      int64   `json:"time"`
	Messaging []Event `json:"messaging"`
	Standby   []Event `json:"standby"`
}

type Party struct {
	ID string `json:"id"`
}

// Event is one messaging event. Read, delivery and reaction events carry none of the fields below that we use,
// and are skipped.
type Event struct {
	Sender    Party     `json:"sender"`
	Recipient Party     `json:"recipient"`
	Timestamp int64     `json:"timestamp"`
	Message   *Message  `json:"message"`
	Postback  *Postback `json:"postback"`
	Referral  *Referral `json:"referral"`
}

type Message struct {
	MID         string       `json:"mid"`
	Text        string       `json:"text"`
	QuickReply  *QuickReply  `json:"quick_reply"`
	IsEcho      bool         `json:"is_echo"`
	AppID       json.Number  `json:"app_id"`
	Attachments []Attachment `json:"attachments"`
}

type QuickReply struct {
	Payload string `json:"payload"`
}

type Attachment struct {
	Type string `json:"type"`
}

type Postback struct {
	MID      string    `json:"mid"`
	Title    string    `json:"title"`
	Payload  string    `json:"payload"`
	Referral *Referral `json:"referral"`
}

// Referral: where the person came from (an m.me link's ref, a Click-to-Messenger ad), as a UTM tag says it on the
// site.
type Referral struct {
	Ref    string `json:"ref,omitempty"`
	Source string `json:"source,omitempty"`
	Type   string `json:"type,omitempty"`
	AdID   string `json:"ad_id,omitempty"`
}

// Inbound is one event as the gateway hands it to the API (contracts/messenger.md "The API's side").
type Inbound struct {
	Kind          string    `json:"kind"` // message, postback, referral, echo
	PageID        string    `json:"page_id"`
	PSID          string    `json:"psid"`
	MID           string    `json:"mid"`
	Timestamp     int64     `json:"timestamp"`
	Text          string    `json:"text,omitempty"`
	QuickReply    string    `json:"quick_reply,omitempty"`
	Postback      string    `json:"postback,omitempty"`
	PostbackTitle string    `json:"postback_title,omitempty"`
	Referral      *Referral `json:"referral,omitempty"`
	Attachments   []string  `json:"attachments,omitempty"`
	// Heard while another app owns the thread: the API keeps it and answers nothing.
	Standby bool `json:"standby,omitempty"`
}

// Key is what makes a retried delivery the same event: Meta's message id, or for an event without one, who and
// when.
func (in Inbound) Key() string {
	if in.MID != "" {
		return in.MID
	}
	return in.Kind + ":" + in.PageID + ":" + in.PSID + ":" + strconv.FormatInt(in.Timestamp, 10)
}

// Answered says whether the API may answer the event: a message or a button from the person, while the app owns
// the thread. An echo, a referral alone or a standby event is only kept.
func (in Inbound) Answered() bool {
	return !in.Standby && (in.Kind == "message" || in.Kind == "postback")
}

// Skipped counts the events Events leaves out, by why.
type Skipped struct {
	OwnEchoes int // the app's own messages coming back
	Other     int // read, delivery, reaction and anything else we don't use
}

// Events turns a payload into the gateway's events, oldest first (Meta does not promise the order within a
// batch). The app's own messages coming back as echoes are left out (ourAppID); an echo from anyone else is a
// person writing from the Page's inbox, kept so the chat in the panel stays whole.
func Events(p Payload, ourAppID string) ([]Inbound, Skipped) {
	var out []Inbound
	var skipped Skipped
	for _, e := range p.Entry {
		for _, ev := range e.Messaging {
			if in, ok := event(e.ID, ev, ourAppID, false, &skipped); ok {
				out = append(out, in)
			}
		}
		for _, ev := range e.Standby {
			if in, ok := event(e.ID, ev, ourAppID, true, &skipped); ok {
				out = append(out, in)
			}
		}
	}
	sort.SliceStable(out, func(i, j int) bool { return out[i].Timestamp < out[j].Timestamp })
	return out, skipped
}

func event(pageID string, ev Event, ourAppID string, standby bool, skipped *Skipped) (Inbound, bool) {
	in := Inbound{PageID: pageID, PSID: ev.Sender.ID, Timestamp: ev.Timestamp, Standby: standby}
	switch {
	case ev.Message != nil && ev.Message.IsEcho:
		if ourAppID != "" && ev.Message.AppID.String() == ourAppID {
			skipped.OwnEchoes++
			return Inbound{}, false
		}
		// The Page wrote it: the person is the recipient.
		in.Kind, in.PSID, in.MID, in.Text = "echo", ev.Recipient.ID, ev.Message.MID, ev.Message.Text
		in.Attachments = attachmentTypes(ev.Message.Attachments)
	case ev.Message != nil:
		in.Kind, in.MID, in.Text = "message", ev.Message.MID, ev.Message.Text
		if ev.Message.QuickReply != nil {
			in.QuickReply = ev.Message.QuickReply.Payload
		}
		in.Attachments = attachmentTypes(ev.Message.Attachments)
		in.Referral = ev.Referral
	case ev.Postback != nil:
		in.Kind, in.MID = "postback", ev.Postback.MID
		in.Postback, in.PostbackTitle = ev.Postback.Payload, ev.Postback.Title
		in.Referral = ev.Postback.Referral
	case ev.Referral != nil:
		in.Kind, in.Referral = "referral", ev.Referral
	default:
		skipped.Other++
		return Inbound{}, false
	}
	if in.PSID == "" || in.PageID == "" {
		skipped.Other++
		return Inbound{}, false
	}
	return in, true
}

func attachmentTypes(as []Attachment) []string {
	var out []string
	for _, a := range as {
		if a.Type != "" {
			out = append(out, a.Type)
		}
	}
	return out
}
