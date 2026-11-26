// bbc-messenger: Skye and the agents on Facebook Messenger, for the BuyBusinessClass and Business Class Tickets
// Pages (contracts/messenger.md).
//
// It keeps no data: Meta's events go to the API, which keeps the chat and decides the replies; the gateway checks
// Meta's signature, keeps each person's events in order and sends what the API answers. Environment (the secrets
// only here, set in Railway, never in code or chat):
//
//	PORT                      where to listen (Railway sets it); 8080 otherwise
//	META_APP_ID               the Meta app's id (its own echoes are left out)
//	META_APP_SECRET           the app secret: the webhook's signature (secret)
//	META_VERIFY_TOKEN         the webhook's verify token, as set in the app's webhook settings (secret)
//	META_PAGE_ID_BBC          the BuyBusinessClass Page's id
//	META_PAGE_TOKEN_BBC       its Page access token (secret)
//	META_PAGE_ID_BCT          the Business Class Tickets Page's id
//	META_PAGE_TOKEN_BCT       its Page access token (secret)
//	META_GRAPH_VERSION        the Graph API version; v26.0 otherwise
//	API_URL                   the API's address (https://…)
//	MESSENGER_GATEWAY_SECRET  shared with the API: every call between the two is signed with it (secret)
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

	"github.com/Nasalciuc/BBC-AI-Chatbot-Admin-Panel/bbc-messenger/internal/gateway"
	"github.com/Nasalciuc/BBC-AI-Chatbot-Admin-Panel/bbc-messenger/internal/meta"
)

// sites are the Pages the gateway may answer for, by the suffix of their variables.
var sites = []string{"bbc", "bct"}

func pages(getenv func(string) string) map[string]gateway.Page {
	out := map[string]gateway.Page{}
	for _, site := range sites {
		id := strings.TrimSpace(getenv("META_PAGE_ID_" + strings.ToUpper(site)))
		token := strings.TrimSpace(getenv("META_PAGE_TOKEN_" + strings.ToUpper(site)))
		if id != "" && token != "" {
			out[id] = gateway.Page{ID: id, Token: token, Site: site}
		}
	}
	return out
}

func main() {
	log := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))
	if err := run(log); err != nil {
		log.Error("bbc-messenger stopped", "error", err.Error())
		os.Exit(1)
	}
}

func run(log *slog.Logger) error {
	version := strings.TrimSpace(os.Getenv("META_GRAPH_VERSION"))
	if version == "" {
		version = "v26.0"
	}
	cfg := gateway.Config{
		AppSecret:     []byte(os.Getenv("META_APP_SECRET")),
		VerifyToken:   os.Getenv("META_VERIFY_TOKEN"),
		AppID:         strings.TrimSpace(os.Getenv("META_APP_ID")),
		Pages:         pages(os.Getenv),
		APIURL:        strings.TrimSpace(os.Getenv("API_URL")),
		GatewaySecret: []byte(os.Getenv("MESSENGER_GATEWAY_SECRET")),
		Workers:       32,
		QueueSize:     256,
		APITimeout:    75 * time.Second,
		Retries:       []time.Duration{2 * time.Second, 5 * time.Second, 15 * time.Second},
		// Meta unsubscribes the app after an hour of failed deliveries, so the webhook always answers at once; an
		// event the API can't take yet is kept and tried again that long.
		KeepFor:   time.Hour,
		LongRetry: time.Minute,
		// Below WriteTimeout, so /v1/send always answers what went out.
		SendBudget:  60 * time.Second,
		CallTimeout: 10 * time.Second,
		HoldFor:     15 * time.Minute,
	}
	srv := &gateway.Server{
		Cfg:  cfg,
		Meta: &meta.Client{HTTP: &http.Client{Timeout: cfg.CallTimeout}, Base: "https://graph.facebook.com", Version: version},
		API:  &http.Client{},
		Log:  log,
	}
	if !srv.Ready() {
		log.Warn("not configured yet: the webhook refuses Meta until every variable is set (/health says ready)")
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()
	work, cancelWork := context.WithCancel(context.Background())
	defer cancelWork()
	srv.Start(work)

	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}
	hs := &http.Server{
		Addr:              ":" + port,
		Handler:           srv.Handler(),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      75 * time.Second, // above SendBudget: an agent's send always gets its answer back
		IdleTimeout:       60 * time.Second,
		MaxHeaderBytes:    16 << 10,
	}
	errs := make(chan error, 1)
	go func() {
		log.Info("bbc-messenger listening", "port", port, "pages", len(cfg.Pages), "graph", version)
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
	// SIGTERM (a deploy): no new event from here (503: Meta delivers it to the next process). The queued events go
	// to the API while the HTTP server waits for the requests still running (an agent's send): both share the
	// grace period, so a long send never leaves the queues without time.
	shutdown, cancel := context.WithTimeout(context.Background(), 25*time.Second)
	defer cancel()
	srv.Close()
	drained := make(chan struct{})
	go func() {
		srv.Stop(shutdown)
		close(drained)
	}()
	err := hs.Shutdown(shutdown)
	<-drained
	return err
}
