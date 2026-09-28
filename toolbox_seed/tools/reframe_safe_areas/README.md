# reframe_safe_areas

Before queueing 9:16 / 1:1 / 4:5 renders of a 16:9 film, prove every version still frames the lockup properly.

**How:** for each aspect the scene is loaded at the new size (templates re-lay the lockup out — horizontal ↔ stacked,
fit to title-safe — instead of cropping), the end card is rendered and the non-background content box is compared with
the action-safe area (`luma_engine.layout.safe_area`). Outputs one end card per aspect and `contact.png` (safe area
green = OK / red = content outside; content box orange).

```json
{"scene": "work/scene.py", "aspects": ["9:16", "1:1", "4:5"], "long_side": 960}
```

**Limitations:** only the checked frame (the end card by default, or `time`) is inspected — scrub mid-animation frames
with `time` for moves that travel near the edges. Custom scenes must derive their layout from `self.width/height`.
