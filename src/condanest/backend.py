from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Set

import tempfile
import urllib.request

from .config import AppConfig, load_config, save_config
from .logging_utils import configure_logging
from .models import BackendInfo, DiskUsageReport, Environment, Package

logger = configure_logging()

ENV_LIST_FILE = Path.home() / ".conda" / "environments.txt"


class BackendNotFoundError(RuntimeError):
    """Raised when no suitable Conda/Mamba backend can be located."""


class EnvOperationError(RuntimeError):
    """Raised when an environment operation (clone/remove/clean/export) fails."""


class TermsOfServiceError(EnvOperationError):
    """Raised when a command fails due to unaccepted Terms of Service."""


def _candidate_executables(config: AppConfig) -> Iterable[str]:
    """Yield possible backend executables in priority order.

    We intentionally *prefer `conda` over `mamba`* for the primary backend
    executable because some Miniforge/Mamba builds do not support every
    `conda` subcommand/flag we rely on (e.g. `config --show` or `create --clone`).

    Order:
      1. Explicit config override
      2. $CONDA_EXE / $MAMBA_EXE
      3. Common install locations under $HOME (miniforge3, miniconda3, anaconda3),
         preferring `conda` over `mamba`
      4. Whatever is on PATH, preferring `conda` over `mamba`
    """
    # 1) Explicit user override
    if config.conda_executable:
        cfg_path = Path(config.conda_executable)
        # If the override points to a mamba binary but a sibling `conda`
        # exists, try the `conda` binary first for better CLI compatibility.
        if "mamba" in cfg_path.name and (cfg_path.parent / "conda").is_file():
            yield str(cfg_path.parent / "conda")
        yield config.conda_executable

    # 2) Environment variables set by Conda/Mamba shells.
    for env_var in ("CONDA_EXE", "MAMBA_EXE"):
        exe = os.environ.get(env_var)
        if exe and Path(exe).is_file():
            yield exe

    # 3) Common install roots in the home directory.
    home = Path.home()
    for root_name in ("miniforge3", "miniconda3", "anaconda3"):
        root = home / root_name / "bin"
        for name in ("conda", "mamba"):
            candidate = root / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                yield str(candidate)

    # 4) Fall back to PATH lookup (prefer conda; mamba second).
    for name in ("conda", "mamba"):
        path = shutil.which(name)
        if path:
            yield path


def detect_backend() -> BackendInfo:
    """Detect Mamba/Conda backend and basic info.

    Raises:
        BackendNotFoundError: if no backend is found.
    """
    config = load_config()

    for exe in _candidate_executables(config):
        try:
            completed = subprocess.run(
                [exe, "--version"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            logger.warning("Failed probing backend '%s': %s", exe, exc)
            continue

        out = completed.stdout.strip()
        version = out.splitlines()[0] if out else "unknown"

        # Try to get base prefix via 'info --json'.
        base_prefix: Optional[Path] = None
        try:
            info_proc = subprocess.run(
                [exe, "info", "--json"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            info = json.loads(info_proc.stdout)
            root_prefix = info.get("root_prefix") or info.get("default_prefix")
            if root_prefix:
                base_prefix = Path(root_prefix)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed retrieving backend info from '%s': %s", exe, exc)

        kind = "mamba" if "mamba" in Path(exe).name.lower() else "conda"

        # If we successfully detected a backend executable that differs from
        # the stored override, update the config so future runs use it first.
        if config.conda_executable != exe:
            config.conda_executable = exe
            try:
                save_config(config)
            except Exception:  # noqa: BLE001
                logger.debug("Failed to persist updated conda_executable", exc_info=True)

        return BackendInfo(kind=kind, executable=Path(exe), version=version, base_prefix=base_prefix)

    raise BackendNotFoundError("No Conda/Mamba backend (mamba/conda) found on PATH or in config.")


def _run_json(backend: BackendInfo, args: List[str]) -> object:
    cmd = [str(backend.executable)] + args
    logger.info("Running backend JSON command: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    except subprocess.CalledProcessError as exc:
        logger.error(
            "Backend command failed: %s\nSTDOUT:\n%s\nSTDERR:\n%s",
            cmd,
            exc.stdout,
            exc.stderr,
        )
        raise

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        logger.error("Failed to decode JSON output from %s: %s", cmd, exc)
        raise


def _run_plain(backend: BackendInfo, args: List[str]) -> subprocess.CompletedProcess[str]:
    """Run a backend command returning plain text output."""
    cmd = [str(backend.executable)] + args
    logger.info("Running backend command: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr or ""
        # Special-case Anaconda / defaults ToS failures so the UI can show
        # friendly guidance instead of a generic error.
        if "CondaToSNonInteractiveError" in stderr:
            logger.error(
                "Backend command hit Terms-of-Service gate: %s\nSTDOUT:\n%s\nSTDERR:\n%s",
                cmd,
                exc.stdout,
                stderr,
            )
            raise TermsOfServiceError(stderr) from exc
        logger.error(
            "Backend command failed: %s\nSTDOUT:\n%s\nSTDERR:\n%s",
            cmd,
            exc.stdout,
            stderr,
        )
        raise EnvOperationError(f"Backend command failed: {' '.join(cmd)}") from exc
    return proc


def _read_environments_txt() -> List[Path]:
    """Read ~/.conda/environments.txt if available."""
    paths: List[Path] = []
    try:
        if ENV_LIST_FILE.is_file():
            for line in ENV_LIST_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                p = Path(line)
                # Only include existing directories.
                if p.is_dir():
                    paths.append(p)
    except OSError as exc:
        logger.warning("Failed to read %s: %s", ENV_LIST_FILE, exc)
    return paths


def _discover_envs_from_envs_dirs(backend: BackendInfo, existing: Set[Path]) -> List[Path]:
    """Use `conda config --show envs_dirs` to discover additional env locations."""
    extra: List[Path] = []
    try:
        data = _run_json(backend, ["config", "--show", "envs_dirs", "--json"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to read envs_dirs from backend config: %s", exc)
        return extra

    if not isinstance(data, dict):
        return extra

    dirs = data.get("envs_dirs")
    if not isinstance(dirs, list):
        return extra

    for d in dirs:
        d_path = Path(str(d))
        if not d_path.is_dir():
            continue
        try:
            for child in d_path.iterdir():
                if not child.is_dir():
                    continue
                # Simple heuristic: treat any dir with a conda-meta subdir as an env.
                if (child / "conda-meta").is_dir() and child not in existing:
                    extra.append(child)
                    existing.add(child)
        except OSError as exc:
            logger.warning("Failed scanning envs_dir %s: %s", d_path, exc)

    return extra


def list_envs(backend: BackendInfo) -> List[Environment]:
    """Return environment list using a fast, layered strategy.

    Priority:
      1. ~/.conda/environments.txt  (fast path, no subprocess)
      2. `conda config --show envs_dirs` deep scan for extra env roots
      3. Fallback to `conda env list --json` if everything else fails
    """
    env_paths: List[Path] = []
    seen: Set[Path] = set()

    # 1) Fast path: environments.txt
    for p in _read_environments_txt():
        if p not in seen:
            env_paths.append(p)
            seen.add(p)

    # 2) Deep scan via envs_dirs for additional envs.
    env_paths.extend(_discover_envs_from_envs_dirs(backend, seen))

    # 3) Fallback to `conda env list --json` if we found nothing.
    default_prefix: Optional[str] = None
    if not env_paths:
        try:
            data = _run_json(backend, ["env", "list", "--json"])
            if isinstance(data, dict):
                raw_envs = data.get("envs") or []
                default_prefix = data.get("default_prefix")
                for path_str in raw_envs:
                    p = Path(path_str)
                    if p not in seen and p.is_dir():
                        env_paths.append(p)
                        seen.add(p)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to list environments from backend: %s", exc)

    # Ensure the base/root prefix is present as 'base' when available.
    if backend.base_prefix and backend.base_prefix not in seen and backend.base_prefix.is_dir():
        env_paths.insert(0, backend.base_prefix)
        seen.add(backend.base_prefix)

    # Determine active env from environment if possible.
    active_prefix = os.environ.get("CONDA_PREFIX")

    envs: List[Environment] = []
    for p in env_paths:
        name = p.name
        # If this is the root/base prefix, present as 'base'.
        if backend.base_prefix is not None and p == backend.base_prefix:
            name = "base"

        is_active = False
        if active_prefix and os.path.abspath(str(p)) == os.path.abspath(active_prefix):
            is_active = True
        elif default_prefix and os.path.abspath(str(p)) == os.path.abspath(default_prefix):
            is_active = True

        envs.append(Environment(name=name, path=p, is_active=is_active))

    # Sort by name for stable presentation.
    envs.sort(key=lambda e: e.name)
    return envs


def list_installed_packages(backend: BackendInfo, env: Environment) -> List[Package]:
    """Return installed Conda packages for an environment.

    Uses `conda list --json --prefix <path>`.
    """
    data = _run_json(backend, ["list", "--json", "--prefix", str(env.path)])
    if not isinstance(data, list):
        logger.error("Unexpected package list payload: %r", data)
        return []

    packages: List[Package] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        version = entry.get("version")
        if not name or not version:
            continue
        pkg = Package(
            name=str(name),
            version=str(version),
            build_string=entry.get("build_string"),
            channel=entry.get("channel"),
            source="conda",
        )
        packages.append(pkg)

    return sorted(packages, key=lambda p: p.name)


def install_packages(backend: BackendInfo, env: Environment, specs: List[str]) -> None:
    """Install one or more packages into an environment."""
    if not specs:
        return
    args: List[str] = [
        "install",
        "--yes",
        "--name",
        env.name,
        *specs,
    ]
    _run_plain(backend, args)


def update_packages(backend: BackendInfo, env: Environment, specs: List[str]) -> None:
    """Update one or more packages in an environment."""
    if not specs:
        return
    args: List[str] = [
        "update",
        "--yes",
        "--name",
        env.name,
        *specs,
    ]
    _run_plain(backend, args)


def remove_packages(backend: BackendInfo, env: Environment, specs: List[str]) -> None:
    """Remove one or more packages from an environment."""
    if not specs:
        return
    args: List[str] = [
        "remove",
        "--yes",
        "--name",
        env.name,
        *specs,
    ]
    _run_plain(backend, args)


def update_all_packages(backend: BackendInfo, env: Environment) -> None:
    """Update all packages in an environment."""
    args: List[str] = [
        "update",
        "--yes",
        "--all",
        "--name",
        env.name,
    ]
    _run_plain(backend, args)


def clone_environment(backend: BackendInfo, source: Environment, new_name: str) -> Environment:
    """Clone an environment to a new name.

    This uses `conda create --yes --prefix <new_path> --clone <old_path>`.
    """
    new_path = source.path.parent / new_name
    _run_plain(
        backend,
        [
            "create",
            "--yes",
            "--prefix",
            str(new_path),
            "--clone",
            str(source.path),
        ],
    )
    return Environment(name=new_name, path=new_path, is_active=False)


def remove_environment(backend: BackendInfo, env: Environment) -> None:
    """Delete an environment."""
    _run_plain(
        backend,
        [
            "remove",
            "--yes",
            "--prefix",
            str(env.path),
            "--all",
        ],
    )


def create_environment(backend: BackendInfo, name: str, python_version: Optional[str] = None) -> Environment:
    """Create a new environment with an optional Python version."""
    args: List[str] = [
        "create",
        "--yes",
        "--name",
        name,
    ]
    if python_version:
        args.append(f"python={python_version}")

    _run_plain(backend, args)

    # Derive the expected env path under <root>/envs/<name>
    root_prefix = backend.base_prefix or Path.home() / "miniforge3"
    env_path = root_prefix / "envs" / name
    return Environment(name=name, path=env_path, is_active=False)


def export_environment_yaml(
    backend: BackendInfo,
    env: Environment,
    dest: Path,
    no_builds: bool = False,
) -> None:
    """Export env configuration to a YAML file.

    Args:
        backend: Backend info.
        env: Environment to export.
        dest: Destination file path.
        no_builds: If True, use ``--no-builds`` to omit build strings, like
            ``conda env export --no-builds``.
    """
    cmd = [
        str(backend.executable),
        "env",
        "export",
        "--prefix",
        str(env.path),
    ]
    if no_builds:
        cmd.append("--no-builds")

    logger.info("Exporting environment YAML: %s -> %s", " ".join(cmd), dest)
    try:
        with dest.open("w", encoding="utf-8") as f:
            subprocess.run(
                cmd,
                check=True,
                stdout=f,
                stderr=subprocess.PIPE,
                text=True,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
    except subprocess.CalledProcessError as exc:
        logger.error(
            "Env export failed: %s\nSTDERR:\n%s",
            cmd,
            exc.stderr,
        )
        raise EnvOperationError("Failed to export environment YAML.") from exc


def create_environment_from_file(
    backend: BackendInfo,
    file: Path,
    name: Optional[str] = None,
) -> None:
    """Create an environment from an environment.yml file."""
    args: List[str] = [
        "env",
        "create",
        "--file",
        str(file),
    ]
    if name:
        args.extend(["--name", name])

    _run_plain(backend, args)


def _safe_du_bytes(path: Path) -> int:
    """Return directory size in bytes using du -sb, or 0 on failure."""
    try:
        proc = subprocess.run(
            ["du", "-sb", str(path)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        first = proc.stdout.splitlines()[0].split()[0]
        return int(first)
    except Exception:  # noqa: BLE001
        return 0


def env_disk_usage_bytes(env: Environment) -> int:
    """Return environment directory size in bytes (best effort)."""
    return _safe_du_bytes(env.path)


def get_disk_usage_report(backend: BackendInfo) -> DiskUsageReport:
    """Approximate Conda disk usage (envs + pkgs cache)."""
    # Prefer backend-reported root; fall back to common locations.
    root_prefix = backend.base_prefix
    if root_prefix is None:
        # Try typical roots under $HOME.
        home = Path.home()
        for root_name in ("miniforge3", "miniconda3", "anaconda3"):
            candidate = home / root_name
            if candidate.is_dir():
                root_prefix = candidate
                break

    if root_prefix is None:
        return DiskUsageReport(pkgs_cache=0, envs=0, total=0)

    pkgs_dir = root_prefix / "pkgs"
    envs_dir = root_prefix / "envs"

    pkgs_bytes = _safe_du_bytes(pkgs_dir) if pkgs_dir.is_dir() else 0
    envs_bytes = _safe_du_bytes(envs_dir) if envs_dir.is_dir() else 0
    total = pkgs_bytes + envs_bytes
    return DiskUsageReport(pkgs_cache=pkgs_bytes, envs=envs_bytes, total=total)


def run_global_clean(backend: BackendInfo) -> None:
    """Run `conda clean --all` using the detected backend."""
    _run_plain(
        backend,
        [
            "clean",
            "--all",
            "--yes",
        ],
    )


def get_channel_priorities(backend: BackendInfo) -> List[str]:
    """Return configured Conda channel priorities (best effort)."""
    try:
        data = _run_json(backend, ["config", "--show", "channels", "--json"])
    except Exception:  # noqa: BLE001
        return []
    if isinstance(data, dict):
        channels = data.get("channels")
        if isinstance(channels, list):
            return [str(ch) for ch in channels]
    return []


def set_channel_priorities(backend: BackendInfo, channels: List[str]) -> None:
    """Persist a specific ordered list of channels.

    This rewrites the global ``channels`` configuration key in the user's
    ``.condarc`` to exactly match the provided list. It is intentionally
    simple and opinionated rather than trying to preserve comments, etc.
    """
    # First, try to remove any existing `channels` key. This may fail if the
    # key is not set; treat that as non-fatal.
    try:
        _run_plain(backend, ["config", "--remove-key", "channels"])
    except EnvOperationError:
        logger.debug("No existing 'channels' key to remove", exc_info=True)

    # Then add each channel back in the desired order.
    for ch in channels:
        ch_str = str(ch).strip()
        if not ch_str:
            continue
        _run_plain(backend, ["config", "--add", "channels", ch_str])


def get_channel_priority_mode(backend: BackendInfo) -> Optional[str]:
    """Return the configured channel_priority mode, if available."""
    try:
        data = _run_json(backend, ["config", "--show", "channel_priority", "--json"])
    except Exception:  # noqa: BLE001
        return None

    if isinstance(data, dict):
        value = data.get("channel_priority")
        if isinstance(value, str):
            return value
    return None


def set_channel_priority_mode(backend: BackendInfo, mode: str) -> None:
    """Set channel_priority to a specific mode (e.g. 'strict' or 'flexible')."""
    mode = mode.strip().lower()
    if mode not in {"strict", "flexible"}:
        raise EnvOperationError(f"Unsupported channel_priority mode: {mode}")
    _run_plain(backend, ["config", "--set", "channel_priority", mode])


def install_miniforge(
    progress: Optional[Callable[[str], None]] = None,
) -> Path:
    """Download and install Miniforge into the user's home directory.

    The installer script requires bash (POSIX sh is not supported).

    Returns:
        Path to the installation prefix (e.g. ~/miniforge3).

    Raises:
        EnvOperationError on failure (including unsupported architecture).
    """
    def report(msg: str) -> None:
        logger.info("[install_miniforge] %s", msg)
        if progress is not None:
            try:
                progress(msg)
            except Exception:  # noqa: BLE001
                # UI callbacks should not break the installer.
                logger.debug("Progress callback failed", exc_info=True)

    # Detect system architecture.
    machine = platform.machine()
    arch_map = {
        "x86_64": "x86_64",
        "amd64": "x86_64",  # Some systems report amd64 instead of x86_64
        "aarch64": "aarch64",
        "arm64": "aarch64",  # Some systems report arm64 instead of aarch64
    }

    arch_key = arch_map.get(machine.lower())
    if arch_key is None:
        supported = ", ".join(sorted(set(arch_map.values())))
        raise EnvOperationError(
            f"Unsupported architecture: {machine}. "
            f"Miniforge installer is only available for: {supported}. "
            f"Please install Conda/Mamba manually or use a supported architecture."
        )

    home = Path.home()
    install_dir = home / "miniforge3"
    installer_filename = f"Miniforge3-Linux-{arch_key}.sh"
    url = (
        "https://github.com/conda-forge/miniforge/releases/latest/download/"
        f"{installer_filename}"
    )

    try:
        tmpdir = Path(tempfile.mkdtemp(prefix="condanest-miniforge-"))
    except Exception as exc:  # noqa: BLE001
        raise EnvOperationError(f"Failed to create temporary directory: {exc}") from exc

    installer_path = tmpdir / installer_filename

    # 1) Download installer.
    try:
        report("Downloading Miniforge installer…")
        urllib.request.urlretrieve(url, installer_path)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001
        raise EnvOperationError(f"Failed to download Miniforge installer: {exc}") from exc

    # 2) Run installer in batch mode (requires bash).
    try:
        report("Running Miniforge installer…")
        subprocess.run(
            ["bash", str(installer_path), "-b", "-p", str(install_dir)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError:
        raise EnvOperationError(
            "bash is required to run the Miniforge installer, but bash was not found. "
            "Please install bash and try again."
        )
    except subprocess.CalledProcessError as exc:
        logger.error(
            "Miniforge installer failed: %s\nSTDOUT:\n%s\nSTDERR:\n%s",
            exc.cmd,
            exc.stdout,
            exc.stderr,
        )
        raise EnvOperationError("Miniforge installation failed; see log for details.") from exc
    finally:
        try:
            if installer_path.exists():
                installer_path.unlink()
        except OSError:
            logger.debug("Failed to remove installer script", exc_info=True)

    # 3) Update config to point at the new installation.
    conda_exe = install_dir / "bin" / "conda"
    mamba_exe = install_dir / "bin" / "mamba"
    # Prefer `conda` for maximum CLI compatibility; fall back to mamba.
    exe_path = conda_exe if conda_exe.is_file() else mamba_exe
    if not exe_path.is_file():
        raise EnvOperationError(
            f"Miniforge installed to {install_dir}, but no 'conda' or 'mamba' executable was found."
        )

    try:
        cfg = load_config()
        cfg.conda_executable = str(exe_path)
        # Point env_root to the standard envs/ directory.
        envs_dir = install_dir / "envs"
        cfg.env_root = str(envs_dir)
        save_config(cfg)
    except Exception:  # noqa: BLE001
        logger.debug("Failed to persist Miniforge configuration", exc_info=True)

    report(f"Miniforge installed to {install_dir}")
    return install_dir

