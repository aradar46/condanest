from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

CONFIG_DIR = Path.home() / ".config" / "conda-control"
CONFIG_PATH = CONFIG_DIR / "config.json"


@dataclass
class AppConfig:
    """User configuration for Conda Control."""

    conda_executable: Optional[str] = None


def load_config() -> AppConfig:
    if not CONFIG_PATH.is_file():
        return AppConfig()

    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AppConfig()

    return AppConfig(
        conda_executable=data.get("conda_executable"),
    )


def save_config(config: AppConfig) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = asdict(config)
    CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")

