package main

import (
	"strings"
	"testing"
)

// uniqueMediaFilename builds the on-disk filename directly from a WhatsApp
// document message's sender-chosen FileName field (see extractMediaInfo).
// That field is fully attacker-controlled: any contact can set it to
// "../../../etc/passwd" and, since prefetchIncomingMedia downloads media
// automatically the moment a message arrives, no local user action is needed
// to trigger it. If the filename component is not confined to a single path
// element before being combined into localPath ("chatDir/onDiskFilename" in
// downloadMedia), a malicious sender can write files outside the intended
// store/<chat> directory.
func TestUniqueMediaFilenameContainsTraversal(t *testing.T) {
	cases := []struct {
		name      string
		filename  string
		messageID string
		want      string
	}{
		{"parent-dir traversal, unix-style", "../../etc/passwd", "MSGID", "passwd_MSGID"},
		{"absolute path", "/etc/shadow", "MSGID", "shadow_MSGID"},
		{"traversal in the middle of the path", "foo/../../bar.txt", "MSGID", "bar_MSGID.txt"},
		{"trailing slash, directory-only", "folder/", "MSGID", "folder_MSGID"},
		{"deeply nested traversal", "a/b/c/../../../../../root/.ssh/authorized_keys", "MSGID", "authorized_keys_MSGID"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := uniqueMediaFilename(tc.filename, tc.messageID)
			if got != tc.want {
				t.Errorf("uniqueMediaFilename(%q, %q) = %q, want %q", tc.filename, tc.messageID, got, tc.want)
			}
			if strings.ContainsAny(got, "/\\") {
				t.Errorf("uniqueMediaFilename(%q, %q) = %q still contains a path separator; traversal not contained", tc.filename, tc.messageID, got)
			}
		})
	}
}

// Backslashes are not a path separator on the OS this bridge runs on (macOS/
// Linux), so a Windows-style traversal attempt is inert here: it stays a
// single, literal filename component. Documented so a future contributor
// doesn't "fix" this into truncating legitimate filenames that happen to
// contain a backslash character.
func TestUniqueMediaFilenameBackslashIsLiteralOnUnix(t *testing.T) {
	got := uniqueMediaFilename(`..\..\evil.exe`, "MSGID")
	want := `..\..\evil_MSGID.exe`
	if got != want {
		t.Errorf("uniqueMediaFilename(%q, ...) = %q, want %q", `..\..\evil.exe`, got, want)
	}
}

// Ordinary, non-adversarial inputs must keep working exactly as before: a
// normal name gets the message ID spliced in before its extension.
func TestUniqueMediaFilenameOrdinaryCases(t *testing.T) {
	cases := []struct {
		name      string
		filename  string
		messageID string
		want      string
	}{
		{"simple document", "invoice.pdf", "3B76B4B89156F7E9BD59", "invoice_3B76B4B89156F7E9BD59.pdf"},
		{"multiple dots keeps only the last as extension", "archive.tar.gz", "MSGID", "archive.tar_MSGID.gz"},
		{"uppercase extension is preserved as-is", "PHOTO.JPG", "MSGID", "PHOTO_MSGID.JPG"},
		{"dotfile has no separate extension", ".gitignore", "MSGID", "_MSGID.gitignore"},
		{"empty filename", "", "MSGID", "_MSGID."},
		{"filename is just a dot", ".", "MSGID", "_MSGID."},
		{"filename is just dot-dot", "..", "MSGID", "._MSGID."},
		{"empty message ID", "photo.jpg", "", "photo_.jpg"},
		{"very long filename is preserved in full", strings.Repeat("a", 300) + ".jpg", "MSGID", strings.Repeat("a", 300) + "_MSGID.jpg"},
		{"reserved-looking characters kept, since they're valid on this filesystem", "weird:name?.txt", "MSGID", "weird:name?_MSGID.txt"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := uniqueMediaFilename(tc.filename, tc.messageID); got != tc.want {
				t.Errorf("uniqueMediaFilename(%q, %q) = %q, want %q", tc.filename, tc.messageID, got, tc.want)
			}
		})
	}
}

// Unicode, emoji, RTL and zero-width filenames must survive untouched other
// than the traversal-stripping and ID splice — WhatsApp filenames are not
// restricted to ASCII.
func TestUniqueMediaFilenameUnicode(t *testing.T) {
	cases := []struct {
		name      string
		filename  string
		messageID string
		want      string
	}{
		{"CJK filename", "日本語ファイル.pdf", "MSGID", "日本語ファイル_MSGID.pdf"},
		{"emoji filename", "🎉party.png", "MSGID", "🎉party_MSGID.png"},
		{"RTL Arabic filename", "مرحبا.txt", "MSGID", "مرحبا_MSGID.txt"},
		{"zero-width joiner preserved literally", "a‍b.txt", "MSGID", "a‍b_MSGID.txt"},
		{"combining diacritics", "café.txt", "MSGID", "café_MSGID.txt"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := uniqueMediaFilename(tc.filename, tc.messageID); got != tc.want {
				t.Errorf("uniqueMediaFilename(%q, %q) = %q, want %q", tc.filename, tc.messageID, got, tc.want)
			}
		})
	}
}

// The security invariant that matters: whatever comes in, the returned
// on-disk filename must never contain a path separator, so it can never
// escape the chat's media directory once joined onto chatDir.
func FuzzUniqueMediaFilenameNoTraversal(f *testing.F) {
	for _, seed := range []string{
		"../../etc/passwd", "/etc/shadow", "foo/../../bar.txt",
		"", ".", "..", "folder/", ".gitignore", "archive.tar.gz",
		"日本語.pdf", "weird:name?.txt",
	} {
		f.Add(seed)
	}
	f.Fuzz(func(t *testing.T, filename string) {
		got := uniqueMediaFilename(filename, "MSGID")
		if strings.ContainsRune(got, '/') {
			t.Fatalf("uniqueMediaFilename(%q, \"MSGID\") = %q contains a path separator", filename, got)
		}
	})
}
