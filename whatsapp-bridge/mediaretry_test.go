package main

import (
	"fmt"
	"testing"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/types/events"
)

// A 403/404/410 must route to the retry path; anything else must not, or we'd
// spam the sender's device for problems a re-upload can't fix.
func TestIsMediaGoneError(t *testing.T) {
	cases := []struct {
		name string
		err  error
		want bool
	}{
		{"403", whatsmeow.ErrMediaDownloadFailedWith403, true},
		{"404", whatsmeow.ErrMediaDownloadFailedWith404, true},
		{"410", whatsmeow.ErrMediaDownloadFailedWith410, true},
		{"wrapped 403", fmt.Errorf("failed to download media: %w", whatsmeow.ErrMediaDownloadFailedWith403), true},
		{"bad sha256", whatsmeow.ErrInvalidMediaSHA256, false},
		{"unrelated", fmt.Errorf("connection reset"), false},
		{"nil", nil, false},
	}
	for _, tc := range cases {
		if got := isMediaGoneError(tc.err); got != tc.want {
			t.Errorf("%s: isMediaGoneError(%v) = %v, want %v", tc.name, tc.err, got, tc.want)
		}
	}
}

// The retry response arrives on the event goroutine while the download blocks
// on another, so delivery must reach the right waiter and must not block when
// nobody is listening.
func TestMediaRetryWaiterRouting(t *testing.T) {
	chWanted, doneWanted := awaitMediaRetry("MSG_WANTED")
	defer doneWanted()
	chOther, doneOther := awaitMediaRetry("MSG_OTHER")
	defer doneOther()

	deliverMediaRetry(&events.MediaRetry{MessageID: "MSG_WANTED"})

	select {
	case evt := <-chWanted:
		if evt.MessageID != "MSG_WANTED" {
			t.Fatalf("got event for %s, want MSG_WANTED", evt.MessageID)
		}
	case <-time.After(time.Second):
		t.Fatal("waiter never received its event")
	}

	select {
	case evt := <-chOther:
		t.Fatalf("unrelated waiter received %s", evt.MessageID)
	default:
	}
}

// An unmatched response (retry timed out, or a stray event) must be dropped
// rather than deadlocking the event handler.
func TestMediaRetryUnknownMessageDoesNotBlock(t *testing.T) {
	done := make(chan struct{})
	go func() {
		deliverMediaRetry(&events.MediaRetry{MessageID: "NOBODY_WAITING"})
		close(done)
	}()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("deliverMediaRetry blocked with no waiter registered")
	}
}

// A timed-out download deregisters itself; a late response must then be a no-op.
func TestMediaRetryCleanupRemovesWaiter(t *testing.T) {
	_, done := awaitMediaRetry("MSG_ABANDONED")
	done()

	pendingMediaRetries.Lock()
	_, stillThere := pendingMediaRetries.waiters["MSG_ABANDONED"]
	pendingMediaRetries.Unlock()
	if stillThere {
		t.Fatal("waiter still registered after cleanup")
	}

	finished := make(chan struct{})
	go func() {
		deliverMediaRetry(&events.MediaRetry{MessageID: "MSG_ABANDONED"})
		close(finished)
	}()
	select {
	case <-finished:
	case <-time.After(time.Second):
		t.Fatal("late response blocked after waiter cleanup")
	}
}
