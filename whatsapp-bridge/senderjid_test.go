package main

import (
	"context"
	"errors"
	"testing"

	"go.mau.fi/whatsmeow/types"
)

// fakeLIDs answers GetPNForLID only for the LIDs it knows, mirroring the
// device's real LID map: a hit means the user part is a LID, a miss means it
// is a phone number.
type fakeLIDs struct {
	known map[string]string
	err   error
}

func (f fakeLIDs) GetPNForLID(_ context.Context, lid types.JID) (types.JID, error) {
	if f.err != nil {
		return types.EmptyJID, f.err
	}
	pn, ok := f.known[lid.User]
	if !ok {
		return types.EmptyJID, nil
	}
	return types.JID{User: pn, Server: types.DefaultUserServer}, nil
}

func TestResolveSenderJID(t *testing.T) {
	// 182476131586252 is a real LID from the device's map; 14157588762 is the
	// phone number behind it.
	lids := fakeLIDs{known: map[string]string{"182476131586252": "14157588762"}}

	cases := []struct {
		name       string
		senderUser string
		want       string
	}{
		{"known LID resolves to @lid", "182476131586252", "182476131586252@lid"},
		{"unknown user part is a phone number", "14157588762", "14157588762@s.whatsapp.net"},
		{"already-qualified JID passes through", "182476131586252@lid", "182476131586252@lid"},
		{"already-qualified phone JID passes through", "14155551234@s.whatsapp.net", "14155551234@s.whatsapp.net"},
	}
	for _, tc := range cases {
		got, err := resolveSenderJID(lids, tc.senderUser)
		if err != nil {
			t.Errorf("%s: unexpected error: %v", tc.name, err)
			continue
		}
		if got != tc.want {
			t.Errorf("%s: resolveSenderJID(%q) = %q, want %q", tc.name, tc.senderUser, got, tc.want)
		}
	}
}

// Without a sender there is nothing to address a group retry receipt to, so
// this must fail loudly rather than produce a bogus "@s.whatsapp.net" JID.
func TestResolveSenderJIDRejectsEmpty(t *testing.T) {
	if got, err := resolveSenderJID(fakeLIDs{}, ""); err == nil {
		t.Fatalf("expected an error for an empty sender, got %q", got)
	}
}

// A broken LID store must not silently mislabel a LID as a phone number; treat
// the lookup failure as "not a known LID" but never crash.
func TestResolveSenderJIDSurvivesLookupError(t *testing.T) {
	got, err := resolveSenderJID(fakeLIDs{err: errors.New("db closed")}, "182476131586252")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got != "182476131586252@s.whatsapp.net" {
		t.Errorf("got %q, want fallback to 182476131586252@s.whatsapp.net", got)
	}
}

// A nil LID store (client not fully initialised) must not panic.
func TestResolveSenderJIDHandlesNilStore(t *testing.T) {
	got, err := resolveSenderJID(nil, "14157588762")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got != "14157588762@s.whatsapp.net" {
		t.Errorf("got %q, want 14157588762@s.whatsapp.net", got)
	}
}

// The direct path is what every download is built from; it must be the URL's
// path component with no query string, and must start with a slash or
// whatsmeow rejects it outright.
func TestExtractDirectPathFromURL(t *testing.T) {
	cases := []struct {
		name string
		url  string
		want string
	}{
		{
			"strips host and query",
			"https://mmg.whatsapp.net/v/t62.7118-24/13812002_698058036224062_n.enc?ccb=11-4&oh=abc&oe=def",
			"/v/t62.7118-24/13812002_698058036224062_n.enc",
		},
		{
			"no query string",
			"https://mmg.whatsapp.net/v/t62.7118-24/file.enc",
			"/v/t62.7118-24/file.enc",
		},
	}
	for _, tc := range cases {
		if got := extractDirectPathFromURL(tc.url); got != tc.want {
			t.Errorf("%s: extractDirectPathFromURL(%q) = %q, want %q", tc.name, tc.url, got, tc.want)
		}
	}
}
