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

`GET /health` says `"ready": true` once the settings it needs are there (never their values): `META_APP_SECRET`,
`META_VERIFY_TOKEN`, `META_APP_ID` (without it the app's own messages, coming back as echoes, would reach the API as
the Page inbox's), at least one Page with its id and token (the second Page is not needed), `API_URL` and
`MESSENGER_GATEWAY_SECRET`. Until then `POST /webhook` answers Meta 503 and Meta delivers again; `GET /webhook` and
`/health` still answer, for the setup, and Meta's check of the webhook passes once `META_VERIFY_TOKEN` is the token
given in the app's webhook settings.

## Test

```sh
go vet ./... && go test -race ./...
```

The tests use a fake Send API and a fake API (`httptest`): no network, no Meta account.
