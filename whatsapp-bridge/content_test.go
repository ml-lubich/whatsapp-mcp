package main

import (
	"strings"
	"testing"

	waProto "go.mau.fi/whatsmeow/binary/proto"
	"google.golang.org/protobuf/proto"
)

// extractTextContent only ever surfaces Conversation or ExtendedTextMessage
// text; every other message type (image, video, etc.) is deliberately
// ignored for now, per the comment in main.go.
func TestExtractTextContent(t *testing.T) {
	cases := []struct {
		name string
		msg  *waProto.Message
		want string
	}{
		{"nil message", nil, ""},
		{"empty message", &waProto.Message{}, ""},
		{"empty conversation string", &waProto.Message{Conversation: proto.String("")}, ""},
		{"plain conversation text", &waProto.Message{Conversation: proto.String("hello")}, "hello"},
		{
			"unicode conversation text",
			&waProto.Message{Conversation: proto.String("héllo wörld 日本語")},
			"héllo wörld 日本語",
		},
		{
			"emoji conversation text",
			&waProto.Message{Conversation: proto.String("🎉🔥💯")},
			"🎉🔥💯",
		},
		{
			"RTL Arabic conversation text",
			&waProto.Message{Conversation: proto.String("مرحبا بالعالم")},
			"مرحبا بالعالم",
		},
		{
			"zero-width characters preserved, not stripped",
			&waProto.Message{Conversation: proto.String("a​b‌ c‍")},
			"a​b‌ c‍",
		},
		{
			"whitespace-only conversation is not trimmed",
			&waProto.Message{Conversation: proto.String("   ")},
			"   ",
		},
		{
			"embedded newlines preserved",
			&waProto.Message{Conversation: proto.String("line1\nline2")},
			"line1\nline2",
		},
		{
			"very long conversation text is not truncated",
			&waProto.Message{Conversation: proto.String(strings.Repeat("x", 10000))},
			strings.Repeat("x", 10000),
		},
		{
			"extended text message with text",
			&waProto.Message{ExtendedTextMessage: &waProto.ExtendedTextMessage{Text: proto.String("reply text")}},
			"reply text",
		},
		{
			"extended text message present but Text is nil",
			&waProto.Message{ExtendedTextMessage: &waProto.ExtendedTextMessage{}},
			"",
		},
		{
			"extended text message with empty Text",
			&waProto.Message{ExtendedTextMessage: &waProto.ExtendedTextMessage{Text: proto.String("")}},
			"",
		},
		{
			"conversation takes priority over extended text",
			&waProto.Message{
				Conversation:        proto.String("plain wins"),
				ExtendedTextMessage: &waProto.ExtendedTextMessage{Text: proto.String("extended loses")},
			},
			"plain wins",
		},
		{
			"image-only message has no text content",
			&waProto.Message{ImageMessage: &waProto.ImageMessage{Caption: proto.String("a caption")}},
			"",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := extractTextContent(tc.msg); got != tc.want {
				t.Errorf("extractTextContent(%v) = %q, want %q", tc.msg, got, tc.want)
			}
		})
	}
}
