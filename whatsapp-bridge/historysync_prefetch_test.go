package main

import (
	"fmt"
	"strings"
	"testing"
	"time"

	"go.mau.fi/whatsmeow/proto/waCommon"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/proto/waHistorySync"
	"go.mau.fi/whatsmeow/proto/waWeb"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
	"google.golang.org/protobuf/proto"
)

// testLogger is a minimal waLog.Logger double that only records Warnf calls,
// which is all prefetchMedia uses to report a failed background fetch.
type testLogger struct {
	warnings chan string
}

func (l *testLogger) Warnf(msg string, args ...interface{}) {
	select {
	case l.warnings <- fmt.Sprintf(msg, args...):
	default:
	}
}
func (l *testLogger) Errorf(msg string, args ...interface{}) {}
func (l *testLogger) Infof(msg string, args ...interface{})  {}
func (l *testLogger) Debugf(msg string, args ...interface{}) {}
func (l *testLogger) Sub(string) waLog.Logger                { return l }

// Root cause of bug 1's remaining failures: handleMessage prefetches media for
// live events, but a reconnect delivers older messages through a *separate*
// events.HistorySync event, which never called prefetch at all. Those are
// exactly the messages whose CDN links are already stale by the time anyone
// asks for them via /api/download, which is what produced "media no longer
// available on phone" for message 3B167AFB8C2909F8FEC1.
//
// The media info here is deliberately incomplete (no URL/keys), so the
// background fetch fails fast inside downloadMedia's own validation, without
// ever touching the network or a live whatsmeow.Client (nil is passed as the
// client for exactly this reason). What this test verifies is that a fetch
// is attempted at all for a history-synced media message.
func TestHandleHistorySyncPrefetchesMedia(t *testing.T) {
	db := openTestDB(t)
	if err := initSchema(db); err != nil {
		t.Fatalf("initSchema failed: %v", err)
	}
	store := &MessageStore{db: db}

	chatJID := "120363430434063376@g.us"
	// Pre-seed the chat name so GetChatName returns immediately without
	// touching the (nil) client.
	if err := store.StoreChat(chatJID, "Test Group", time.Now()); err != nil {
		t.Fatalf("StoreChat failed: %v", err)
	}

	const msgID = "MSG_HISTORY_SYNC_1"
	historySync := &events.HistorySync{
		Data: &waHistorySync.HistorySync{
			Conversations: []*waHistorySync.Conversation{
				{
					ID: proto.String(chatJID),
					Messages: []*waHistorySync.HistorySyncMsg{
						{
							Message: &waWeb.WebMessageInfo{
								Key: &waCommon.MessageKey{
									ID:     proto.String(msgID),
									FromMe: proto.Bool(false),
								},
								Message: &waE2E.Message{
									ImageMessage: &waE2E.ImageMessage{},
								},
								MessageTimestamp: proto.Uint64(uint64(time.Now().Unix())),
							},
						},
					},
				},
			},
		},
	}

	logger := &testLogger{warnings: make(chan string, 4)}
	handleHistorySync(nil, store, historySync, logger)

	select {
	case msg := <-logger.warnings:
		if !strings.Contains(msg, msgID) {
			t.Fatalf("prefetch warning did not mention %s: %q", msgID, msg)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("history sync never attempted to prefetch the media message")
	}
}
