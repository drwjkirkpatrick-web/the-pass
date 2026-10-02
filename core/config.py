"""The Pass — configuration (Prompt 02).

NOTE: every path, threshold and time window the agent uses lives here, so the
restaurant can retune behavior without touching code.

WHY defaults-not-crashes: a missing config.yaml, or a machine without pyyaml,
must still boot. The agent is useless if it refuses to start.
"""
from __future__ import annotations

import os
import warnings
from dataclasses import dataclass, field, replace
from typing import Any, Dict

from core.types import filter_fields


@dataclass(frozen=True)
class Config:
    # storage
    db_path: str = "the_pass.db"
    photo_dir: str = "photos"
    # integrations (empty string = feature unavailable, agent degrades)
    global_recipes_db: str = ""
    mock_vision: bool = True
    # service rhythm (local wall-clock)
    service_start: str = "16:00"
    service_end: str = "22:00"
    temp_interval_sec: int = 300
    reminder_lead_min: int = 45
    review_lead_min: int = 120
    delivery_safety_buffer_days: float = 0.0  # extra slack on top of p90
    # thresholds
    conf_threshold: float = 0.6
    wash_backlog_threshold: int = 3
    refire_cooldown_min: int = 5
    dedupe_window_sec: int = 4
    walkin_max_c: float = 4.0
    hot_hold_min_c: float = 60.0
    score_drop_threshold: float = 0.8
    portion_delta_threshold: float = 0.12
    wip_limits: Dict[str, int] = field(default_factory=lambda: {"Doing": 3})
    # web surface
    serve_host: str = "127.0.0.1"
    serve_port: int = 8788

    def with_overrides(self, **kw: Any) -> "Config":
        """Return a copy with selected fields replaced (frozen dataclass)."""
        return replace(self, **kw)

    @classmethod
    def default(cls) -> "Config":
        """Defaults, with the global recipe DB path picked up from the env.

        WHY the env var: the recipe DB lives outside this repo; pointing at it
        should not require editing a file.
        """
        return cls(global_recipes_db=os.environ.get("OPEN_GLOBAL_RECIPES_DB", ""))

    @classmethod
    def from_yaml(cls, path: str) -> "Config":
        base = cls.default()
        if not path or not os.path.exists(path):
            return base
        try:
            import yaml  # optional dependency
        except ImportError:
            warnings.warn("pyyaml not installed; using default config")
            return base
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        if not isinstance(raw, dict):
            return base
        return replace(base, **filter_fields(cls, raw))
