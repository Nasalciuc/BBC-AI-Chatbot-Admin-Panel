package meta

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptrace"
	"strings"
	"sync/atomic"
	"unicode/utf8"
)

// MaxText is the most characters one Messenger message carries; a longer reply goes as several.
const MaxText = 2000

// MaxQuickReplies and MaxTitle are Meta's limits for quick replies (13 a message, 20 characters a title).
const (
	MaxQuickReplies = 13
	MaxTitle        = 20
)

// Reply is one message to the person. Quick replies ride on it: text ones carry a payload the API reads back
// (a button's kind), user_phone_number and user_email offer the person's own contact from their profile in one tap.
type Reply struct {
	Text         string          `json:"text"`
	QuickReplies []QuickReplyOut `json:"quick_replies,omitempty"`
}

type QuickReplyOut struct {
	ContentType string `json:"content_type"` // text, user_phone_number, user_email
	Title       string `json:"title,omitempty"`
	Payload     string `json:"payload,omitempty"`
}

// Client is the Send API for one or more Pages (the token says which).
type Client struct {
	HTTP    *http.Client
	Base    string // https://graph.facebook.com
	Version string // v26.0
}

// Error is the Send API refusing a call, with the kind the gateway acts on (Kind).
type Error struct {
	Status  int
	Code    int
	Subcode int
	Message string
}

func (e *Error) Error() string {
	return fmt.Sprintf("send api %d: code %d/%d: %s", e.Status, e.Code, e.Subcode, e.Message)
}

// Kind sorts a refusal by what can be done about it:
//
//	window_closed  the person last wrote more than 24 hours ago: no reply until they write again
//	unavailable    the person can't be reached (blocked the Page, deleted their account, …)
//	thread_owned   another app (the Page Inbox) owns the thread now (conversation routing)
//	token          the Page's token is wrong or expired: nothing goes out until it is replaced
//	rate           too many calls (a Graph code, or HTTP 429 alone): later
//	temporary      Meta's own trouble (5xx, code 1 or 2): later
//	other          anything else
func (e *Error) Kind() string {
	switch {
	case e.Code == 10 && (e.Subcode == 2018278 || e.Subcode == 2534022):
		return "window_closed"
	case e.Code == 551 || (e.Code == 200 && e.Subcode == 1545041) || (e.Code == 10 && e.Subcode == 2018108):
		return "unavailable"
	case (e.Code == 10 && e.Subcode == 2018300) || (e.Code == 100 && e.Subcode == 2534037) || e.Subcode == 2018321:
		return "thread_owned"
	case e.Code == 190:
		return "token"
	case e.Code == 4 || e.Code == 613 || e.Subcode == 2534040 || e.Status == http.StatusTooManyRequests:
		return "rate"
	case e.Status >= 500 || e.Code == 1 || e.Code == 2:
		return "temporary"
	}
	return "other"
}

// Retry says whether the same call may succeed later.
func (e *Error) Retry() bool {
	k := e.Kind()
	return k == "rate" || k == "temporary"
}

// Unconfirmed is a call whose answer never came after Meta had the whole request (the connection dropped, the
// wait ran out): the message may be with the person. It is never sent again (the Send API takes no key that would
// make a second send the same message), and the sender is told it is not confirmed (kind "unknown").
type Unconfirmed struct{ Err error }

func (e *Unconfirmed) Error() string {
	return "send api: no answer once the request was out: " + e.Err.Error()
}
func (e *Unconfirmed) Unwrap() error { return e.Err }

// KindOf is the kind of any error a call returned: "unknown" for one Meta may have carried out, "temporary" for a
// network failure before the request went out.
func KindOf(err error) string {
	var ue *Unconfirmed
	if errors.As(err, &ue) {
		return "unknown"
	}
	var se *Error
	if errors.As(err, &se) {
		return se.Kind()
	}
	return "temporary"
}

// Retryable is whether any error a call returned may pass on a later try without sending twice: a refusal Meta
// may get over, or a failure before the request went out. Never an Unconfirmed one.
func Retryable(err error) bool {
	var ue *Unconfirmed
	if errors.As(err, &ue) {
		return false
	}
	var se *Error
	if errors.As(err, &se) {
		return se.Retry()
	}
	return err != nil && !errors.Is(err, context.Canceled)
}

// Send sends one message as a reply (messaging_type RESPONSE), or with a tag when one is given (MESSAGE_TAG:
// HUMAN_AGENT, after Meta approved the feature). It answers Meta's message id.
func (c *Client) Send(ctx context.Context, token, psid string, r Reply, tag string) (string, error) {
	body := map[string]any{"recipient": map[string]string{"id": psid}, "message": r}
	if tag != "" {
		body["messaging_type"], body["tag"] = "MESSAGE_TAG", tag
	} else {
		body["messaging_type"] = "RESPONSE"
	}
	var out struct {
		MessageID string `json:"message_id"`
	}
	if err := c.call(ctx, token, body, &out); err != nil {
		return "", err
	}
	return out.MessageID, nil
}

// Action shows the person what the Page is doing: mark_seen, typing_on, typing_off.
func (c *Client) Action(ctx context.Context, token, psid, action string) error {
	return c.call(ctx, token, map[string]any{"recipient": map[string]string{"id": psid}, "sender_action": action}, nil)
}

func (c *Client) call(ctx context.Context, token string, body any, out any) error {
	raw, err := json.Marshal(body)
	if err != nil {
		return err
	}
	// Whether Meta has had the whole request: a failure after that may still be a message sent.
	var wrote atomic.Bool
	ctx = httptrace.WithClientTrace(ctx, &httptrace.ClientTrace{
		WroteRequest: func(info httptrace.WroteRequestInfo) {
			if info.Err == nil {
				wrote.Store(true)
			}
		},
	})
	url := strings.TrimRight(c.Base, "/") + "/" + c.Version + "/me/messages"
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(raw))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	// The token in a header, never in the address: an address ends up in logs.
	req.Header.Set("Authorization", "Bearer "+token)
	res, err := c.HTTP.Do(req)
	if err != nil {
		if wrote.Load() {
			return &Unconfirmed{Err: err}
		}
		return err
	}
	defer func() { _ = res.Body.Close() }()
	data, readErr := io.ReadAll(io.LimitReader(res.Body, 64<<10))
	if res.StatusCode/100 == 2 {
		// Meta took it: an answer cut short or not JSON is still a message sent, never one to send again.
		if out != nil && readErr == nil {
			_ = json.Unmarshal(data, out) // the message id is for the logs only
		}
		return nil
	}
	var e struct {
		Error struct {
			Message string `json:"message"`
			Code    int    `json:"code"`
			Subcode int    `json:"error_subcode"`
		} `json:"error"`
	}
	if readErr == nil {
		_ = json.Unmarshal(data, &e) // a body that is not Meta's error shape leaves only the status
	}
	return &Error{Status: res.StatusCode, Code: e.Error.Code, Subcode: e.Error.Subcode, Message: e.Error.Message}
}

// Split cuts a text into messages of at most MaxText characters, at a paragraph, a line, a sentence or a space when
// one is near the end, so a long answer arrives as several readable messages. Never an empty one.
func Split(text string) []string {
	text = strings.TrimSpace(text)
	var out []string
	for utf8.RuneCountInString(text) > MaxText {
		cut := cutAt(text)
		out = append(out, strings.TrimSpace(text[:cut]))
		text = strings.TrimSpace(text[cut:])
	}
	if text != "" {
		out = append(out, text)
	}
	return out
}

// cutAt is the byte where the first message ends: within MaxText characters, at the last good break in its second
// half, else at MaxText characters.
func cutAt(text string) int {
	n, limit := 0, len(text)
	for i := range text {
		if n == MaxText {
			limit = i // the first MaxText characters are text[:i]
			break
		}
		n++
	}
	head := text[:limit]
	for _, sep := range []string{"\n\n", "\n", ". ", "! ", "? ", " "} {
		if i := strings.LastIndex(head, sep); i > len(head)/2 {
			return i + len(sep)
		}
	}
	return limit
}

// Clip cuts a quick reply's title to Meta's 20 characters.
func Clip(title string) string {
	if utf8.RuneCountInString(title) <= MaxTitle {
		return title
	}
	r := []rune(title)
	return string(r[:MaxTitle-1]) + "…"
}
