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
    """A built-in template class, or (fallback) a toolbox template plugin's ``build`` function."""
    import importlib

    if name not in TEMPLATES:
        from .. import plugins

        p = plugins.registry().get(name)
        if p and p["kind"] == "template" and p["enabled"]:
            return plugins.load(name).build
        raise KeyError(f"unknown template {name!r}")
    mod, cls = TEMPLATES[name].split(":")
    return getattr(importlib.import_module(mod), cls)


def all_templates() -> dict[str, dict]:
    """Built-in templates plus enabled template plugins from the toolbox."""
    from .. import plugins

    out = {k: {"name": k, "source": "builtin", "entry": v} for k, v in TEMPLATES.items()}
    for p in plugins.list_plugins("template"):
        out.setdefault(p["name"], {"name": p["name"], "source": "plugin", "version": p["version"], "description": p["description"]})
    return out
