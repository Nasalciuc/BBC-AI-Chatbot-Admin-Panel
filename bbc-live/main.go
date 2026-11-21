// bbc-live: who is on the site now, and an agent's hello before the visitor writes (contracts/live.md).
//
// It holds no database and no secrets of its own: Redis is its only store (coordination, never the
// truth), and the API issues every token it reads. Environment:
//
//	PORT                  where to listen (Railway sets it); 8080 otherwise
//	REDIS_URL             redis:// or rediss://, the same Redis as the API
//	VISITOR_TOKEN_SECRET  the API's, to read the widget's visitor token
//	LIVE_TOKEN_SECRET     the API's, to read the panel's live token
//	ALLOWED_ORIGINS       the sites and the panel, comma-separated (https://www.example.com,...)
package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/Nasalciuc/BBC-AI-Chatbot-Admin-Panel/bbc-live/internal/live"
	"github.com/Nasalciuc/BBC-AI-Chatbot-Admin-Panel/bbc-live/internal/resp"
)

func origins(raw string) map[string]bool {
	out := map[string]bool{}
	for _, o := range strings.Split(raw, ",") {
		if o = strings.TrimRight(strings.TrimSpace(o), "/"); o != "" {
			out[o] = true
		}
	}
	return out
}

func main() {
	log := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))
	if err := run(log); err != nil {
		log.Error("bbc-live stopped", "error", err.Error())
		os.Exit(1)
	}
}

func run(log *slog.Logger) error {
	opts, err := resp.ParseURL(os.Getenv("REDIS_URL"))
	if err != nil {
		return err
	}
	rc := resp.New(opts)
	defer func() { _ = rc.Close() }()

	srv := &live.Server{
		Store:         &live.Store{R: rc},
		Hub:           live.NewHub(),
		VisitorSecret: []byte(os.Getenv("VISITOR_TOKEN_SECRET")),
		AgentSecret:   []byte(os.Getenv("LIVE_TOKEN_SECRET")),
		Origins:       origins(os.Getenv("ALLOWED_ORIGINS")),
		Log:           log,
		PollWait:      25 * time.Second,
		Recheck:       5 * time.Second,
		MinBeatGap:    5 * time.Second,
	}
	if len(srv.VisitorSecret) == 0 || len(srv.AgentSecret) == 0 {
		log.Warn("a token secret is missing: the widget and panel routes answer 503 until it is set")
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()
	go srv.Hub.Listen(ctx, rc, log)
	go func() {
		t := time.NewTicker(time.Minute)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				srv.ForgetOldBeats()
			}
		}
	}()

	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}
	hs := &http.Server{
		Addr:              ":" + port,
		Handler:           srv.Handler(),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      40 * time.Second, // the inbox poll waits 25 s
		IdleTimeout:       60 * time.Second,
		MaxHeaderBytes:    16 << 10,
	}
	errs := make(chan error, 1)
	go func() {
		log.Info("bbc-live listening", "port", port, "origins", len(srv.Origins))
		errs <- hs.ListenAndServe()
	}()
	select {
	case err := <-errs:
		if !errors.Is(err, http.ErrServerClosed) {
			return err
		}
		return nil
	case <-ctx.Done():
	}
	// SIGTERM (a deploy): the polls in flight answer, then the process ends.
	shutdown, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	return hs.Shutdown(shutdown)
}
