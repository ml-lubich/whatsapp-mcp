package main

import (
	"fmt"
	"testing"
	"time"

	waLog "go.mau.fi/whatsmeow/util/log"
)

// Media was only ever fetched when someone asked for it. By then WhatsApp's CDN
// object had usually expired, so the download fell back to asking the sender's
// phone to re-upload — which answers "media no longer available on phone" and
// leaves us with nothing, even though the image is still visible in the app.
//
// The fix is to fetch on arrival, while the link is still good. These cover the
// decision of what to fetch; the fetch itself reuses downloadMedia, which
// already short-circuits when the file is on disk.
func TestShouldPrefetchMedia(t *testing.T) {
	cases := []struct {
		name      string
		mediaType string
		want      bool
	}{
		{"image", "image", true},
		{"video", "video", true},
		{"audio", "audio", true},
		{"document", "document", true},
		{"plain text has no media", "", false},
		{"unknown type is not guessed at", "sticker-pack", false},
	}
	for _, tc := range cases {
		if got := shouldPrefetchMedia(tc.mediaType); got != tc.want {
			t.Errorf("%s: shouldPrefetchMedia(%q) = %v, want %v",
				tc.name, tc.mediaType, got, tc.want)
		}
	}
}

// Functional: a media message must actually trigger a fetch, with the right
// identifiers, and must not block the caller.
func TestPrefetchMediaFetchesInBackground(t *testing.T) {
	type call struct{ id, jid string }
	calls := make(chan call, 1)

	prefetchMedia("image", "MSG_1", "chat@g.us", func(id, jid string) error {
		calls <- call{id, jid}
		return nil
	}, waLog.Noop)

	select {
	case got := <-calls:
		if got.id != "MSG_1" || got.jid != "chat@g.us" {
			t.Errorf("fetched %+v, want {MSG_1 chat@g.us}", got)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("media message did not trigger a fetch")
	}
}

// Negative: a text message must not touch the network. A stray fetch per text
// message would hammer the CDN for every chat we observe.
func TestPrefetchMediaSkipsNonMedia(t *testing.T) {
	fetched := make(chan struct{}, 1)
	for _, mediaType := range []string{"", "sticker-pack"} {
		prefetchMedia(mediaType, "MSG_2", "chat@g.us", func(string, string) error {
			fetched <- struct{}{}
			return nil
		}, waLog.Noop)
	}

	select {
	case <-fetched:
		t.Fatal("non-media message triggered a fetch")
	case <-time.After(300 * time.Millisecond):
		// nothing fetched, as intended
	}
}

// Negative: a failing download must not panic or block. The message is already
// stored, and the HTTP endpoint can still retry later.
func TestPrefetchMediaSurvivesFetchFailure(t *testing.T) {
	done := make(chan struct{}, 1)

	prefetchMedia("document", "MSG_3", "chat@g.us", func(string, string) error {
		defer func() { done <- struct{}{} }()
		return fmt.Errorf("cdn said no")
	}, waLog.Noop)

	select {
	case <-done:
		// returned cleanly despite the error
	case <-time.After(2 * time.Second):
		t.Fatal("failing fetch never completed")
	}
}
