// Package resp is the small Redis client bbc-live needs: RESP2 over TCP or TLS, a bounded pool of
// connections for commands, and one subscriber for a pub/sub channel.
//
// Why not a library: the service runs a dozen commands and four Lua scripts, all of it coordination
// (Redis is never the truth here). The standard library is the whole dependency tree; the tests run
// against a real redis-server, Lua and pub/sub included.
package resp

import (
	"bufio"
	"context"
	"crypto/tls"
	"errors"
	"fmt"
	"io"
	"net"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"
)

// Value is one RESP2 reply.
type Value struct {
	Kind  byte // '+' simple string, '-' error, ':' integer, '$' bulk string, '*' array
	Str   string
	Int   int64
	Array []Value
	Null  bool // a nil bulk string or a nil array
}

// Error is an error reply from Redis (WRONGTYPE, NOSCRIPT, ERR …). The connection is still good.
type Error string

func (e Error) Error() string { return "redis: " + string(e) }

// ErrNil is returned by the helpers for a nil reply.
var ErrNil = errors.New("redis: nil")

// ErrClosed is returned after Close.
var ErrClosed = errors.New("redis: client closed")

const maxBulk = 8 << 20 // nothing this service stores comes near; a larger length is a broken stream

// Options says where Redis is and how to talk to it.
type Options struct {
	Addr     string
	Username string
	Password string
	DB       int
	TLS      bool
	Timeout  time.Duration // per command, dial included
	PoolSize int
}

// ParseURL reads redis://[user:password@]host[:port][/db] or rediss://… (TLS).
func ParseURL(raw string) (Options, error) {
	u, err := url.Parse(raw)
	if err != nil {
		return Options{}, fmt.Errorf("redis url: %w", err)
	}
	o := Options{Timeout: time.Second, PoolSize: 16}
	switch u.Scheme {
	case "redis":
	case "rediss":
		o.TLS = true
	default:
		return Options{}, fmt.Errorf("redis url: scheme %q, want redis or rediss", u.Scheme)
	}
	host, port := u.Hostname(), u.Port()
	if host == "" {
		return Options{}, errors.New("redis url: no host")
	}
	if port == "" {
		port = "6379"
	}
	o.Addr = net.JoinHostPort(host, port)
	if u.User != nil {
		o.Username = u.User.Username()
		o.Password, _ = u.User.Password()
		if o.Username == "default" {
			o.Username = "" // AUTH <password> is the default user on every Redis version
		}
	}
	if p := strings.Trim(u.Path, "/"); p != "" {
		db, err := strconv.Atoi(p)
		if err != nil || db < 0 {
			return Options{}, fmt.Errorf("redis url: database %q", p)
		}
		o.DB = db
	}
	return o, nil
}

type conn struct {
	nc net.Conn
	br *bufio.Reader
	bw *bufio.Writer
}

func dial(ctx context.Context, o Options) (*conn, error) {
	d := net.Dialer{Timeout: o.Timeout}
	var nc net.Conn
	var err error
	if o.TLS {
		host, _, _ := net.SplitHostPort(o.Addr)
		td := tls.Dialer{NetDialer: &d, Config: &tls.Config{ServerName: host, MinVersion: tls.VersionTLS12}}
		nc, err = td.DialContext(ctx, "tcp", o.Addr)
	} else {
		nc, err = d.DialContext(ctx, "tcp", o.Addr)
	}
	if err != nil {
		return nil, err
	}
	c := &conn{nc: nc, br: bufio.NewReader(nc), bw: bufio.NewWriter(nc)}
	if o.Password != "" {
		args := []string{"AUTH", o.Password}
		if o.Username != "" {
			args = []string{"AUTH", o.Username, o.Password}
		}
		if _, err := c.roundTrip(o.Timeout, args); err != nil {
			c.close()
			return nil, fmt.Errorf("redis auth: %w", err)
		}
	}
	if o.DB != 0 {
		if _, err := c.roundTrip(o.Timeout, []string{"SELECT", strconv.Itoa(o.DB)}); err != nil {
			c.close()
			return nil, fmt.Errorf("redis select: %w", err)
		}
	}
	return c, nil
}

func (c *conn) close() { _ = c.nc.Close() }

func (c *conn) write(args []string) error {
	if _, err := fmt.Fprintf(c.bw, "*%d\r\n", len(args)); err != nil {
		return err
	}
	for _, a := range args {
		if _, err := fmt.Fprintf(c.bw, "$%d\r\n%s\r\n", len(a), a); err != nil {
			return err
		}
	}
	return c.bw.Flush()
}

func (c *conn) readLine() (string, error) {
	line, err := c.br.ReadString('\n')
	if err != nil {
		return "", err
	}
	if len(line) < 3 || line[len(line)-2] != '\r' {
		return "", fmt.Errorf("redis: bad line %q", line)
	}
	return line[:len(line)-2], nil
}

func (c *conn) read() (Value, error) {
	line, err := c.readLine()
	if err != nil {
		return Value{}, err
	}
	kind, rest := line[0], line[1:]
	switch kind {
	case '+', '-':
		return Value{Kind: kind, Str: rest}, nil
	case ':':
		n, err := strconv.ParseInt(rest, 10, 64)
		if err != nil {
			return Value{}, fmt.Errorf("redis: bad integer %q", rest)
		}
		return Value{Kind: kind, Int: n}, nil
	case '$':
		n, err := strconv.Atoi(rest)
		if err != nil || n < -1 || n > maxBulk {
			return Value{}, fmt.Errorf("redis: bad bulk length %q", rest)
		}
		if n == -1 {
			return Value{Kind: kind, Null: true}, nil
		}
		buf := make([]byte, n+2)
		if _, err := io.ReadFull(c.br, buf); err != nil {
			return Value{}, err
		}
		return Value{Kind: kind, Str: string(buf[:n])}, nil
	case '*':
		n, err := strconv.Atoi(rest)
		if err != nil || n < -1 || n > 1<<20 {
			return Value{}, fmt.Errorf("redis: bad array length %q", rest)
		}
		if n == -1 {
			return Value{Kind: kind, Null: true}, nil
		}
		arr := make([]Value, n)
		for i := range arr {
			if arr[i], err = c.read(); err != nil {
				return Value{}, err
			}
		}
		return Value{Kind: kind, Array: arr}, nil
	default:
		return Value{}, fmt.Errorf("redis: unknown reply type %q", kind)
	}
}

// roundTrip sends one command and reads its reply; an error reply comes back as Error.
func (c *conn) roundTrip(timeout time.Duration, args []string) (Value, error) {
	if err := c.nc.SetDeadline(time.Now().Add(timeout)); err != nil {
		return Value{}, err
	}
	if err := c.write(args); err != nil {
		return Value{}, err
	}
	v, err := c.read()
	if err != nil {
		return Value{}, err
	}
	if v.Kind == '-' {
		return v, Error(v.Str)
	}
	return v, nil
}

// Client is a pool of connections. It is safe for concurrent use.
type Client struct {
	o      Options
	mu     sync.Mutex
	idle   []*conn
	slots  chan struct{} // one token per open connection, so at most PoolSize are open
	closed bool
}

// New makes a client; it connects lazily, on the first command.
func New(o Options) *Client {
	if o.Timeout <= 0 {
		o.Timeout = time.Second
	}
	if o.PoolSize <= 0 {
		o.PoolSize = 16
	}
	return &Client{o: o, slots: make(chan struct{}, o.PoolSize)}
}

func (cl *Client) get(ctx context.Context) (*conn, error) {
	select {
	case cl.slots <- struct{}{}:
	case <-ctx.Done():
		return nil, ctx.Err()
	}
	cl.mu.Lock()
	if cl.closed {
		cl.mu.Unlock()
		<-cl.slots
		return nil, ErrClosed
	}
	if n := len(cl.idle); n > 0 {
		c := cl.idle[n-1]
		cl.idle = cl.idle[:n-1]
		cl.mu.Unlock()
		return c, nil
	}
	cl.mu.Unlock()
	c, err := dial(ctx, cl.o)
	if err != nil {
		<-cl.slots
		return nil, err
	}
	return c, nil
}

func (cl *Client) put(c *conn, healthy bool) {
	cl.mu.Lock()
	if healthy && !cl.closed {
		cl.idle = append(cl.idle, c)
		cl.mu.Unlock()
	} else {
		cl.mu.Unlock()
		c.close()
	}
	<-cl.slots
}

// Do runs one command. A network error closes that connection; an error reply (Error) keeps it.
func (cl *Client) Do(ctx context.Context, args ...string) (Value, error) {
	c, err := cl.get(ctx)
	if err != nil {
		return Value{}, err
	}
	timeout := cl.o.Timeout
	if dl, ok := ctx.Deadline(); ok {
		if left := time.Until(dl); left < timeout {
			timeout = left
		}
	}
	v, err := c.roundTrip(timeout, args)
	var replyErr Error
	cl.put(c, err == nil || errors.As(err, &replyErr))
	return v, err
}

// Close closes the idle connections and refuses new commands.
func (cl *Client) Close() error {
	cl.mu.Lock()
	cl.closed = true
	idle := cl.idle
	cl.idle = nil
	cl.mu.Unlock()
	for _, c := range idle {
		c.close()
	}
	return nil
}

// Subscribe listens on one channel and calls onMessage for every message, until ctx ends or the
// connection fails (it returns that error; the caller decides when to listen again). A PING every
// `ping` keeps a quiet connection honest: a reply missing for two pings fails it.
func (cl *Client) Subscribe(ctx context.Context, channel string, ping time.Duration, onMessage func(string)) error {
	c, err := dial(ctx, cl.o)
	if err != nil {
		return err
	}
	defer c.close()
	stop := context.AfterFunc(ctx, c.close)
	defer stop()
	if err := c.nc.SetWriteDeadline(time.Now().Add(cl.o.Timeout)); err != nil {
		return err
	}
	if err := c.write([]string{"SUBSCRIBE", channel}); err != nil {
		return err
	}
	var writeMu sync.Mutex
	pingDone := make(chan struct{})
	defer close(pingDone)
	go func() {
		t := time.NewTicker(ping)
		defer t.Stop()
		for {
			select {
			case <-pingDone:
				return
			case <-t.C:
				writeMu.Lock()
				_ = c.nc.SetWriteDeadline(time.Now().Add(cl.o.Timeout))
				err := c.write([]string{"PING"})
				writeMu.Unlock()
				if err != nil {
					c.close()
					return
				}
			}
		}
	}()
	// Once ctx ends, AfterFunc closes the connection: whatever fails next, the reason is ctx.
	ended := func(err error) error {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		return err
	}
	for {
		if err := c.nc.SetReadDeadline(time.Now().Add(2*ping + cl.o.Timeout)); err != nil {
			return ended(err)
		}
		v, err := c.read()
		if err != nil {
			return ended(err)
		}
		if v.Kind != '*' || len(v.Array) < 2 {
			continue
		}
		switch strings.ToLower(v.Array[0].Str) {
		case "message":
			if len(v.Array) == 3 && v.Array[1].Str == channel {
				onMessage(v.Array[2].Str)
			}
		case "subscribe", "pong":
		}
	}
}

// Str is a string reply, ErrNil for a nil one.
func Str(v Value, err error) (string, error) {
	if err != nil {
		return "", err
	}
	if v.Null {
		return "", ErrNil
	}
	switch v.Kind {
	case '+', '$':
		return v.Str, nil
	case ':':
		return strconv.FormatInt(v.Int, 10), nil
	}
	return "", fmt.Errorf("redis: want a string, got %q", v.Kind)
}

// Int is an integer reply.
func Int(v Value, err error) (int64, error) {
	if err != nil {
		return 0, err
	}
	switch v.Kind {
	case ':':
		return v.Int, nil
	case '$', '+':
		if v.Null {
			return 0, ErrNil
		}
		return strconv.ParseInt(v.Str, 10, 64)
	}
	return 0, fmt.Errorf("redis: want an integer, got %q", v.Kind)
}

// Strings is an array of strings (a nil element becomes "").
func Strings(v Value, err error) ([]string, error) {
	if err != nil {
		return nil, err
	}
	if v.Null {
		return nil, nil
	}
	if v.Kind != '*' {
		return nil, fmt.Errorf("redis: want an array, got %q", v.Kind)
	}
	out := make([]string, len(v.Array))
	for i, e := range v.Array {
		if e.Kind == ':' {
			out[i] = strconv.FormatInt(e.Int, 10)
		} else {
			out[i] = e.Str
		}
	}
	return out, nil
}
