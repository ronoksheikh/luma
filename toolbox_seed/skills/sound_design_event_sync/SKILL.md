---
name: sound_design_event_sync
description: Sound design that lands on the picture — every hit, whoosh and chime aligned to timeline events, loudness-safe, verified with a spectrogram.
tags: [audio, sound-design, sync, events, loudness]
tools_used: [audio_event_spectrogram, audio_mix, qc_report]
---

## When to use
Any film with synthesized or ElevenLabs sound that must hit visual beats (seats, catches, impacts, the final hold).

## Steps
1. Put every visual beat in the scene's `Timeline` (events with names) — the same events.json drives audio and QC.
2. Place sounds with `Mix` anchors: transients at the event time (`anchor="onset"`), swells ending on it (`anchor="end"`).
3. Master: −14 LUFS integrated, −1 dBTP true peak (4× oversampled limiter), 24-bit WAV.
4. Run `audio_event_spectrogram(audio, events, fps)` on the mix (or the final MP4): every percussive event within
   ±2 frames; look at the spectrogram for masking and clicks.
5. `qc_report` repeats the A/V sync check on the delivered file.

## Pitfalls
- AAC priming/padding shifts audio by ~1024 samples at the start: check sync on the final MP4, not only the WAV.
- Swells and reverse cymbals have no sharp onset — check them by ear, exclude them with `only_named`.
- A long reverb tail on the last hit hides the "still" ending; fade the tail under the hold.
- Sample peaks under 0 dBFS can still produce intersample overs: trust the true-peak meter, not the sample peak.

## Verification
- `audio_event_spectrogram` pass = every checked event within tolerance.
- `qc_report`: `av_sync_events`, `loudness`, `true_peak` pass.

## Example
Fan unfold at 60 fps: events seat_1..seat_5 (click + FM bell pentatonic), catch (sub thump), shock (whoosh ending on
the event), hold (glass ping) → offsets −4…+6 ms, all within ±33 ms; −14.0 LUFS, −1.2 dBTP.
