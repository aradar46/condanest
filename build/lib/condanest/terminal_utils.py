from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .models import Environment


class TerminalLaunchError(RuntimeError):
    """Raised when no suitable terminal could be launched."""


def _detect_terminal_command() -> list[str]:
    """Return a terminal command (argv) suitable for launching a script.

    Strategy:
    1) Respect $TERMINAL if it points to an executable.
    2) Use the Debian/XDG helper x-terminal-emulator when available.
    3) Fall back through a list of common terminal emulators.
    """
    term = os.environ.get("TERMINAL")
    if term:
        return [term]

    if shutil.which("x-terminal-emulator"):
        return ["x-terminal-emulator", "-e"]

    for name in ("gnome-terminal", "konsole", "kitty", "xfce4-terminal", "tilix", "xterm"):
        if shutil.which(name):
            # Most support -e; modern gnome-terminal warns but still works.
            return [name, "-e"]

    raise TerminalLaunchError(
        "No suitable terminal application found (x-terminal-emulator, gnome-terminal, etc.)."
    )


def launch_terminal_for_env(
    env: Environment,
    working_dir: Path | None = None,
    conda_executable: str | None = None,
) -> None:
    """Launch a terminal window with the given Conda environment activated.

    Implementation detail:
    - Generate a small temporary bash script that:
      * cd's into the working directory
      * runs `conda run -n <env> bash`
    - Ask the terminal to execute that script.
    This avoids depending on interactive shell functions from `conda init`
    and keeps the shell open for the user.
    """
    if working_dir is None:
        working_dir = Path.home()

    term_cmd = _detect_terminal_command()

    # Prefer an explicit executable path passed from the backend; fall back to
    # CONDA_EXE, then to a bare 'conda' on PATH.
    conda_exe = conda_executable or os.environ.get("CONDA_EXE", "conda")

    tmp_dir = Path(tempfile.mkdtemp(prefix="condanest-"))
    script_path = tmp_dir / f"launch-{env.name}.sh"

    script_content = f"""#!/usr/bin/env bash
cd "{working_dir}" || exit 1
conda_exe="{conda_exe}"
echo "[CondaNest] Using Conda executable: $conda_exe"
echo "[CondaNest] Environment: {env.name}"
echo ""

# Start an interactive shell inside the Conda environment.
"$conda_exe" run -n "{env.name}" "$SHELL" -i
status=$?
echo ""
if [ $status -ne 0 ]; then
    echo "[CondaNest] 'conda run' exited with status $status."
    echo "If this failed, check that the environment exists and that Conda works from this executable:"
    echo "    $conda_exe"
    echo ""
fi
read -p "Press Enter to close this window..." _ignored
exit $status
"""
    try:
        script_path.write_text(script_content, encoding="utf-8")
        script_path.chmod(0o755)
    except OSError as exc:
        raise TerminalLaunchError(f"Failed to prepare launcher script: {exc}") from exc

    full_cmd = term_cmd + [str(script_path)]
    try:
        subprocess.Popen(full_cmd)
    except OSError as exc:
        raise TerminalLaunchError(f"Failed to launch terminal: {exc}") from exc

