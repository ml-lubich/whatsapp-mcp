package main

import (
	"database/sql"
	"path/filepath"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

func openTestDB(t *testing.T) *sql.DB {
	t.Helper()
	db, err := sql.Open("sqlite3", "file:"+filepath.Join(t.TempDir(), "messages.db"))
	if err != nil {
		t.Fatalf("failed to open test database: %v", err)
	}
	t.Cleanup(func() { db.Close() })
	return db
}

func hasColumn(t *testing.T, db *sql.DB, table, column string) bool {
	t.Helper()
	rows, err := db.Query("PRAGMA table_info(" + table + ")")
	if err != nil {
		t.Fatalf("failed to read schema: %v", err)
	}
	defer rows.Close()
	for rows.Next() {
		var cid int
		var name, colType string
		var notNull, pk int
		var dflt sql.NullString
		if err := rows.Scan(&cid, &name, &colType, &notNull, &dflt, &pk); err != nil {
			t.Fatalf("failed to scan schema row: %v", err)
		}
		if name == column {
			return true
		}
	}
	return false
}

// The bridge runs initSchema on every start, so a second run against an
// already-migrated database must be a no-op. If it errored the bridge would
// refuse to start after its first launch.
func TestInitSchemaIsIdempotent(t *testing.T) {
	db := openTestDB(t)

	for i := 1; i <= 3; i++ {
		if err := initSchema(db); err != nil {
			t.Fatalf("initSchema run %d failed: %v", i, err)
		}
	}
	if !hasColumn(t, db, "messages", "sender_jid") {
		t.Fatal("sender_jid column missing after repeated initSchema runs")
	}
}

// Databases created before sender_jid existed must gain the column in place,
// without losing the messages already stored in them.
func TestInitSchemaMigratesLegacyDatabase(t *testing.T) {
	db := openTestDB(t)

	// Recreate the pre-migration schema exactly.
	if _, err := db.Exec(`
		CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP);
		CREATE TABLE messages (
			id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TIMESTAMP,
			is_from_me BOOLEAN, media_type TEXT, filename TEXT, url TEXT,
			media_key BLOB, file_sha256 BLOB, file_enc_sha256 BLOB, file_length INTEGER,
			PRIMARY KEY (id, chat_jid)
		);
		INSERT INTO chats VALUES ('120363@g.us', 'Website :)', CURRENT_TIMESTAMP);
		INSERT INTO messages (id, chat_jid, sender, content, is_from_me, media_type)
		VALUES ('LEGACY1', '120363@g.us', '182476131586252', 'old message', 0, 'image');
	`); err != nil {
		t.Fatalf("failed to build legacy database: %v", err)
	}

	if hasColumn(t, db, "messages", "sender_jid") {
		t.Fatal("legacy fixture unexpectedly already has sender_jid")
	}
	if err := initSchema(db); err != nil {
		t.Fatalf("migration of legacy database failed: %v", err)
	}
	if !hasColumn(t, db, "messages", "sender_jid") {
		t.Fatal("sender_jid column was not added to legacy database")
	}

	var content string
	var senderJID sql.NullString
	if err := db.QueryRow(
		"SELECT content, sender_jid FROM messages WHERE id = 'LEGACY1'",
	).Scan(&content, &senderJID); err != nil {
		t.Fatalf("legacy row unreadable after migration: %v", err)
	}
	if content != "old message" {
		t.Errorf("legacy row content = %q, want %q", content, "old message")
	}
	if senderJID.Valid {
		t.Errorf("migrated legacy row should have NULL sender_jid, got %q", senderJID.String)
	}
}

// A legacy row has no sender_jid; GetMessageSender must surface that as empty
// (so the caller resolves it) while still returning the bare user part.
func TestGetMessageSenderHandlesLegacyRow(t *testing.T) {
	db := openTestDB(t)
	if err := initSchema(db); err != nil {
		t.Fatalf("initSchema failed: %v", err)
	}
	store := &MessageStore{db: db}

	if _, err := db.Exec(`
		INSERT INTO chats VALUES ('120363@g.us', 'Website :)', CURRENT_TIMESTAMP);
		INSERT INTO messages (id, chat_jid, sender, content, is_from_me, media_type)
		VALUES ('LEGACY1', '120363@g.us', '182476131586252', 'x', 0, 'image');
	`); err != nil {
		t.Fatalf("failed to seed legacy row: %v", err)
	}

	senderJID, senderUser, isFromMe, err := store.GetMessageSender("LEGACY1", "120363@g.us")
	if err != nil {
		t.Fatalf("GetMessageSender failed: %v", err)
	}
	if senderJID != "" {
		t.Errorf("senderJID = %q, want empty for a legacy row", senderJID)
	}
	if senderUser != "182476131586252" {
		t.Errorf("senderUser = %q, want 182476131586252", senderUser)
	}
	if isFromMe {
		t.Error("isFromMe = true, want false")
	}
}

// Newly received messages must persist the full JID, including the server —
// dropping it is what broke group media retries in the first place.
func TestStoreMessageRoundTripsSenderJID(t *testing.T) {
	db := openTestDB(t)
	if err := initSchema(db); err != nil {
		t.Fatalf("initSchema failed: %v", err)
	}
	store := &MessageStore{db: db}

	if err := store.StoreChat("120363@g.us", "Website :)", time.Now()); err != nil {
		t.Fatalf("StoreChat failed: %v", err)
	}
	if err := store.StoreMessage(
		"MSG1", "120363@g.us", "182476131586252", "182476131586252@lid",
		"hello", time.Now(), false, "image", "photo.jpg", "https://mmg.whatsapp.net/v/x.enc",
		[]byte("key"), []byte("sha"), []byte("encsha"), 1234,
	); err != nil {
		t.Fatalf("StoreMessage failed: %v", err)
	}

	senderJID, senderUser, isFromMe, err := store.GetMessageSender("MSG1", "120363@g.us")
	if err != nil {
		t.Fatalf("GetMessageSender failed: %v", err)
	}
	if senderJID != "182476131586252@lid" {
		t.Errorf("senderJID = %q, want 182476131586252@lid", senderJID)
	}
	if senderUser != "182476131586252" {
		t.Errorf("senderUser = %q, want 182476131586252", senderUser)
	}
	if isFromMe {
		t.Error("isFromMe = true, want false")
	}
}
