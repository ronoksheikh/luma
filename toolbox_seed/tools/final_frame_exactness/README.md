# final_frame_exactness

A brand outro must END on the exact lockup the designer approved. This decodes the video's last frame and compares
it with the reference end card pixel by pixel.

**How:** ffmpeg seeks to the end (`-sseof`), decodes one frame; the per-pixel max channel difference gives the mean
(`mean_abs_diff`, pass ≤ `max_mad`, default 2/255 — H.264 4:2:0 chroma costs ~0.1–1), the max and the share of pixels
off by more than 8 codes. `diff_heatmap.png` shows where (×16, inferno).

```json
{"video": "outputs/outro.mp4", "reference": "renders/final/end_card.png"}
```

**Limitations:** the reference must be at the video size; a video that fades out at the very end should be checked
`from_end_s` before the fade. Chroma subsampling blurs thin coloured edges: a high max with a low mean is normal.
