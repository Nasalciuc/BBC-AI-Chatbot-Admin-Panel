package main

import "testing"

func TestPagesNeedTheirIdAndToken(t *testing.T) {
	env := map[string]string{
		"META_PAGE_ID_BBC": " 111 ", "META_PAGE_TOKEN_BBC": "token-bbc",
		"META_PAGE_ID_BCT": "222", // no token: not a Page the gateway can answer for
	}
	got := pages(func(k string) string { return env[k] })
	if len(got) != 1 || got["111"].Site != "bbc" || got["111"].Token != "token-bbc" {
		t.Errorf("pages: %+v", got)
	}
}
