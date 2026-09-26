package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"
)

// The /api/send handler must reject anything that isn't POST, and validate
// its body, before ever touching sendWhatsAppMessage. client is nil in every
// case here: sendWhatsAppMessage's very first line is client.IsConnected(),
// which whatsmeow defines as nil-safe (returns false for a nil *Client), so
// even the cases that DO reach it never risk sending anything — they just
// observe the deterministic "Not connected to WhatsApp" response.
func TestSendHandlerValidation(t *testing.T) {
	handler := makeSendHandler(nil)

	cases := []struct {
		name           string
		method         string
		body           string
		wantStatus     int
		wantBodySubstr string // for non-JSON (http.Error) responses
	}{
		{"GET is rejected", http.MethodGet, "", http.StatusMethodNotAllowed, "Method not allowed"},
		{"PUT is rejected", http.MethodPut, "", http.StatusMethodNotAllowed, "Method not allowed"},
		{"DELETE is rejected", http.MethodDelete, "", http.StatusMethodNotAllowed, "Method not allowed"},
		{"HEAD is rejected", http.MethodHead, "", http.StatusMethodNotAllowed, "Method not allowed"},
		{"malformed JSON body", http.MethodPost, "{not json", http.StatusBadRequest, "Invalid request format"},
		{"empty body", http.MethodPost, "", http.StatusBadRequest, "Invalid request format"},
		{"JSON array instead of object", http.MethodPost, "[]", http.StatusBadRequest, "Invalid request format"},
		{"recipient is a number, not a string", http.MethodPost, `{"recipient":12345,"message":"hi"}`, http.StatusBadRequest, "Invalid request format"},
		{"missing recipient", http.MethodPost, `{"message":"hi"}`, http.StatusBadRequest, "Recipient is required"},
		{"empty recipient", http.MethodPost, `{"recipient":"","message":"hi"}`, http.StatusBadRequest, "Recipient is required"},
		{"missing message and media_path", http.MethodPost, `{"recipient":"123@s.whatsapp.net"}`, http.StatusBadRequest, "Message or media path is required"},
		{"empty message and empty media_path", http.MethodPost, `{"recipient":"123@s.whatsapp.net","message":"","media_path":""}`, http.StatusBadRequest, "Message or media path is required"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			req := httptest.NewRequest(tc.method, "/api/send", strings.NewReader(tc.body))
			rec := httptest.NewRecorder()
			handler(rec, req)

			if rec.Code != tc.wantStatus {
				t.Errorf("status = %d, want %d (body: %q)", rec.Code, tc.wantStatus, rec.Body.String())
			}
			if !strings.Contains(rec.Body.String(), tc.wantBodySubstr) {
				t.Errorf("body = %q, want it to contain %q", rec.Body.String(), tc.wantBodySubstr)
			}
		})
	}
}

// Once validation passes, the handler must reach sendWhatsAppMessage and
// surface its "not connected" result as a 500 with a JSON body — proving the
// happy path is wired up without a live client ever being touched.
func TestSendHandlerReachesSendWithoutConnectedClient(t *testing.T) {
	handler := makeSendHandler(nil)

	cases := []struct {
		name string
		body string
	}{
		{"message only", `{"recipient":"123@s.whatsapp.net","message":"hello"}`},
		{"media path only, no message text", `{"recipient":"123@s.whatsapp.net","media_path":"/tmp/whatever.jpg"}`},
		{"bare phone number recipient (no @)", `{"recipient":"14155551234","message":"hi"}`},
		{"recipient with unusual characters", `{"recipient":"not-a-real-jid@@@","message":"hi"}`},
		{"unknown extra JSON fields are ignored", `{"recipient":"123@s.whatsapp.net","message":"hi","unexpected_field":"whatever"}`},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodPost, "/api/send", strings.NewReader(tc.body))
			rec := httptest.NewRecorder()
			handler(rec, req)

			if rec.Code != http.StatusInternalServerError {
				t.Fatalf("status = %d, want %d", rec.Code, http.StatusInternalServerError)
			}
			var resp SendMessageResponse
			if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
				t.Fatalf("response body is not valid JSON: %v (body: %q)", err, rec.Body.String())
			}
			if resp.Success {
				t.Error("Success = true with a nil client, want false")
			}
			if resp.Message != "Not connected to WhatsApp" {
				t.Errorf("Message = %q, want %q", resp.Message, "Not connected to WhatsApp")
			}
		})
	}
}

// A large body must decode without the handler crashing or hanging; there is
// no explicit size cap in the handler today, so this documents current
// behavior (a very large recipient/message still just gets validated and
// forwarded, it does not get rejected for size).
func TestSendHandlerOversizedBody(t *testing.T) {
	huge := strings.Repeat("a", 2*1024*1024) // 2MB message string
	body := `{"recipient":"123@s.whatsapp.net","message":"` + huge + `"}`

	handler := makeSendHandler(nil)
	req := httptest.NewRequest(http.MethodPost, "/api/send", strings.NewReader(body))
	rec := httptest.NewRecorder()
	handler(rec, req)

	if rec.Code != http.StatusInternalServerError {
		t.Errorf("status = %d, want %d (large body should still decode and reach the not-connected path)", rec.Code, http.StatusInternalServerError)
	}
}

// /api/download validation and its non-network-touching error paths: bad
// input, an unknown message, a message with no media, and incomplete media
// metadata. client is nil throughout; downloadMedia only calls into the
// (nil) client on an actual network download, which none of these reach.
func TestDownloadHandlerValidation(t *testing.T) {
	db := openTestDB(t)
	if err := initSchema(db); err != nil {
		t.Fatalf("initSchema failed: %v", err)
	}
	store := &MessageStore{db: db}
	handler := makeDownloadHandler(nil, store)

	cases := []struct {
		name           string
		method         string
		body           string
		wantStatus     int
		wantBodySubstr string
	}{
		{"GET is rejected", http.MethodGet, "", http.StatusMethodNotAllowed, "Method not allowed"},
		{"PUT is rejected", http.MethodPut, "", http.StatusMethodNotAllowed, "Method not allowed"},
		{"malformed JSON body", http.MethodPost, "{not json", http.StatusBadRequest, "Invalid request format"},
		{"message_id is a number, not a string", http.MethodPost, `{"message_id":123,"chat_jid":"a@g.us"}`, http.StatusBadRequest, "Invalid request format"},
		{"missing message_id", http.MethodPost, `{"chat_jid":"a@g.us"}`, http.StatusBadRequest, "Message ID and Chat JID are required"},
		{"missing chat_jid", http.MethodPost, `{"message_id":"MSG1"}`, http.StatusBadRequest, "Message ID and Chat JID are required"},
		{"both missing", http.MethodPost, `{}`, http.StatusBadRequest, "Message ID and Chat JID are required"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			req := httptest.NewRequest(tc.method, "/api/download", strings.NewReader(tc.body))
			rec := httptest.NewRecorder()
			handler(rec, req)

			if rec.Code != tc.wantStatus {
				t.Errorf("status = %d, want %d (body: %q)", rec.Code, tc.wantStatus, rec.Body.String())
			}
			if !strings.Contains(rec.Body.String(), tc.wantBodySubstr) {
				t.Errorf("body = %q, want it to contain %q", rec.Body.String(), tc.wantBodySubstr)
			}
		})
	}
}

func TestDownloadHandlerAgainstStore(t *testing.T) {
	db := openTestDB(t)
	if err := initSchema(db); err != nil {
		t.Fatalf("initSchema failed: %v", err)
	}
	store := &MessageStore{db: db}
	handler := makeDownloadHandler(nil, store)
	const chatJID = "120363@g.us"
	if err := store.StoreChat(chatJID, "Test Chat", time.Now()); err != nil {
		t.Fatalf("StoreChat failed: %v", err)
	}

	postJSON := func(t *testing.T, messageID, chat string) *httptest.ResponseRecorder {
		t.Helper()
		body, err := json.Marshal(DownloadMediaRequest{MessageID: messageID, ChatJID: chat})
		if err != nil {
			t.Fatalf("failed to marshal request: %v", err)
		}
		req := httptest.NewRequest(http.MethodPost, "/api/download", strings.NewReader(string(body)))
		rec := httptest.NewRecorder()
		handler(rec, req)
		return rec
	}

	t.Run("unknown message returns a failure response, not a crash", func(t *testing.T) {
		rec := postJSON(t, "NOPE", chatJID)
		if rec.Code != http.StatusInternalServerError {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusInternalServerError)
		}
		var resp DownloadMediaResponse
		if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
			t.Fatalf("response is not valid JSON: %v", err)
		}
		if resp.Success {
			t.Error("Success = true for an unknown message")
		}
		if !strings.Contains(resp.Message, "failed to find message") {
			t.Errorf("Message = %q, want it to mention the lookup failure", resp.Message)
		}
	})

	t.Run("a text message (no media) is reported as not a media message", func(t *testing.T) {
		if err := store.StoreMessage("TXT1", chatJID, "s", "s@s.whatsapp.net", "just text", time.Now(), false, "", "", "", nil, nil, nil, 0); err != nil {
			t.Fatalf("StoreMessage failed: %v", err)
		}
		rec := postJSON(t, "TXT1", chatJID)
		if rec.Code != http.StatusInternalServerError {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusInternalServerError)
		}
		var resp DownloadMediaResponse
		if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
			t.Fatalf("response is not valid JSON: %v", err)
		}
		if !strings.Contains(resp.Message, "not a media message") {
			t.Errorf("Message = %q, want it to mention the message isn't media", resp.Message)
		}
	})

	t.Run("a media message with incomplete metadata cannot be downloaded", func(t *testing.T) {
		if err := store.StoreMessage("INCOMPLETE1", chatJID, "s", "s@s.whatsapp.net", "", time.Now(), false, "image", "photo.jpg", "", nil, nil, nil, 0); err != nil {
			t.Fatalf("StoreMessage failed: %v", err)
		}
		rec := postJSON(t, "INCOMPLETE1", chatJID)
		if rec.Code != http.StatusInternalServerError {
			t.Fatalf("status = %d, want %d", rec.Code, http.StatusInternalServerError)
		}
		var resp DownloadMediaResponse
		if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
			t.Fatalf("response is not valid JSON: %v", err)
		}
		if !strings.Contains(resp.Message, "incomplete media information") {
			t.Errorf("Message = %q, want it to mention incomplete media info", resp.Message)
		}
	})

	t.Run("an already-downloaded file is served from the cache without touching the client", func(t *testing.T) {
		t.Chdir(t.TempDir())
		// Re-create the store's data in the new working directory, since
		// t.Chdir changes where "store/" resolves for downloadMedia.
		db2 := openTestDB(t)
		if err := initSchema(db2); err != nil {
			t.Fatalf("initSchema failed: %v", err)
		}
		store2 := &MessageStore{db: db2}
		handler2 := makeDownloadHandler(nil, store2)
		if err := store2.StoreChat(chatJID, "Test Chat", time.Now()); err != nil {
			t.Fatalf("StoreChat failed: %v", err)
		}
		if err := store2.StoreMessage("CACHED1", chatJID, "s", "s@s.whatsapp.net", "", time.Now(), false,
			"image", "cached.jpg", "", nil, nil, nil, 0); err != nil {
			t.Fatalf("StoreMessage failed: %v", err)
		}
		chatDir := "store/" + chatJID
		if err := os.MkdirAll(chatDir, 0755); err != nil {
			t.Fatalf("failed to create chat dir: %v", err)
		}
		if err := os.WriteFile(chatDir+"/cached_CACHED1.jpg", []byte("already here"), 0644); err != nil {
			t.Fatalf("failed to pre-place cached file: %v", err)
		}

		body, _ := json.Marshal(DownloadMediaRequest{MessageID: "CACHED1", ChatJID: chatJID})
		req := httptest.NewRequest(http.MethodPost, "/api/download", strings.NewReader(string(body)))
		rec := httptest.NewRecorder()
		handler2(rec, req)

		if rec.Code != http.StatusOK {
			t.Fatalf("status = %d, want %d (body: %q)", rec.Code, http.StatusOK, rec.Body.String())
		}
		var resp DownloadMediaResponse
		if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
			t.Fatalf("response is not valid JSON: %v", err)
		}
		if !resp.Success {
			t.Errorf("Success = false, want true for a cache hit")
		}
		if resp.Filename != "cached_CACHED1.jpg" {
			t.Errorf("Filename = %q, want %q", resp.Filename, "cached_CACHED1.jpg")
		}
	})
}

// Large but well-formed bodies must decode without the handler choking.
func TestDownloadHandlerOversizedBody(t *testing.T) {
	db := openTestDB(t)
	if err := initSchema(db); err != nil {
		t.Fatalf("initSchema failed: %v", err)
	}
	store := &MessageStore{db: db}
	handler := makeDownloadHandler(nil, store)

	hugeChatJID := strings.Repeat("a", 1024*1024) + "@g.us"
	body, _ := json.Marshal(DownloadMediaRequest{MessageID: "MSG1", ChatJID: hugeChatJID})
	req := httptest.NewRequest(http.MethodPost, "/api/download", strings.NewReader(string(body)))
	rec := httptest.NewRecorder()
	handler(rec, req)

	if rec.Code != http.StatusInternalServerError {
		t.Errorf("status = %d, want %d (oversized-but-valid body should still be processed as a lookup failure)", rec.Code, http.StatusInternalServerError)
	}
}
