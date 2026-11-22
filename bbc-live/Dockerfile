# bbc-live, built from this folder (on Railway: a service whose root directory is bbc-live).
# The standard library only: nothing to download but the toolchain image.
FROM golang:1.24-alpine AS build
WORKDIR /src
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/bbc-live .

# Static binary, no shell, not root; the CA bundle is there for a rediss:// Redis.
FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/bbc-live /bbc-live
USER nonroot:nonroot
EXPOSE 8080
ENTRYPOINT ["/bbc-live"]
