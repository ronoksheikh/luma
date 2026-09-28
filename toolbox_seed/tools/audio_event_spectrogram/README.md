# audio_event_spectrogram

Does every sound land on its picture event? This renders a **waveform + log-frequency spectrogram with the
timeline's event markers** and measures, for each event, the offset to the nearest detected audio onset.

**How:** audio is read at 48 kHz (anything soundfile or ffmpeg reads, including the final MP4); onsets come from
`luma_engine.audio.onsets` (spectral flux); the tolerance is `tolerance_frames / fps` (default 2 frames at 60 fps =
±33 ms). Events come from the scene's `events.json` (`{"events": [{t, name}]}`) or an inline list.

```json
{"audio": "renders/final/mix.wav", "events": "renders/final/events.json", "fps": 60}
```

**Limitations:** onset detection can report spurious onsets on noisy beds (a false onset near an event could mask
a sync error) — look at the spectrogram; soft swells have no sharp onset: check only percussive events with
`only_named`.
