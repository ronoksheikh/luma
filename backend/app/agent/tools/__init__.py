"""Director tools. Importing this package registers every tool."""
from . import control_tools, file_tools, media_tools, plan_tools, power_tools, present_tools, review_tools, subagent_tools, terminal_tools, toolbox_tools  # noqa: F401
from .base import REGISTRY, Tool

try:
    from . import el_tools  # noqa: F401
except ImportError:  # pragma: no cover
    pass


def available_tools(el_enabled: bool, project_settings: dict | None = None, only: list[str] | None = None) -> dict[str, Tool]:
    """ElevenLabs tools only with a key; web / sub-agent tools only when the project enables them;
    `only` restricts the set further (sub-agents)."""
    st = project_settings or {}
    out = {n: t for n, t in REGISTRY.items() if (el_enabled or not t.elevenlabs) and (not t.requires or st.get(t.requires))}
    if only is not None:
        out = {n: t for n, t in out.items() if n in set(only)}
    return out
