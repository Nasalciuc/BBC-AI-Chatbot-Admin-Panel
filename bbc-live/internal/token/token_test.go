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
	Pass struct {
		Secret string `json:"secret"`
		Now    int64  `json:"now"`
		Valid  []struct {
			Token     string `json:"token"`
			VisitorID string `json:"visitor_id"`
			Exp       int64  `json:"exp"`
		} `json:"valid"`
		Invalid []struct {
			Token  string `json:"token"`
			Reason string `json:"reason"`
		} `json:"invalid"`
	} `json:"pass"`
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

func TestThePassesTheAPIIssued(t *testing.T) {
	v := load(t)
	now := time.Unix(v.Pass.Now, 0)
	for _, c := range v.Pass.Valid {
		vid, err := token.ReadPass(c.Token, []byte(v.Pass.Secret), now)
		if err != nil || vid != c.VisitorID {
			t.Errorf("%s: got %q %v", c.Token, vid, err)
		}
		// The Go test issuer writes the same bytes as the API.
		if again := token.IssuePassForTests(c.VisitorID, c.Exp-86400, c.Exp, []byte(v.Pass.Secret)); again != c.Token {
			t.Errorf("issued %s, the API issued %s", again, c.Token)
		}
	}
	for _, c := range v.Pass.Invalid {
		if _, err := token.ReadPass(c.Token, []byte(v.Pass.Secret), now); reason(err) != c.Reason {
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
	if _, err := token.ReadPass(v.Pass.Valid[0].Token, nil, time.Unix(v.Pass.Now, 0)); !errors.Is(err, token.ErrNoKey) {
		t.Fatalf("pass without a secret: %v", err)
	}
	if _, err := token.ReadAgent(v.Agent.Valid[0].Token, nil, time.Unix(v.Agent.Now, 0)); !errors.Is(err, token.ErrNoKey) {
		t.Fatalf("agent without a secret: %v", err)
	}
}

// One secret signs both: the prefix is what keeps a visitor's pass from opening the panel's routes.
func TestAPassIsNotAnAgentToken(t *testing.T) {
	v := load(t)
	if v.Pass.Secret != v.Agent.Secret {
		t.Fatal("the vectors share one secret, as production does")
	}
	if _, err := token.ReadAgent(v.Pass.Valid[0].Token, []byte(v.Agent.Secret), time.Unix(v.Agent.Now, 0)); !errors.Is(err, token.ErrInvalid) {
		t.Fatalf("got %v", err)
	}
	if _, err := token.ReadPass(v.Agent.Valid[0].Token, []byte(v.Pass.Secret), time.Unix(v.Pass.Now, 0)); !errors.Is(err, token.ErrInvalid) {
		t.Fatalf("got %v", err)
	}
}
