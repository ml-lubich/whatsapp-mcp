package main

import "testing"

func TestMin(t *testing.T) {
	cases := []struct {
		name string
		x, y int
		want int
	}{
		{"x smaller", 3, 5, 3},
		{"y smaller", 5, 3, 3},
		{"equal", 5, 5, 5},
		{"negative x", -1, 5, -1},
		{"both negative", -5, -1, -5},
		{"zero and positive", 0, 7, 0},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := min(tc.x, tc.y); got != tc.want {
				t.Errorf("min(%d, %d) = %d, want %d", tc.x, tc.y, got, tc.want)
			}
		})
	}
}

// placeholderWaveform must always hand WhatsApp exactly the 64-byte buffer it
// expects, with every byte in the 0-100 range it documents, regardless of the
// duration fed in — including durations no real caller would ever pass.
func TestPlaceholderWaveformShapeAndRange(t *testing.T) {
	cases := []struct {
		name     string
		duration uint32
	}{
		{"minimum duration", 1},
		{"typical voice note", 30},
		{"maximum clamped duration", 300},
		{"zero duration, defensive", 0},
		{"duration far beyond the caller's clamp", 100000},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			waveform := placeholderWaveform(tc.duration)
			if len(waveform) != 64 {
				t.Fatalf("placeholderWaveform(%d) length = %d, want 64", tc.duration, len(waveform))
			}
			for i, b := range waveform {
				if b > 100 {
					t.Errorf("placeholderWaveform(%d)[%d] = %d, want <= 100", tc.duration, i, b)
				}
			}
		})
	}
}

// The RNG is reseeded from the duration on every call, so the same duration
// must always produce byte-for-byte the same waveform: callers may generate
// a waveform more than once for the same voice note without it changing.
func TestPlaceholderWaveformDeterministic(t *testing.T) {
	for _, duration := range []uint32{1, 30, 300} {
		first := placeholderWaveform(duration)
		second := placeholderWaveform(duration)
		if string(first) != string(second) {
			t.Errorf("placeholderWaveform(%d) is not deterministic: %v != %v", duration, first, second)
		}
	}
}

// Different durations seed the RNG differently and scale the frequency
// factor, so they should not collide in practice; this guards against the
// function degenerating into a constant output.
func TestPlaceholderWaveformVariesByDuration(t *testing.T) {
	short := placeholderWaveform(1)
	long := placeholderWaveform(300)
	if string(short) == string(long) {
		t.Error("placeholderWaveform(1) and placeholderWaveform(300) produced identical output")
	}
}
