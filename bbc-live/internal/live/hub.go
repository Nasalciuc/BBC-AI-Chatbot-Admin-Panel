package live

import (
	"context"
	"log/slog"
	"sync"
	"sync/atomic"
	"time"

	"github.com/Nasalciuc/BBC-AI-Chatbot-Admin-Panel/bbc-live/internal/resp"
)

// Hub wakes the widgets waiting on GET /v1/inbox when the API publishes their visitor id on
// live:inbox. A missed message costs at most the poll's own recheck (Recheck): the waiter looks at
// the inbox again on its own, so pub/sub only makes the hello arrive at once.
type Hub struct {
	mu      sync.Mutex
	waiters map[string]map[*waiter]struct{}
	count   atomic.Int64

	Reconnects atomic.Int64
	Wakes      atomic.Int64
}

type waiter struct{ ch chan struct{} }

const (
	MaxWaitersPerVisitor = 3    // tabs of one visitor
	MaxWaiters           = 5000 // in this process
)

func NewHub() *Hub { return &Hub{waiters: map[string]map[*waiter]struct{}{}} }

// Wait registers a waiter for the visitor; ok is false when there are too many. Call done when finished.
func (h *Hub) Wait(vid string) (wake <-chan struct{}, done func(), ok bool) {
	h.mu.Lock()
	defer h.mu.Unlock()
	set := h.waiters[vid]
	if len(set) >= MaxWaitersPerVisitor || h.count.Load() >= MaxWaiters {
		return nil, func() {}, false
	}
	if set == nil {
		set = map[*waiter]struct{}{}
		h.waiters[vid] = set
	}
	w := &waiter{ch: make(chan struct{}, 1)}
	set[w] = struct{}{}
	h.count.Add(1)
	var once sync.Once
	return w.ch, func() {
		once.Do(func() {
			h.mu.Lock()
			defer h.mu.Unlock()
			if s := h.waiters[vid]; s != nil {
				delete(s, w)
				if len(s) == 0 {
					delete(h.waiters, vid)
				}
			}
			h.count.Add(-1)
		})
	}, true
}

// Wake tells every waiter of the visitor to look at their inbox.
func (h *Hub) Wake(vid string) {
	h.mu.Lock()
	defer h.mu.Unlock()
	for w := range h.waiters[vid] {
		select {
		case w.ch <- struct{}{}:
		default:
		}
	}
	h.Wakes.Add(1)
}

// Waiting is how many polls wait now.
func (h *Hub) Waiting() int64 { return h.count.Load() }

// Listen keeps one subscription to live:inbox until ctx ends, reconnecting with a growing pause.
func (h *Hub) Listen(ctx context.Context, r *resp.Client, log *slog.Logger) {
	pause := 250 * time.Millisecond
	for ctx.Err() == nil {
		started := time.Now()
		err := r.Subscribe(ctx, InboxChannel, 30*time.Second, h.Wake)
		if ctx.Err() != nil {
			return
		}
		h.Reconnects.Add(1)
		if time.Since(started) > time.Minute {
			pause = 250 * time.Millisecond
		}
		log.Warn("live:inbox subscription ended, listening again", "error", errString(err), "in", pause.String())
		select {
		case <-ctx.Done():
			return
		case <-time.After(pause):
		}
		if pause < 10*time.Second {
			pause *= 2
		}
	}
}

func errString(err error) string {
	if err == nil {
		return ""
	}
	return err.Error()
}
