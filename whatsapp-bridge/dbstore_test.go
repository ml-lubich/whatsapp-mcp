package main

import (
	"database/sql"
	"testing"
	"time"
)

// GetMessages and GetChats read the tables StoreMessage/StoreChat write to.
// These cover the DB-facing edges the schema tests don't: empty results,
// LIMIT semantics, ordering, and upsert-via-INSERT-OR-REPLACE behavior.

func TestGetMessagesEdgeCases(t *testing.T) {
	db := openTestDB(t)
	if err := initSchema(db); err != nil {
		t.Fatalf("initSchema failed: %v", err)
	}
	store := &MessageStore{db: db}
	const chatJID = "120363@g.us"
	if err := store.StoreChat(chatJID, "Test Chat", time.Now()); err != nil {
		t.Fatalf("StoreChat failed: %v", err)
	}

	t.Run("empty chat returns no messages and no error", func(t *testing.T) {
		msgs, err := store.GetMessages(chatJID, 10)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if len(msgs) != 0 {
			t.Errorf("got %d messages, want 0", len(msgs))
		}
	})

	t.Run("nonexistent chat returns no messages and no error", func(t *testing.T) {
		msgs, err := store.GetMessages("does-not-exist@g.us", 10)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if len(msgs) != 0 {
			t.Errorf("got %d messages, want 0", len(msgs))
		}
	})

	base := time.Now().Truncate(time.Second)
	for i, id := range []string{"M1", "M2", "M3", "M4", "M5"} {
		ts := base.Add(time.Duration(i) * time.Minute)
		if err := store.StoreMessage(id, chatJID, "sender", "sender@s.whatsapp.net",
			"msg-"+id, ts, false, "", "", "", nil, nil, nil, 0); err != nil {
			t.Fatalf("StoreMessage(%s) failed: %v", id, err)
		}
	}

	t.Run("limit is respected", func(t *testing.T) {
		msgs, err := store.GetMessages(chatJID, 2)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if len(msgs) != 2 {
			t.Fatalf("got %d messages, want 2", len(msgs))
		}
	})

	t.Run("results are ordered newest first", func(t *testing.T) {
		msgs, err := store.GetMessages(chatJID, 5)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if len(msgs) != 5 {
			t.Fatalf("got %d messages, want 5", len(msgs))
		}
		if msgs[0].Content != "msg-M5" {
			t.Errorf("newest message = %q, want msg-M5", msgs[0].Content)
		}
		if msgs[len(msgs)-1].Content != "msg-M1" {
			t.Errorf("oldest returned message = %q, want msg-M1", msgs[len(msgs)-1].Content)
		}
	})

	t.Run("limit 0 returns no rows", func(t *testing.T) {
		msgs, err := store.GetMessages(chatJID, 0)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if len(msgs) != 0 {
			t.Errorf("got %d messages, want 0 for LIMIT 0", len(msgs))
		}
	})

	// SQLite treats a negative LIMIT as "no limit"; this documents that the
	// bridge inherits that behavior rather than rejecting it.
	t.Run("negative limit means unlimited under SQLite semantics", func(t *testing.T) {
		msgs, err := store.GetMessages(chatJID, -1)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if len(msgs) != 5 {
			t.Errorf("got %d messages with limit -1, want all 5", len(msgs))
		}
	})
}

func TestGetChatsEdgeCases(t *testing.T) {
	db := openTestDB(t)
	if err := initSchema(db); err != nil {
		t.Fatalf("initSchema failed: %v", err)
	}
	store := &MessageStore{db: db}

	t.Run("empty database returns an empty map and no error", func(t *testing.T) {
		chats, err := store.GetChats()
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if len(chats) != 0 {
			t.Errorf("got %d chats, want 0", len(chats))
		}
	})

	t1 := time.Now().Truncate(time.Second)
	if err := store.StoreChat("a@g.us", "Chat A", t1); err != nil {
		t.Fatalf("StoreChat(a) failed: %v", err)
	}
	if err := store.StoreChat("b@g.us", "Chat B", t1.Add(time.Hour)); err != nil {
		t.Fatalf("StoreChat(b) failed: %v", err)
	}

	t.Run("all stored chats are returned", func(t *testing.T) {
		chats, err := store.GetChats()
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if len(chats) != 2 {
			t.Fatalf("got %d chats, want 2", len(chats))
		}
		if _, ok := chats["a@g.us"]; !ok {
			t.Error("missing chat a@g.us")
		}
		if _, ok := chats["b@g.us"]; !ok {
			t.Error("missing chat b@g.us")
		}
	})

	// StoreChat is INSERT OR REPLACE, so re-storing the same JID must update
	// the existing row rather than creating a second one.
	t.Run("storing the same JID again upserts instead of duplicating", func(t *testing.T) {
		newTime := t1.Add(24 * time.Hour)
		if err := store.StoreChat("a@g.us", "Chat A renamed", newTime); err != nil {
			t.Fatalf("StoreChat re-upsert failed: %v", err)
		}
		chats, err := store.GetChats()
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if len(chats) != 2 {
			t.Fatalf("got %d chats after upsert, want still 2 (no duplicate row)", len(chats))
		}
		got := chats["a@g.us"]
		if !got.Equal(newTime) {
			t.Errorf("chat a@g.us last_message_time = %v, want %v", got, newTime)
		}
	})

	// SQL-injection-shaped content in a text column must round-trip verbatim
	// (parameterized queries, not string concatenation) and must not corrupt
	// the schema.
	t.Run("SQL-injection-shaped chat name round-trips safely", func(t *testing.T) {
		evilName := "'; DROP TABLE messages; --"
		if err := store.StoreChat("evil@g.us", evilName, t1); err != nil {
			t.Fatalf("StoreChat with injection-shaped name failed: %v", err)
		}
		var gotName string
		if err := db.QueryRow("SELECT name FROM chats WHERE jid = ?", "evil@g.us").Scan(&gotName); err != nil {
			t.Fatalf("failed to read back chat name: %v", err)
		}
		if gotName != evilName {
			t.Errorf("chat name = %q, want %q", gotName, evilName)
		}
		// The messages table must still exist and be usable.
		if _, err := db.Exec("INSERT INTO chats (jid, name) VALUES (?, ?)", "sanity@g.us", "still here"); err != nil {
			t.Fatalf("messages/chats tables appear damaged after injection-shaped input: %v", err)
		}
	})
}

func TestStoreMessageEdgeCases(t *testing.T) {
	db := openTestDB(t)
	if err := initSchema(db); err != nil {
		t.Fatalf("initSchema failed: %v", err)
	}
	store := &MessageStore{db: db}
	const chatJID = "120363@g.us"
	if err := store.StoreChat(chatJID, "Test Chat", time.Now()); err != nil {
		t.Fatalf("StoreChat failed: %v", err)
	}

	t.Run("message with no content and no media is silently skipped", func(t *testing.T) {
		if err := store.StoreMessage("EMPTY1", chatJID, "s", "s@s.whatsapp.net", "", time.Now(), false, "", "", "", nil, nil, nil, 0); err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		var count int
		if err := db.QueryRow("SELECT COUNT(*) FROM messages WHERE id = 'EMPTY1'").Scan(&count); err != nil {
			t.Fatalf("count query failed: %v", err)
		}
		if count != 0 {
			t.Errorf("empty message was stored (%d rows), want 0", count)
		}
	})

	t.Run("text-only message is stored with empty media fields", func(t *testing.T) {
		if err := store.StoreMessage("TXT1", chatJID, "s", "s@s.whatsapp.net", "hello", time.Now(), false, "", "", "", nil, nil, nil, 0); err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		msgs, err := store.GetMessages(chatJID, 100)
		if err != nil {
			t.Fatalf("GetMessages failed: %v", err)
		}
		found := false
		for _, m := range msgs {
			if m.Content == "hello" {
				found = true
				if m.MediaType != "" {
					t.Errorf("MediaType = %q, want empty", m.MediaType)
				}
			}
		}
		if !found {
			t.Error("text-only message was not stored")
		}
	})

	t.Run("re-storing the same id and chat overwrites, not duplicates", func(t *testing.T) {
		if err := store.StoreMessage("DUP1", chatJID, "s", "s@s.whatsapp.net", "first version", time.Now(), false, "", "", "", nil, nil, nil, 0); err != nil {
			t.Fatalf("first StoreMessage failed: %v", err)
		}
		if err := store.StoreMessage("DUP1", chatJID, "s", "s@s.whatsapp.net", "second version", time.Now(), false, "", "", "", nil, nil, nil, 0); err != nil {
			t.Fatalf("second StoreMessage failed: %v", err)
		}
		var count int
		var content string
		if err := db.QueryRow("SELECT COUNT(*), content FROM messages WHERE id = 'DUP1' GROUP BY content").Scan(&count, &content); err != nil {
			t.Fatalf("query failed: %v", err)
		}
		if count != 1 {
			t.Errorf("got %d rows for DUP1, want exactly 1", count)
		}
		if content != "second version" {
			t.Errorf("content = %q, want %q", content, "second version")
		}
	})

	t.Run("unicode and emoji content round-trips exactly", func(t *testing.T) {
		text := "héllo 🎉 日本語 مرحبا"
		if err := store.StoreMessage("UNI1", chatJID, "s", "s@s.whatsapp.net", text, time.Now(), false, "", "", "", nil, nil, nil, 0); err != nil {
			t.Fatalf("StoreMessage failed: %v", err)
		}
		var got string
		if err := db.QueryRow("SELECT content FROM messages WHERE id = 'UNI1'").Scan(&got); err != nil {
			t.Fatalf("query failed: %v", err)
		}
		if got != text {
			t.Errorf("content = %q, want %q", got, text)
		}
	})

	t.Run("SQL-injection-shaped content round-trips safely without damaging the schema", func(t *testing.T) {
		evil := "'; DROP TABLE messages; --"
		if err := store.StoreMessage("INJ1", chatJID, "s", "s@s.whatsapp.net", evil, time.Now(), false, "", "", "", nil, nil, nil, 0); err != nil {
			t.Fatalf("StoreMessage failed: %v", err)
		}
		var got string
		if err := db.QueryRow("SELECT content FROM messages WHERE id = 'INJ1'").Scan(&got); err != nil {
			t.Fatalf("messages table appears damaged: %v", err)
		}
		if got != evil {
			t.Errorf("content = %q, want %q", got, evil)
		}
	})

	t.Run("nil byte slices for media keys round-trip as empty, not error", func(t *testing.T) {
		if err := store.StoreMessage("NILKEYS", chatJID, "s", "s@s.whatsapp.net", "", time.Now(), false, "image", "f.jpg", "", nil, nil, nil, 0); err != nil {
			t.Fatalf("StoreMessage failed: %v", err)
		}
		_, _, _, mediaKey, sha, encsha, length, err := store.GetMediaInfo("NILKEYS", chatJID)
		if err != nil {
			t.Fatalf("GetMediaInfo failed: %v", err)
		}
		if len(mediaKey) != 0 || len(sha) != 0 || len(encsha) != 0 || length != 0 {
			t.Errorf("expected all-empty media fields, got mediaKey=%v sha=%v encsha=%v length=%d", mediaKey, sha, encsha, length)
		}
	})

	t.Run("large but realistic file length round-trips exactly", func(t *testing.T) {
		const bigFile uint64 = 4 * 1024 * 1024 * 1024 // 4GB, plausible video size
		if err := store.StoreMessage("BIG1", chatJID, "s", "s@s.whatsapp.net", "", time.Now(), false, "video", "big.mp4", "https://x", []byte("k"), []byte("s"), []byte("e"), bigFile); err != nil {
			t.Fatalf("StoreMessage failed: %v", err)
		}
		_, _, _, _, _, _, gotLength, err := store.GetMediaInfo("BIG1", chatJID)
		if err != nil {
			t.Fatalf("GetMediaInfo failed: %v", err)
		}
		if gotLength != bigFile {
			t.Errorf("fileLength = %d, want %d", gotLength, bigFile)
		}
	})
}

func TestMediaInfoEdgeCases(t *testing.T) {
	db := openTestDB(t)
	if err := initSchema(db); err != nil {
		t.Fatalf("initSchema failed: %v", err)
	}
	store := &MessageStore{db: db}
	const chatJID = "120363@g.us"
	if err := store.StoreChat(chatJID, "Test Chat", time.Now()); err != nil {
		t.Fatalf("StoreChat failed: %v", err)
	}

	t.Run("GetMediaInfo on a nonexistent message returns sql.ErrNoRows", func(t *testing.T) {
		_, _, _, _, _, _, _, err := store.GetMediaInfo("NOPE", chatJID)
		if err != sql.ErrNoRows {
			t.Errorf("err = %v, want sql.ErrNoRows", err)
		}
	})

	if err := store.StoreMessage("MI1", chatJID, "s", "s@s.whatsapp.net", "", time.Now(), false,
		"image", "photo.jpg", "https://old-url", []byte("oldkey"), []byte("oldsha"), []byte("oldenc"), 100); err != nil {
		t.Fatalf("StoreMessage failed: %v", err)
	}

	t.Run("StoreMediaInfo updates an existing message's media fields", func(t *testing.T) {
		if err := store.StoreMediaInfo("MI1", chatJID, "https://new-url", []byte("newkey"), []byte("newsha"), []byte("newenc"), 200); err != nil {
			t.Fatalf("StoreMediaInfo failed: %v", err)
		}
		_, _, url, mediaKey, sha, encsha, length, err := store.GetMediaInfo("MI1", chatJID)
		if err != nil {
			t.Fatalf("GetMediaInfo failed: %v", err)
		}
		if url != "https://new-url" || string(mediaKey) != "newkey" || string(sha) != "newsha" || string(encsha) != "newenc" || length != 200 {
			t.Errorf("media fields not updated: url=%q mediaKey=%q sha=%q encsha=%q length=%d", url, mediaKey, sha, encsha, length)
		}
	})

	t.Run("StoreMediaInfo on a nonexistent message is a no-op, not an error", func(t *testing.T) {
		if err := store.StoreMediaInfo("GHOST", chatJID, "https://x", []byte("k"), []byte("s"), []byte("e"), 1); err != nil {
			t.Fatalf("unexpected error updating a nonexistent row: %v", err)
		}
		_, _, _, _, _, _, _, err := store.GetMediaInfo("GHOST", chatJID)
		if err != sql.ErrNoRows {
			t.Errorf("err = %v, want sql.ErrNoRows (no row should have been created)", err)
		}
	})
}
