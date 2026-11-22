# bbc-live

"On the site now": who is on the site, and an agent's hello before the visitor writes. The contract,
routes, tokens and Redis keys are in [`../contracts/live.md`](../contracts/live.md).

- Go, the standard library only (a small RESP client in `internal/resp`). No database, no token issued
  here: the API is the authority, Redis the only store.
- `internal/token` reads the API's tokens; `internal/live` is the store (Lua in `internal/live/lua`, copies
  of `contracts/live/`), the hub that wakes waiting polls, and the HTTP routes.

## Run

```sh
REDIS_URL=redis://localhost:6379 LIVE_TOKEN_SECRET=… \
ALLOWED_ORIGINS=https://www.buybusinessclass.com,https://admin.example.com go run .
```

## Test

```sh
go vet ./... && go test -race ./...
```

The tests use a real Redis: `REDIS_TEST_URL`, or a `redis-server` on the PATH started for the run; without
either they skip (CI sets `LIVE_TEST_REQUIRE_REDIS=1`, so there they fail instead). Every package's tests empty
that Redis first, so with one shared `REDIS_TEST_URL` run the packages one at a time: `go test -race -p 1 ./...`
(CI does; with a `redis-server` each test binary starts its own).
