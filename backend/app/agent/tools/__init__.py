"""Director tools. Importing this package registers every tool."""
from . import control_tools, file_tools, media_tools, plan_tools, present_tools, terminal_tools  # noqa: F401
from .base import REGISTRY, Tool

try:
    from . import el_tools  # noqa: F401
except ImportError:  # pragma: no cover
    pass


def available_tools(el_enabled: bool) -> dict[str, Tool]:
    """ElevenLabs tools are only offered when a key is configured."""
    return {n: t for n, t in REGISTRY.items() if el_enabled or not t.elevenlabs}
