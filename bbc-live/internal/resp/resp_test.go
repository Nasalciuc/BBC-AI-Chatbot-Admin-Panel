package resp_test

import (
	"context"
	"errors"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/Nasalciuc/BBC-AI-Chatbot-Admin-Panel/bbc-live/internal/redistest"
	"github.com/Nasalciuc/BBC-AI-Chatbot-Admin-Panel/bbc-live/internal/resp"
)

func TestMain(m *testing.M) {
	code := m.Run()
	redistest.Stop()
	os.Exit(code)
}

func client(t *testing.T) *resp.Client {
	t.Helper()
	o, err := resp.ParseURL(redistest.URL(t))
	if err != nil {
		t.Fatal(err)
	}
	c := resp.New(o)
	t.Cleanup(func() { _ = c.Close() })
	return c
}

func TestParseURL(t *testing.T) {
	cases := []struct {
		in   string
		want resp.Options
		bad  bool
	}{
		{in: "redis://localhost", want: resp.Options{Addr: "localhost:6379"}},
		{in: "redis://default:s3cret@redis.railway.internal:6380/2",
			want: resp.Options{Addr: "redis.railway.internal:6380", Password: "s3cret", DB: 2}},
		{in: "rediss://alice:pw@cache.example.test", want: resp.Options{Addr: "cache.example.test:6379",
			Username: "alice", Password: "pw", TLS: true}},
		{in: "http://localhost", bad: true},
		{in: "redis:///0", bad: true},
		{in: "redis://localhost/x", bad: true},
	}
	for _, c := range cases {
		got, err := resp.ParseURL(c.in)
		if c.bad {
			if err == nil {
				t.Errorf("%s: want an error", c.in)
			}
			continue
		}
		if err != nil {
			t.Fatalf("%s: %v", c.in, err)
		}
		if got.Addr != c.want.Addr || got.Username != c.want.Username || got.Password != c.want.Password ||
			got.DB != c.want.DB || got.TLS != c.want.TLS {
			t.Errorf("%s: got %+v, want %+v", c.in, got, c.want)
		}
	}
}

func TestCommandsAndReplies(t *testing.T) {
	c := client(t)
	ctx := context.Background()
	if v, err := resp.Str(c.Do(ctx, "SET", "k", "a value with\r\nnewlines")); err != nil || v != "OK" {
		t.Fatalf("set: %q %v", v, err)
	}
	if v, err := resp.Str(c.Do(ctx, "GET", "k")); err != nil || v != "a value with\r\nnewlines" {
		t.Fatalf("get: %q %v", v, err)
	}
	if _, err := resp.Str(c.Do(ctx, "GET", "missing")); !errors.Is(err, resp.ErrNil) {
		t.Fatalf("a missing key is ErrNil, got %v", err)
	}
	if n, err := resp.Int(c.Do(ctx, "INCR", "n")); err != nil || n != 1 {
		t.Fatalf("incr: %d %v", n, err)
	}
	if _, err := c.Do(ctx, "RPUSH", "l", "a", "b", "c"); err != nil {
		t.Fatal(err)
	}
	if got, err := resp.Strings(c.Do(ctx, "LRANGE", "l", "0", "-1")); err != nil || strings.Join(got, ",") != "a,b,c" {
		t.Fatalf("lrange: %v %v", got, err)
	}
	var replyErr resp.Error
	if _, err := c.Do(ctx, "LPUSH", "k", "x"); !errors.As(err, &replyErr) || !strings.Contains(err.Error(), "WRONGTYPE") {
		t.Fatalf("a wrong type is an error reply, got %v", err)
	}
	// The connection that carried the error reply still works.
	if _, err := c.Do(ctx, "PING"); err != nil {
		t.Fatal(err)
	}
	script := "return {KEYS[1], ARGV[1], tonumber(ARGV[2]) + 1}"
	got, err := c.Do(ctx, "EVAL", script, "1", "key", "arg", "41")
	if err != nil || len(got.Array) != 3 || got.Array[0].Str != "key" || got.Array[2].Int != 42 {
		t.Fatalf("eval: %+v %v", got, err)
	}
}

func TestThePoolHoldsUnderConcurrency(t *testing.T) {
	c := client(t)
	ctx := context.Background()
	var wg sync.WaitGroup
	errs := make(chan error, 200)
	for i := 0; i < 200; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if _, err := c.Do(ctx, "INCR", "hits"); err != nil {
				errs <- err
			}
		}()
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		t.Fatal(err)
	}
	if n, err := resp.Int(c.Do(ctx, "GET", "hits")); err != nil || n != 200 {
		t.Fatalf("hits = %d %v", n, err)
	}
}

func TestSubscribeGetsWhatIsPublished(t *testing.T) {
	c := client(t)
	ctx, cancel := context.WithCancel(context.Background())
	got := make(chan string, 4)
	done := make(chan error, 1)
	go func() { done <- c.Subscribe(ctx, "live:test", 50*time.Millisecond, func(m string) { got <- m }) }()
	deadline := time.Now().Add(3 * time.Second)
	for {
		n, err := resp.Int(c.Do(context.Background(), "PUBLISH", "live:test", "hello"))
		if err != nil {
			t.Fatal(err)
		}
		if n == 1 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("the subscriber never subscribed")
		}
		time.Sleep(20 * time.Millisecond)
	}
	select {
	case m := <-got:
		if m != "hello" {
			t.Fatalf("got %q", m)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("no message")
	}
	time.Sleep(150 * time.Millisecond) // a few pings go by and are answered
	cancel()
	if err := <-done; !errors.Is(err, context.Canceled) {
		t.Fatalf("subscribe ended with %v, want context.Canceled", err)
	}
}

func TestADeadServerFailsFastAndAClosedClientRefuses(t *testing.T) {
	o, err := resp.ParseURL("redis://127.0.0.1:1")
	if err != nil {
		t.Fatal(err)
	}
	o.Timeout = 200 * time.Millisecond
	c := resp.New(o)
	start := time.Now()
	if _, err := c.Do(context.Background(), "PING"); err == nil {
		t.Fatal("want an error from a closed port")
	}
	if time.Since(start) > time.Second {
		t.Fatal("a dead Redis must cost milliseconds, not seconds")
	}
	_ = c.Close()
	if _, err := c.Do(context.Background(), "PING"); !errors.Is(err, resp.ErrClosed) {
		t.Fatalf("after Close: %v", err)
	}
}
