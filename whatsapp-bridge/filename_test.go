package main

import (
	"fmt"
	"os"
	"testing"
	"time"
)

// Two media messages that arrive in the same second get the same
// auto-generated filename (e.g. image_20260921_053233.jpg). Before this fix,
// downloadMedia wrote both to the exact same disk path, so the second
// download silently clobbered the first. uniqueMediaFilename embeds the
// message ID so every message gets its own path, regardless of what the
// stored `filename` column says.
func TestUniqueMediaFilenameAppendsMessageID(t *testing.T) {
	cases := []struct {
		name      string
		filename  string
		messageID string
		want      string
	}{
		{"auto-generated image name", "image_20260921_053233.jpg", "3BA7C31587DB3BBDA6C6", "image_20260921_053233_3BA7C31587DB3BBDA6C6.jpg"},
		{"document keeps its original name", "nikita (4).xlsx", "3B76B4B89156F7E9BD59", "nikita (4)_3B76B4B89156F7E9BD59.xlsx"},
		{"no extension", "document_20260101_120000", "MSGNOEXT", "document_20260101_120000_MSGNOEXT"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := uniqueMediaFilename(tc.filename, tc.messageID); got != tc.want {
				t.Errorf("uniqueMediaFilename(%q, %q) = %q, want %q", tc.filename, tc.messageID, got, tc.want)
			}
		})
	}
}

// Real case: messages 3BA7C31587DB3BBDA6C6 and 3B628E0E257A27EC2979 both
// stored with filename "image_20260921_053233.jpg" because they arrived in
// the same second. downloadMedia must resolve each to its own file on disk
// instead of one overwriting the other.
//
// The media info is left incomplete on purpose (empty URL/keys never appear
// in real rows, but we don't need a real download here): the test only cares
// that each message ID maps to a distinct, pre-existing file, which exercises
// downloadMedia's cache-hit path without touching the network.
func TestDownloadMediaFilenamesDoNotCollide(t *testing.T) {
	t.Chdir(t.TempDir())

	db := openTestDB(t)
	if err := initSchema(db); err != nil {
		t.Fatalf("initSchema failed: %v", err)
	}
	store := &MessageStore{db: db}

	chatJID := "120363430434063376@g.us"
	sharedFilename := "image_20260921_053233.jpg"
	if err := store.StoreChat(chatJID, "Test Group", time.Now()); err != nil {
		t.Fatalf("StoreChat failed: %v", err)
	}
	for _, id := range []string{"3BA7C31587DB3BBDA6C6", "3B628E0E257A27EC2979"} {
		if err := store.StoreMessage(
			id, chatJID, "182476131586252", "182476131586252@lid",
			"", time.Now(), false, "image", sharedFilename, "",
			nil, nil, nil, 0,
		); err != nil {
			t.Fatalf("StoreMessage(%s) failed: %v", id, err)
		}
	}

	chatDir := fmt.Sprintf("store/%s", chatJID)
	if err := os.MkdirAll(chatDir, 0755); err != nil {
		t.Fatalf("failed to create chat dir: %v", err)
	}
	// Pre-place the two files the fixed code is expected to look for.
	pathA := chatDir + "/image_20260921_053233_3BA7C31587DB3BBDA6C6.jpg"
	pathB := chatDir + "/image_20260921_053233_3B628E0E257A27EC2979.jpg"
	if err := os.WriteFile(pathA, []byte("first image bytes"), 0644); err != nil {
		t.Fatalf("failed to write fixture A: %v", err)
	}
	if err := os.WriteFile(pathB, []byte("second image bytes"), 0644); err != nil {
		t.Fatalf("failed to write fixture B: %v", err)
	}

	okA, _, filenameA, resolvedPathA, errA := downloadMedia(nil, store, "3BA7C31587DB3BBDA6C6", chatJID)
	if !okA || errA != nil {
		t.Fatalf("downloadMedia(A) = ok=%v err=%v, want ok=true err=nil", okA, errA)
	}
	okB, _, filenameB, resolvedPathB, errB := downloadMedia(nil, store, "3B628E0E257A27EC2979", chatJID)
	if !okB || errB != nil {
		t.Fatalf("downloadMedia(B) = ok=%v err=%v, want ok=true err=nil", okB, errB)
	}

	if filenameA == filenameB {
		t.Errorf("both messages resolved to the same filename %q; collision not fixed", filenameA)
	}
	if resolvedPathA == resolvedPathB {
		t.Errorf("both messages resolved to the same path %q; collision not fixed", resolvedPathA)
	}

	contentA, err := os.ReadFile(resolvedPathA)
	if err != nil {
		t.Fatalf("failed to read resolved path A: %v", err)
	}
	if string(contentA) != "first image bytes" {
		t.Errorf("message A resolved to the wrong file: got %q", contentA)
	}
	contentB, err := os.ReadFile(resolvedPathB)
	if err != nil {
		t.Fatalf("failed to read resolved path B: %v", err)
	}
	if string(contentB) != "second image bytes" {
		t.Errorf("message B resolved to the wrong file: got %q", contentB)
	}
}
