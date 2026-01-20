from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional


@dataclass(slots=True)
class BackendInfo:
    """Information about the detected Conda/Mamba backend."""

    kind: Literal["mamba", "conda"]
    executable: Path
    version: str
    base_prefix: Optional[Path]


@dataclass(slots=True)
class Environment:
    """Represents a Conda/Mamba environment."""

    name: str
    path: Path
    is_active: bool = False
    python_version: Optional[str] = None
    size_display: Optional[str] = None  # e.g. '3.2G'


@dataclass(slots=True)
class DiskUsageReport:
    """Aggregate disk usage information for Conda data."""

    pkgs_cache: int  # bytes
    envs: int  # bytes
    total: int  # bytes


@dataclass(slots=True)
class Package:
    """Represents a package installed in an environment."""

    name: str
    version: str
    build_string: Optional[str]
    channel: Optional[str]
    source: Literal["conda", "pip"] = "conda"

