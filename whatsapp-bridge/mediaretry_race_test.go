package main

import (
	"sync"
	"testing"
	"time"

	"go.mau.fi/whatsmeow/types/events"
)

// Two callers can ask for the same message's media before either finishes —
// the MCP tool and the REST endpoint, or simply an impatient retry. Both must
// receive the single retry response; neither may be starved by the other
// registering or deregistering.
func TestConcurrentRetriesForSameMessage(t *testing.T) {
	const msgID = "MSG_CONTESTED"

	chFirst, doneFirst := awaitMediaRetry(msgID)
	defer doneFirst()
	chSecond, doneSecond := awaitMediaRetry(msgID)
	defer doneSecond()

	deliverMediaRetry(&events.MediaRetry{MessageID: msgID})

	for name, ch := range map[string]chan *events.MediaRetry{
		"first waiter": chFirst, "second waiter": chSecond,
	} {
		select {
		case evt := <-ch:
			if evt.MessageID != msgID {
				t.Errorf("%s: got %s, want %s", name, evt.MessageID, msgID)
			}
		case <-time.After(time.Second):
			t.Errorf("%s never received the retry response", name)
		}
	}
}

// One caller giving up (timeout) must not deregister a sibling still waiting
// on the same message.
func TestAbandonedRetryDoesNotStarveSibling(t *testing.T) {
	const msgID = "MSG_ABANDONED_SIBLING"

	_, doneAbandoned := awaitMediaRetry(msgID)
	chSurvivor, doneSurvivor := awaitMediaRetry(msgID)
	defer doneSurvivor()

	doneAbandoned() // first caller times out and cleans up

	deliverMediaRetry(&events.MediaRetry{MessageID: msgID})

	select {
	case evt := <-chSurvivor:
		if evt.MessageID != msgID {
			t.Fatalf("got %s, want %s", evt.MessageID, msgID)
		}
	case <-time.After(time.Second):
		t.Fatal("surviving waiter was starved by its sibling's cleanup")
	}
}

// The registry is touched from the event goroutine and every download
// goroutine at once; run it under -race to catch unguarded map access.
func TestRegistryConcurrentAccessIsSafe(t *testing.T) {
	var wg sync.WaitGroup
	for i := 0; i < 50; i++ {
		wg.Add(1)
		go func(n int) {
			defer wg.Done()
			id := string(rune('A' + n%26))
			ch, done := awaitMediaRetry(id)
			defer done()
			go deliverMediaRetry(&events.MediaRetry{MessageID: id})
			select {
			case <-ch:
			case <-time.After(500 * time.Millisecond):
			}
		}(i)
	}
	wg.Wait()
}
