// Package token reads the two tokens bbc-live is shown (contracts/live.md):
//
//	v1.<visitor_id>.<iat>.<exp>.<hex hmac>   the widget's visitor token, issued by the API
//	                                         (app/security/visitor_token.py), VISITOR_TOKEN_SECRET
//	l1.<payload>.<hex hmac>                  an agent's live token, issued by the API's heartbeat
//	                                         (app/services/live_tokens.py), LIVE_TOKEN_SECRET
//
// bbc-live never issues either: the API is the only authority on who is who.
package token

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"strconv"
	"strings"
	"time"
)

var (
	ErrMissing = errors.New("token: missing")
	ErrInvalid = errors.New("token: invalid")
	ErrExpired = errors.New("token: expired")
	ErrNoKey   = errors.New("token: no secret configured")
)

func sign(secret []byte, body string) string {
	m := hmac.New(sha256.New, secret)
	_, _ = m.Write([]byte(body)) // a hash never fails to write
	return hex.EncodeToString(m.Sum(nil))
}

func signedBy(secret []byte, body, sig string) bool {
	return hmac.Equal([]byte(sig), []byte(sign(secret, body)))
}

// Visitor reads a widget's visitor token and returns its visitor id.
func Visitor(tok string, secret []byte, now time.Time) (string, error) {
	if len(secret) == 0 {
		return "", ErrNoKey
	}
	if tok == "" {
		return "", ErrMissing
	}
	parts := strings.Split(tok, ".")
	if len(parts) != 5 || parts[0] != "v1" || parts[1] == "" {
		return "", ErrInvalid
	}
	if !signedBy(secret, strings.Join(parts[:4], "."), parts[4]) {
		return "", ErrInvalid
	}
	if _, err := strconv.ParseInt(parts[2], 10, 64); err != nil {
		return "", ErrInvalid
	}
	exp, err := strconv.ParseInt(parts[3], 10, 64)
	if err != nil {
		return "", ErrInvalid
	}
	if now.Unix() > exp {
		return "", ErrExpired
	}
	return parts[1], nil
}

// Agent is who the panel says is asking, signed by the API.
type Agent struct {
	User     string `json:"u"`
	Role     string `json:"r"`
	Tunnel   string `json:"t"`
	IssuedAt int64  `json:"iat"`
	Expires  int64  `json:"exp"`
}

// ReadAgent reads an agent's live token.
func ReadAgent(tok string, secret []byte, now time.Time) (Agent, error) {
	if len(secret) == 0 {
		return Agent{}, ErrNoKey
	}
	if tok == "" {
		return Agent{}, ErrMissing
	}
	parts := strings.Split(tok, ".")
	if len(parts) != 3 || parts[0] != "l1" {
		return Agent{}, ErrInvalid
	}
	if !signedBy(secret, parts[0]+"."+parts[1], parts[2]) {
		return Agent{}, ErrInvalid
	}
	raw, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return Agent{}, ErrInvalid
	}
	var a Agent
	if err := json.Unmarshal(raw, &a); err != nil || a.User == "" || a.Role == "" || a.Expires == 0 {
		return Agent{}, ErrInvalid
	}
	if now.Unix() > a.Expires {
		return Agent{}, ErrExpired
	}
	return a, nil
}

// IssueAgentForTests makes an agent token the way the API does (sorted keys, no spaces). Only tests
// use it: in production the API alone issues tokens.
func IssueAgentForTests(a Agent, secret []byte) string {
	// encoding/json writes struct fields in declaration order; the API sorts keys. The payload is
	// built by hand so both produce the same bytes (contracts/live_tokens.json checks it).
	body := `{"exp":` + strconv.FormatInt(a.Expires, 10) + `,"iat":` + strconv.FormatInt(a.IssuedAt, 10) +
		`,"r":` + quote(a.Role) + `,"t":` + quote(a.Tunnel) + `,"u":` + quote(a.User) + `}`
	p := base64.RawURLEncoding.EncodeToString([]byte(body))
	return "l1." + p + "." + sign(secret, "l1."+p)
}

// IssueVisitorForTests makes a visitor token the way the API does.
func IssueVisitorForTests(visitorID string, iat, exp int64, secret []byte) string {
	body := "v1." + visitorID + "." + strconv.FormatInt(iat, 10) + "." + strconv.FormatInt(exp, 10)
	return body + "." + sign(secret, body)
}

func quote(s string) string {
	b, _ := json.Marshal(s)
	return string(b)
}
