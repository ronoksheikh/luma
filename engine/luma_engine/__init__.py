"""luma_engine — the Luma Studio rendering framework.

Modules: brand, svg, text, layout, motion, camera, layers, fx, audio, encode, qc,
scene, render, templates.  See ENGINE_NOTES.md for pitfalls and conventions.
"""
from .brand import BrandKit, Color, Gradient, linear_to_srgb, srgb_to_linear
from .layers import Frame, motion_blur
from .motion import Channel, Event, Spring, Timeline, ease, progress, stagger
from .scene import Scene
from .svg import SVGDocument

__version__ = "1.0.0"

__all__ = [
    "BrandKit", "Channel", "Color", "Event", "Frame", "Gradient", "SVGDocument", "Scene", "Spring", "Timeline",
    "ease", "linear_to_srgb", "motion_blur", "progress", "srgb_to_linear", "stagger",
]
