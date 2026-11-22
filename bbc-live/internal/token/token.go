// Package token reads the two tokens bbc-live is shown (contracts/live.md), both signed by the API with
// LIVE_TOKEN_SECRET (app/services/live_tokens.py):
//
//	b1.<payload>.<hex hmac>   a visitor's pass, from POST /chat/live: {"exp","iat","v"}
//	l1.<payload>.<hex hmac>   an agent's live token, from the API's heartbeat: {"exp","iat","r","t","u"}
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

// read checks a "<prefix>.<payload>.<hmac>" token and decodes its payload into v.
func read(tok, prefix string, secret []byte, v any) error {
	if len(secret) == 0 {
		return ErrNoKey
	}
	if tok == "" {
		return ErrMissing
	}
	parts := strings.Split(tok, ".")
	if len(parts) != 3 || parts[0] != prefix {
		return ErrInvalid
	}
	if !signedBy(secret, parts[0]+"."+parts[1], parts[2]) {
		return ErrInvalid
	}
	raw, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return ErrInvalid
	}
	if err := json.Unmarshal(raw, v); err != nil {
		return ErrInvalid
	}
	return nil
}

// Pass is a visitor's pass to bbc-live, signed by the API.
type Pass struct {
	Visitor  string `json:"v"`
	IssuedAt int64  `json:"iat"`
	Expires  int64  `json:"exp"`
}

// ReadPass reads a widget's pass and returns its visitor id.
func ReadPass(tok string, secret []byte, now time.Time) (string, error) {
	var p Pass
	if err := read(tok, "b1", secret, &p); err != nil {
		return "", err
	}
	if p.Visitor == "" || p.Expires == 0 {
		return "", ErrInvalid
	}
	if now.Unix() > p.Expires {
		return "", ErrExpired
	}
	return p.Visitor, nil
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
	var a Agent
	if err := read(tok, "l1", secret, &a); err != nil {
		return Agent{}, err
	}
	if a.User == "" || a.Role == "" || a.Expires == 0 {
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

// IssuePassForTests makes a visitor's pass the way the API does (sorted keys, no spaces).
func IssuePassForTests(visitorID string, iat, exp int64, secret []byte) string {
	body := `{"exp":` + strconv.FormatInt(exp, 10) + `,"iat":` + strconv.FormatInt(iat, 10) + `,"v":` + quote(visitorID) + `}`
	p := base64.RawURLEncoding.EncodeToString([]byte(body))
	return "b1." + p + "." + sign(secret, "b1."+p)
}

func quote(s string) string {
	b, _ := json.Marshal(s)
	return string(b)
}
