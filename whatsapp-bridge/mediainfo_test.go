package main

import (
	"bytes"
	"strings"
	"testing"

	waProto "go.mau.fi/whatsmeow/binary/proto"
	"google.golang.org/protobuf/proto"
)

// extractMediaInfo picks one of image/video/audio/document, in that priority
// order, and pulls out the fields downloadMedia needs later. These cases
// cover each type, the fallback filename logic, and the type-priority when a
// (malformed) message somehow carries more than one media field.
func TestExtractMediaInfo(t *testing.T) {
	type want struct {
		mediaType     string
		filenamePfx   string // for auto-generated names, checked as a prefix
		filenameExact string // for names that must match exactly; ignored if ""
		url           string
		mediaKey      []byte
		fileSHA256    []byte
		fileEncSHA256 []byte
		fileLength    uint64
	}
	cases := []struct {
		name string
		msg  *waProto.Message
		want want
	}{
		{"nil message", nil, want{}},
		{"empty message, no media fields", &waProto.Message{}, want{}},
		{
			"image message",
			&waProto.Message{ImageMessage: &waProto.ImageMessage{
				URL: proto.String("https://mmg.whatsapp.net/img.enc"), MediaKey: []byte("imgkey"),
				FileSHA256: []byte("imgsha"), FileEncSHA256: []byte("imgencsha"), FileLength: proto.Uint64(1234),
			}},
			want{mediaType: "image", filenamePfx: "image_", url: "https://mmg.whatsapp.net/img.enc",
				mediaKey: []byte("imgkey"), fileSHA256: []byte("imgsha"), fileEncSHA256: []byte("imgencsha"), fileLength: 1234},
		},
		{
			"video message",
			&waProto.Message{VideoMessage: &waProto.VideoMessage{
				URL: proto.String("https://mmg.whatsapp.net/vid.enc"), MediaKey: []byte("vidkey"),
				FileSHA256: []byte("vidsha"), FileEncSHA256: []byte("videncsha"), FileLength: proto.Uint64(99999),
			}},
			want{mediaType: "video", filenamePfx: "video_", url: "https://mmg.whatsapp.net/vid.enc",
				mediaKey: []byte("vidkey"), fileSHA256: []byte("vidsha"), fileEncSHA256: []byte("videncsha"), fileLength: 99999},
		},
		{
			"audio message",
			&waProto.Message{AudioMessage: &waProto.AudioMessage{
				URL: proto.String("https://mmg.whatsapp.net/aud.enc"), MediaKey: []byte("audkey"),
				FileSHA256: []byte("audsha"), FileEncSHA256: []byte("audencsha"), FileLength: proto.Uint64(42),
			}},
			want{mediaType: "audio", filenamePfx: "audio_", url: "https://mmg.whatsapp.net/aud.enc",
				mediaKey: []byte("audkey"), fileSHA256: []byte("audsha"), fileEncSHA256: []byte("audencsha"), fileLength: 42},
		},
		{
			"document message with a filename keeps it as-is",
			&waProto.Message{DocumentMessage: &waProto.DocumentMessage{
				FileName: proto.String("report.pdf"), URL: proto.String("https://mmg.whatsapp.net/doc.enc"),
				MediaKey: []byte("dockey"), FileSHA256: []byte("docsha"), FileEncSHA256: []byte("docencsha"),
				FileLength: proto.Uint64(555),
			}},
			want{mediaType: "document", filenameExact: "report.pdf", url: "https://mmg.whatsapp.net/doc.enc",
				mediaKey: []byte("dockey"), fileSHA256: []byte("docsha"), fileEncSHA256: []byte("docencsha"), fileLength: 555},
		},
		{
			"document message with empty filename falls back to a generated one",
			&waProto.Message{DocumentMessage: &waProto.DocumentMessage{FileName: proto.String("")}},
			want{mediaType: "document", filenamePfx: "document_"},
		},
		{
			"document message with no FileName field set at all falls back",
			&waProto.Message{DocumentMessage: &waProto.DocumentMessage{}},
			want{mediaType: "document", filenamePfx: "document_"},
		},
		{
			"document message with a unicode filename",
			&waProto.Message{DocumentMessage: &waProto.DocumentMessage{FileName: proto.String("日本語ファイル.pdf")}},
			want{mediaType: "document", filenameExact: "日本語ファイル.pdf"},
		},
		{
			"document with a very large file length",
			&waProto.Message{DocumentMessage: &waProto.DocumentMessage{FileName: proto.String("big.bin"), FileLength: proto.Uint64(9223372036854775807)}},
			want{mediaType: "document", filenameExact: "big.bin", fileLength: 9223372036854775807},
		},
		{
			"image with binary/injection-like sha bytes is preserved exactly",
			&waProto.Message{ImageMessage: &waProto.ImageMessage{FileSHA256: []byte{0x00, 0xFF, '\'', ';', '-', '-'}}},
			want{mediaType: "image", filenamePfx: "image_", fileSHA256: []byte{0x00, 0xFF, '\'', ';', '-', '-'}},
		},
		{
			"image and video both set: image wins by check order",
			&waProto.Message{
				ImageMessage: &waProto.ImageMessage{URL: proto.String("img-url")},
				VideoMessage: &waProto.VideoMessage{URL: proto.String("vid-url")},
			},
			want{mediaType: "image", filenamePfx: "image_", url: "img-url"},
		},
		{
			"video and audio both set: video wins by check order",
			&waProto.Message{
				VideoMessage: &waProto.VideoMessage{URL: proto.String("vid-url")},
				AudioMessage: &waProto.AudioMessage{URL: proto.String("aud-url")},
			},
			want{mediaType: "video", filenamePfx: "video_", url: "vid-url"},
		},
		{
			"audio and document both set: audio wins by check order",
			&waProto.Message{
				AudioMessage:    &waProto.AudioMessage{URL: proto.String("aud-url")},
				DocumentMessage: &waProto.DocumentMessage{FileName: proto.String("doc.pdf")},
			},
			want{mediaType: "audio", filenamePfx: "audio_", url: "aud-url"},
		},
		{
			"text-only message (extended text) has no media",
			&waProto.Message{ExtendedTextMessage: &waProto.ExtendedTextMessage{Text: proto.String("hi")}},
			want{},
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			mediaType, filename, url, mediaKey, fileSHA256, fileEncSHA256, fileLength := extractMediaInfo(tc.msg)

			if mediaType != tc.want.mediaType {
				t.Errorf("mediaType = %q, want %q", mediaType, tc.want.mediaType)
			}
			if tc.want.filenameExact != "" && filename != tc.want.filenameExact {
				t.Errorf("filename = %q, want exactly %q", filename, tc.want.filenameExact)
			}
			if tc.want.filenamePfx != "" && !strings.HasPrefix(filename, tc.want.filenamePfx) {
				t.Errorf("filename = %q, want prefix %q", filename, tc.want.filenamePfx)
			}
			if tc.want.filenameExact == "" && tc.want.filenamePfx == "" && filename != "" {
				t.Errorf("filename = %q, want empty", filename)
			}
			if url != tc.want.url {
				t.Errorf("url = %q, want %q", url, tc.want.url)
			}
			if !bytes.Equal(mediaKey, tc.want.mediaKey) {
				t.Errorf("mediaKey = %v, want %v", mediaKey, tc.want.mediaKey)
			}
			if !bytes.Equal(fileSHA256, tc.want.fileSHA256) {
				t.Errorf("fileSHA256 = %v, want %v", fileSHA256, tc.want.fileSHA256)
			}
			if !bytes.Equal(fileEncSHA256, tc.want.fileEncSHA256) {
				t.Errorf("fileEncSHA256 = %v, want %v", fileEncSHA256, tc.want.fileEncSHA256)
			}
			if fileLength != tc.want.fileLength {
				t.Errorf("fileLength = %d, want %d", fileLength, tc.want.fileLength)
			}
		})
	}
}
