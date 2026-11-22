// Package redistest gives the tests a real Redis: REDIS_TEST_URL when it is set (CI runs a redis
// service), otherwise a redis-server started on a free local port for the test binary. Without
// either, the tests that need Redis skip, unless LIVE_TEST_REQUIRE_REDIS=1 (CI), where they fail.
package redistest

import (
	"context"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/Nasalciuc/BBC-AI-Chatbot-Admin-Panel/bbc-live/internal/resp"
)

var (
	once    sync.Once
	url     string
	server  *exec.Cmd
	dataDir string
	reason  string
)

func start() {
	if u := os.Getenv("REDIS_TEST_URL"); u != "" {
		url = u
		return
	}
	bin, err := exec.LookPath("redis-server")
	if err != nil {
		reason = "no redis-server on PATH and no REDIS_TEST_URL"
		return
	}
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		reason = "no free port: " + err.Error()
		return
	}
	port := l.Addr().(*net.TCPAddr).Port
	_ = l.Close()
	dataDir, err = os.MkdirTemp("", "bbc-live-redis-")
	if err != nil {
		reason = "no temp dir: " + err.Error()
		return
	}
	server = exec.Command(bin, "--port", fmt.Sprint(port), "--bind", "127.0.0.1", "--save", "",
		"--appendonly", "no", "--dir", dataDir, "--logfile", filepath.Join(dataDir, "redis.log"))
	if err := server.Start(); err != nil {
		reason = "redis-server did not start: " + err.Error()
		return
	}
	url = fmt.Sprintf("redis://127.0.0.1:%d/0", port)
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		if c, err := net.DialTimeout("tcp", fmt.Sprintf("127.0.0.1:%d", port), 100*time.Millisecond); err == nil {
			_ = c.Close()
			return
		}
		time.Sleep(50 * time.Millisecond)
	}
	reason = "redis-server did not listen in 5 s"
	url = ""
}

// URL is a Redis for this test, emptied first. It skips (or fails, in CI) when there is none.
func URL(t testing.TB) string {
	t.Helper()
	once.Do(start)
	if url == "" {
		if os.Getenv("LIVE_TEST_REQUIRE_REDIS") == "1" {
			t.Fatalf("redis needed: %s", reason)
		}
		t.Skipf("redis needed: %s", reason)
	}
	o, err := resp.ParseURL(url)
	if err != nil {
		t.Fatalf("redis url: %v", err)
	}
	c := resp.New(o)
	defer func() { _ = c.Close() }()
	if _, err := c.Do(context.Background(), "FLUSHDB"); err != nil {
		t.Fatalf("flushdb: %v", err)
	}
	return url
}

// Stop ends the redis-server this package started (call it from TestMain after m.Run).
func Stop() {
	if server != nil && server.Process != nil {
		_ = server.Process.Kill()
		_, _ = server.Process.Wait()
	}
	if dataDir != "" {
		_ = os.RemoveAll(dataDir)
	}
}
