package main

import (
	"encoding/binary"
	"testing"
)

// buildOggPage constructs one minimal, spec-shaped Ogg page: the 27-byte
// fixed header, a single-entry segment table (so payload must be under 255
// bytes), and the payload itself. analyzeOggOpus only reads header fields and
// scans raw page bytes for markers, so this is enough to drive it without a
// real encoder.
func buildOggPage(t *testing.T, pageSeqNum uint32, granulePos uint64, payload []byte) []byte {
	t.Helper()
	if len(payload) > 254 {
		t.Fatalf("buildOggPage helper only supports payloads under 255 bytes, got %d", len(payload))
	}
	page := make([]byte, 27, 27+1+len(payload))
	copy(page[0:4], "OggS")
	page[4] = 0 // version
	page[5] = 0 // header type
	binary.LittleEndian.PutUint64(page[6:14], granulePos)
	binary.LittleEndian.PutUint32(page[14:18], 0xDEADBEEF) // serial number, unused by analyzeOggOpus
	binary.LittleEndian.PutUint32(page[18:22], pageSeqNum)
	// page[22:26] checksum, left zero; analyzeOggOpus never verifies it
	page[26] = 1 // one segment
	page = append(page, byte(len(payload)))
	page = append(page, payload...)
	return page
}

// buildOpusHeadPayload builds the OpusHead packet per RFC 7845 section 5.1:
// magic(8) + version(1) + channels(1) + preSkip(2 LE) + sampleRate(4 LE) +
// outputGain(2 LE) + channelMap(1). padding is appended after the real
// struct so a test can plant a recognisable pattern there and prove
// analyzeOggOpus reads the real fields, not bytes past them.
func buildOpusHeadPayload(preSkip uint16, sampleRate uint32, padding []byte) []byte {
	payload := []byte("OpusHead")
	payload = append(payload, 1, 1) // version, channels
	ps := make([]byte, 2)
	binary.LittleEndian.PutUint16(ps, preSkip)
	payload = append(payload, ps...)
	sr := make([]byte, 4)
	binary.LittleEndian.PutUint32(sr, sampleRate)
	payload = append(payload, sr...)
	payload = append(payload, 0, 0, 0) // outputGain(2) + channelMap(1)
	payload = append(payload, padding...)
	return payload
}

func TestAnalyzeOggOpusInvalidInput(t *testing.T) {
	cases := []struct {
		name string
		data []byte
	}{
		{"nil data", nil},
		{"empty data", []byte{}},
		{"one byte", []byte{'O'}},
		{"three bytes, too short for the signature", []byte("Ogg")},
		{"four bytes, wrong signature", []byte("RIFF")},
		{"looks like a WAV file", []byte("RIFF____WAVEfmt ")},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			_, _, err := analyzeOggOpus(tc.data)
			if err == nil {
				t.Fatalf("analyzeOggOpus(%q) succeeded, want an error for invalid input", tc.data)
			}
		})
	}
}

// A bare "OggS" signature with nothing else is a valid enough prefix to pass
// the initial check but has no complete page; the function must fall back to
// its rough size-based estimate rather than error out or crash.
func TestAnalyzeOggOpusNoCompletePage(t *testing.T) {
	duration, waveform, err := analyzeOggOpus([]byte("OggS"))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if duration < 1 {
		t.Errorf("duration = %d, want at least 1 (clamped minimum)", duration)
	}
	if len(waveform) != 64 {
		t.Errorf("waveform length = %d, want 64", len(waveform))
	}
}

// This is the real end-to-end case: a well-formed two-page Opus stream with a
// correct OpusHead (48kHz, 312-sample pre-skip) and a final granule position
// of 240312 samples, which is exactly 5.0 seconds of audio after removing the
// pre-skip. The padding after the real OpusHead struct is filled with 0xFF so
// that if the reader is looking at the wrong offset (reading padding instead
// of the real preSkip/sampleRate fields), it computes a garbage duration
// instead of the correct 5 seconds.
func TestAnalyzeOggOpusComputesDurationFromGranule(t *testing.T) {
	headPayload := buildOpusHeadPayload(312, 48000, repeatBytes(0xFF, 24))
	page0 := buildOggPage(t, 0, 0, headPayload)
	page1 := buildOggPage(t, 1, 240312, []byte("audio data placeholder"))
	data := append(page0, page1...)

	duration, waveform, err := analyzeOggOpus(data)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if duration != 5 {
		t.Errorf("duration = %d, want 5 (computed from granule 240312, preSkip 312, sampleRate 48000)", duration)
	}
	if len(waveform) != 64 {
		t.Errorf("waveform length = %d, want 64", len(waveform))
	}
}

// Same shape as above but at 8kHz narrowband with no pre-skip, to confirm the
// duration formula isn't hardcoded to 48kHz.
func TestAnalyzeOggOpusRespectsSampleRate(t *testing.T) {
	headPayload := buildOpusHeadPayload(0, 8000, repeatBytes(0xAA, 24))
	page0 := buildOggPage(t, 0, 0, headPayload)
	page1 := buildOggPage(t, 1, 16000, []byte("x")) // 16000 samples / 8000 Hz = 2s
	data := append(page0, page1...)

	duration, _, err := analyzeOggOpus(data)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if duration != 2 {
		t.Errorf("duration = %d, want 2 (16000 samples at 8000Hz)", duration)
	}
}

// The scan only treats an OpusHead as authoritative on page sequence 0 or 1.
// One on a later page is real encoder output it should never see in
// practice, but the code must not pick it up and must fall back cleanly.
func TestAnalyzeOggOpusIgnoresLateOpusHead(t *testing.T) {
	page0 := buildOggPage(t, 0, 0, []byte("not opus head"))
	headPayload := buildOpusHeadPayload(100, 48000, repeatBytes(0x00, 24))
	page2 := buildOggPage(t, 2, 96100, headPayload) // sequence 2: too late to count

	data := append(page0, page2...)
	duration, _, err := analyzeOggOpus(data)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	// Without a recognised OpusHead, sampleRate stays at the 48000 default and
	// preSkip at 0, so duration = granule/sampleRate = 96100/48000 ≈ 2s either
	// way here; the real assertion is that this doesn't error or panic when
	// the only OpusHead-shaped bytes are on a page past the cutoff.
	if duration < 1 {
		t.Errorf("duration = %d, want at least 1", duration)
	}
}

// An OpusHead on page sequence 1 (the last page still checked) must still be
// picked up.
func TestAnalyzeOggOpusAcceptsOpusHeadOnPageOne(t *testing.T) {
	page0 := buildOggPage(t, 0, 0, []byte("some other packet"))
	headPayload := buildOpusHeadPayload(0, 16000, repeatBytes(0x11, 24))
	page1 := buildOggPage(t, 1, 32000, headPayload) // 32000 / 16000Hz = 2s

	data := append(page0, page1...)
	duration, _, err := analyzeOggOpus(data)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if duration != 2 {
		t.Errorf("duration = %d, want 2 (OpusHead on page 1 must still be read)", duration)
	}
}

// Duration is clamped to WhatsApp's accepted range: at least 1 second, at
// most 300.
func TestAnalyzeOggOpusClampsDuration(t *testing.T) {
	cases := []struct {
		name       string
		granule    uint64
		sampleRate uint32
		want       uint32
	}{
		{"tiny granule clamps up to 1 second", 10, 48000, 1},
		{"huge granule clamps down to 300 seconds", 48000 * 10000, 48000, 300},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			headPayload := buildOpusHeadPayload(0, tc.sampleRate, repeatBytes(0x22, 24))
			page0 := buildOggPage(t, 0, 0, headPayload)
			page1 := buildOggPage(t, 1, tc.granule, []byte("d"))
			data := append(page0, page1...)

			duration, _, err := analyzeOggOpus(data)
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if duration != tc.want {
				t.Errorf("duration = %d, want %d", duration, tc.want)
			}
		})
	}
}

// A page whose declared segment table runs past the end of the buffer must
// not panic; the scan should simply stop at the last complete page it found.
func TestAnalyzeOggOpusTruncatedPageDoesNotPanic(t *testing.T) {
	good := buildOggPage(t, 0, 48000, buildOpusHeadPayload(0, 48000, repeatBytes(0x33, 24)))
	// A page header claiming 10 segments but with no segment table or payload
	// data actually present.
	truncated := []byte("OggS")
	truncated = append(truncated, make([]byte, 22)...) // version..pageSeqNum..checksum, all zero
	truncated = append(truncated, 10)                  // numSegments = 10, but nothing follows

	data := append(good, truncated...)
	duration, waveform, err := analyzeOggOpus(data)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if duration != 1 {
		t.Errorf("duration = %d, want 1 (from the one good page, 48000/48000Hz = 1s)", duration)
	}
	if len(waveform) != 64 {
		t.Errorf("waveform length = %d, want 64", len(waveform))
	}
}

// Garbage bytes that don't start with "OggS" must be skipped byte-by-byte
// until the scanner finds the next real page, rather than giving up.
func TestAnalyzeOggOpusRecoversFromGarbageBetweenPages(t *testing.T) {
	page0 := buildOggPage(t, 0, 0, buildOpusHeadPayload(0, 48000, repeatBytes(0x44, 24)))
	garbage := []byte{0x01, 0x02, 0x03, 0x04, 0x05}
	page1 := buildOggPage(t, 1, 48000, []byte("d")) // exactly 1 second at 48kHz

	data := append(append(page0, garbage...), page1...)
	duration, _, err := analyzeOggOpus(data)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if duration != 1 {
		t.Errorf("duration = %d, want 1 (page after garbage still found)", duration)
	}
}

// repeatBytes returns a slice of n copies of b — a small helper to build
// recognisable, non-zero padding that would show up as a wrong answer if
// analyzeOggOpus ever read past the real OpusHead struct.
func repeatBytes(b byte, n int) []byte {
	buf := make([]byte, n)
	for i := range buf {
		buf[i] = b
	}
	return buf
}
