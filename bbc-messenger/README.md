# bbc-messenger

Skye and the agents on Facebook Messenger, for the BuyBusinessClass and Business Class Tickets Pages. The
contract, the routes, the signatures and the counters are in [`../contracts/messenger.md`](../contracts/messenger.md).

- Go, the standard library only. No database: Meta's events go to the API, which keeps the chat and decides the
  replies; the gateway checks Meta's signature, keeps each person's events in order and sends what the API answers.
- `internal/meta` is Meta's side (the webhook's signature and payload, the Send API); `internal/gateway` is the
  webhook, the workers, the API's route out (`POST /v1/send`) and `/health`.

## Run

The secrets go only into the service's variables (Railway), never into code, Git, chat or e-mail:

```sh
META_APP_ID=… META_APP_SECRET=… META_VERIFY_TOKEN=… \
META_PAGE_ID_BBC=… META_PAGE_TOKEN_BBC=… META_PAGE_ID_BCT=… META_PAGE_TOKEN_BCT=… \
API_URL=https://… MESSENGER_GATEWAY_SECRET=… go run .
```

`GET /health` says `"ready": true` once every variable is set (never their values). Until then the webhook answers
Meta 503 (Meta delivers again); Meta's check (`GET /webhook`) and `/health` still answer, for the setup.

## Test

```sh
go vet ./... && go test -race ./...
```

The tests use a fake Send API and a fake API (`httptest`): no network, no Meta account.
