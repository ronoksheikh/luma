"""Parameterised templates that work with any uploaded logo SVG.

Each module exposes a Scene subclass and ``build(**params)``.
"""
TEMPLATES = {
    "fan_unfold": "luma_engine.templates.fan_unfold:FanUnfold",
    "exploded_assembly": "luma_engine.templates.exploded_assembly:ExplodedAssembly",
    "stroke_reveal": "luma_engine.templates.stroke_reveal:StrokeReveal",
    "voiced_explainer": "luma_engine.templates.voiced_explainer:VoicedExplainer",
}


def get_template(name: str):
    import importlib

    mod, cls = TEMPLATES[name].split(":")
    return getattr(importlib.import_module(mod), cls)
