package token_test

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/Nasalciuc/BBC-AI-Chatbot-Admin-Panel/bbc-live/internal/token"
)

// contracts/live_tokens.json: the API issued every token in it; bbc-live must read each the same way.
type vectors struct {
	Visitor struct {
		Secret string `json:"secret"`
		Now    int64  `json:"now"`
		Valid  []struct {
			Token     string `json:"token"`
			VisitorID string `json:"visitor_id"`
		} `json:"valid"`
		Invalid []struct {
			Token  string `json:"token"`
			Reason string `json:"reason"`
		} `json:"invalid"`
	} `json:"visitor"`
	Agent struct {
		Secret string `json:"secret"`
		Now    int64  `json:"now"`
		Valid  []struct {
			Token  string `json:"token"`
			User   string `json:"user"`
			Role   string `json:"role"`
			Tunnel string `json:"tunnel"`
			Exp    int64  `json:"exp"`
		} `json:"valid"`
		Invalid []struct {
			Token  string `json:"token"`
			Reason string `json:"reason"`
		} `json:"invalid"`
	} `json:"agent"`
}

func load(t *testing.T) vectors {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "contracts", "live_tokens.json"))
	if err != nil {
		t.Fatalf("the shared vectors: %v", err)
	}
	var v vectors
	if err := json.Unmarshal(raw, &v); err != nil {
		t.Fatal(err)
	}
	return v
}

func reason(err error) string {
	switch {
	case err == nil:
		return "ok"
	case errors.Is(err, token.ErrMissing):
		return "missing"
	case errors.Is(err, token.ErrExpired):
		return "expired"
	case errors.Is(err, token.ErrInvalid):
		return "invalid"
	}
	return err.Error()
}

func TestTheVisitorTokensTheAPIIssued(t *testing.T) {
	v := load(t)
	now := time.Unix(v.Visitor.Now, 0)
	for _, c := range v.Visitor.Valid {
		vid, err := token.Visitor(c.Token, []byte(v.Visitor.Secret), now)
		if err != nil || vid != c.VisitorID {
			t.Errorf("%s: got %q %v", c.Token, vid, err)
		}
	}
	for _, c := range v.Visitor.Invalid {
		if _, err := token.Visitor(c.Token, []byte(v.Visitor.Secret), now); reason(err) != c.Reason {
			t.Errorf("%q: got %s, want %s", c.Token, reason(err), c.Reason)
		}
	}
}

func TestTheAgentTokensTheAPIIssued(t *testing.T) {
	v := load(t)
	now := time.Unix(v.Agent.Now, 0)
	for _, c := range v.Agent.Valid {
		a, err := token.ReadAgent(c.Token, []byte(v.Agent.Secret), now)
		if err != nil || a.User != c.User || a.Role != c.Role || a.Tunnel != c.Tunnel || a.Expires != c.Exp {
			t.Errorf("%s: got %+v %v", c.Token, a, err)
		}
		// The Go test issuer writes the same bytes as the API.
		if again := token.IssueAgentForTests(a, []byte(v.Agent.Secret)); again != c.Token {
			t.Errorf("issued %s, the API issued %s", again, c.Token)
		}
	}
	for _, c := range v.Agent.Invalid {
		if _, err := token.ReadAgent(c.Token, []byte(v.Agent.Secret), now); reason(err) != c.Reason {
			t.Errorf("%q: got %s, want %s", c.Token, reason(err), c.Reason)
		}
	}
}

func TestNoSecretReadsNothing(t *testing.T) {
	v := load(t)
	if _, err := token.Visitor(v.Visitor.Valid[0].Token, nil, time.Unix(v.Visitor.Now, 0)); !errors.Is(err, token.ErrNoKey) {
		t.Fatalf("visitor without a secret: %v", err)
	}
	if _, err := token.ReadAgent(v.Agent.Valid[0].Token, nil, time.Unix(v.Agent.Now, 0)); !errors.Is(err, token.ErrNoKey) {
		t.Fatalf("agent without a secret: %v", err)
	}
}

func TestAVisitorTokenIsNotAnAgentToken(t *testing.T) {
	v := load(t)
	if _, err := token.ReadAgent(v.Visitor.Valid[0].Token, []byte(v.Agent.Secret), time.Unix(v.Agent.Now, 0)); !errors.Is(err, token.ErrInvalid) {
		t.Fatalf("got %v", err)
	}
	if _, err := token.Visitor(v.Agent.Valid[0].Token, []byte(v.Visitor.Secret), time.Unix(v.Visitor.Now, 0)); !errors.Is(err, token.ErrInvalid) {
		t.Fatalf("got %v", err)
	}
}
