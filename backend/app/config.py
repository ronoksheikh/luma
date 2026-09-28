"""Runtime configuration (environment variables with safe defaults)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool(v: str | None, default: bool) -> bool:
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("LUMA_DATA_DIR", "/data")))
    static_dir: Path = field(default_factory=lambda: Path(os.environ.get("LUMA_STATIC_DIR", Path(__file__).resolve().parents[2] / "frontend" / "dist")))
    prompts_dir: Path = field(default_factory=lambda: Path(os.environ.get("LUMA_PROMPTS_DIR", Path(__file__).resolve().parents[2] / "prompts")))
    samples_dir: Path = field(default_factory=lambda: Path(os.environ.get("LUMA_SAMPLES_DIR", Path(__file__).resolve().parents[2] / "samples")))
    host: str = field(default_factory=lambda: os.environ.get("LUMA_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.environ.get("LUMA_PORT", "8080")))
    # sandbox: the agent's shell/jobs run as this user (empty → same user as the backend)
    sandbox_user: str = field(default_factory=lambda: os.environ.get("LUMA_SANDBOX_USER", ""))
    sandbox_group: str = field(default_factory=lambda: os.environ.get("LUMA_SANDBOX_GROUP", ""))
    sandbox_python: str = field(default_factory=lambda: os.environ.get("LUMA_SANDBOX_PYTHON", ""))
    netctl: str = field(default_factory=lambda: os.environ.get("LUMA_NETCTL", ""))  # e.g. "sudo -n /usr/local/sbin/luma-netctl"
    max_files: int = 20
    max_file_bytes: int = 25 * 1024 * 1024
    max_request_bytes: int = 30 * 1024 * 1024
    job_concurrency: int = field(default_factory=lambda: int(os.environ.get("LUMA_JOB_CONCURRENCY", "1")))
    llm_api_key: str = field(default_factory=lambda: os.environ.get("LLM_API_KEY", ""))
    llm_base_url: str = field(default_factory=lambda: os.environ.get("LLM_BASE_URL", ""))
    llm_model: str = field(default_factory=lambda: os.environ.get("LLM_MODEL", ""))
    elevenlabs_api_key: str = field(default_factory=lambda: os.environ.get("ELEVENLABS_API_KEY", ""))
    elevenlabs_base_url: str = field(default_factory=lambda: os.environ.get("ELEVENLABS_BASE_URL", "https://api.elevenlabs.io"))
    app_url: str = field(default_factory=lambda: os.environ.get("LUMA_APP_URL", "http://localhost:8080"))
    dev_cors: bool = field(default_factory=lambda: _bool(os.environ.get("LUMA_DEV_CORS"), False))

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def secrets_dir(self) -> Path:
        return self.data_dir / "secrets"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'studio.db'}"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.secrets_dir, 0o700)
        except OSError:
            pass


config = Config()


def reload_config() -> Config:
    """Re-read the environment (used by tests)."""
    global config
    new = Config()
    config.__dict__.update(new.__dict__)
    return config


PROJECT_SUBDIRS = ("assets", "work", "renders", "audio", "outputs")

DEFAULT_SETTINGS = {
    "temperature": 0.7,
    "max_steps": 120,
    "wall_clock_minutes": 60,
    "context_budget_tokens": 120000,
    "el_char_budget": 5000,
    "terminal_network": True,
    "director_prompt": None,  # None → prompts/director.md
    "llm_base_url": "",
    "llm_model": "",
    "llm_preset": "openrouter",
    "tool_wait_seconds": 600,
    "plan_required_after_steps": 5,  # model steps without a plan before only planning tools are accepted (0 = off)
    "max_cost_usd": 0,  # per-run cost cap (0 = none)
    "max_tokens": 0,  # per-run token cap (0 = none)
}

DEFAULT_PROJECT_SETTINGS = {
    "width": 1920,
    "height": 1080,
    "fps": 60,
    "duration": 5.0,
    "formats": ["mp4"],
    "avoid_colors": [],
    "voice_language": "en",
    "voice_tone": "",
    "captions": True,
    "autopilot": False,
    "approval_render_minutes": 10,
    "approval_el_chars": 1500,
    "approval_cost_usd": 2.0,
    "web_access": False,
    "subagents": False,
    "subagent_max_concurrency": 2,
    "subagent_max_cost_usd": 1.0,
}

RESOLUTIONS = [(1920, 1080), (1080, 1920), (1080, 1080), (3840, 2160)]
FPS_CHOICES = [24, 30, 60]
