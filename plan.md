## Project Plan: Linux‑Native Conda Control (GTK4, Python)

### 1. Goals & Non‑Goals

- **Primary Goals**
  - **Fast, single-window Conda/Mamba manager** that feels native on Debian/Ubuntu.
  - **Cover CLI pain points**: visual env overview, smart rename, terminal launcher, disk janitor, Conda/Pip distinction.
- **Non‑Goals**
  - No background daemons, no Jupyter integration, no environment creation wizards beyond basics, no “app store” UX.

---

### 2. Architecture & Tech Stack

- **Language & Runtime**
  - **Python 3.10+**, target conda/miniconda/mamba installs on Linux.
- **GUI**
  - **PyGObject (GTK4 + Libadwaita)**, single main window, responsive layout.
- **Backend**
  - **Command runner module**:
    - Detect backend: prefer `mamba`, fallback to `conda`.
    - Run all operations via `subprocess` using `--json` where available.
  - **Data layer**:
    - Simple models: `Environment`, `Package`, `BackendInfo`, `DiskUsageReport`.
- **Configuration**
  - Detect default Conda installation and env root; allow manual override via simple config file (`~/.config/conda-control/config.json`).

---

### 3. Core Features & Milestones

#### Milestone 1: Skeleton App & Backend Probe

- **Tasks**
  - Initialize Python project (virtualenv, `pyproject.toml` or `requirements.txt`).
  - Basic GTK4 window with header bar and blank main area.
  - Detect `mamba` vs `conda`, version, and base env via a backend helper.
  - Error handling: show simple “backend not found / misconfigured” view.

#### Milestone 2: Visual Environment List

- **Tasks**
  - Implement `list_envs()` using `conda env list --json`.
  - For each env:
    - Derive **name**, **path**, **is_active**.
    - Query **Python version** (e.g., `python --version` within env or via `conda list python --json`).
    - Compute **disk size** via `du -sh` (run async, cache results).
  - GTK UI:
    - Left **Sidebar**: listbox/treeview with name, size, Python version.
    - **Selection model** to drive main content.

#### Milestone 3: Environment Actions (Rename & Terminal Here)

- **Smart Rename**
  - UI: “Rename” button or context menu item on selected env.
  - Flow:
    - Prompt new name → run `clone` (`conda create --name new_name --clone old_name`).
    - Verify new env appears and is usable.
    - On success, offer to delete old env (`conda remove --name old_name --all`).
    - Show progress bar dialog with cancellable, detailed command log.
- **Terminal Here**
  - Detect default terminal (check `$TERMINAL`, `xdg-terminal-exec`, fallbacks: `gnome-terminal`, `konsole`, `kitty`, `xterm`).
  - Launch terminal with:
    - Shell startup that runs `conda activate env_name`.
    - Start in env’s working directory (configurable: project path vs env root).

#### Milestone 4: Packages View (Installed / Available + Conda vs Pip)

- **Backend**
  - `list_installed_packages(env)` via `conda list --json`.
  - Detect Pip packages:
    - Use `pip list --format=json` within env or flag `pypi` channel / non‑conda entries.
  - `search_available_packages(env, query)` via `conda search --json`.
- **UI**
  - **Main area**:
    - Tabs: **Installed** / **Available**.
    - Search box with debounce.
    - Columns: name, version, channel/source, **type icon (Conda / Pip)**.
  - **Actions**
    - Remove package:
      - If Conda package → `conda remove`.
      - If Pip package → warn: “Pip package; removing via Conda may break things. Use pip instead.”
    - Install/update package (basic UX only: input name, choose version/channel when available).

#### Milestone 5: Janitor (Disk Cleaner)

- **Pre‑calculation**
  - Estimate reclaimable space:
    - `conda clean --all --dry-run` if available; otherwise approximate from cache dirs size (`pkgs`, `cache`).
  - Build **DiskUsageReport**: {pkgs_cache, envs, total}.
- **UI**
  - Footer label: **“Total Conda Disk Usage: X GB”** (envs + caches).
  - “Clean Up…” button:
    - Shows dialog: summary of what will be removed and estimated space reclaimed.
    - On confirm, run `conda clean --all` with progress and log.

---

### 4. UX / UI Details

- **Layout**
  - **Sidebar**: environments list, filter/search by name, show active env with accent.
  - **Top bar**: app title, current backend (Conda/Mamba), “Open Terminal” button, “Rename” in overflow menu.
  - **Main area**: package tables with search and tabs.
  - **Footer**: disk usage summary + “Clean Up” button + status text (last operation).
- **Linux Integration**
  - Honor system **dark mode** via Libadwaita.
  - Use system icons where possible (e.g., `utilities-terminal`, `applications-system`).
  - Use native dialogs for confirmation and errors.

---

### 5. Integration & Packaging

- **.desktop Integration**
  - Install `conda-control.desktop` to `~/.local/share/applications/` with:
    - `Name=Conda Control`
    - `Exec=conda-control` (wrapper script in `~/.local/bin`).
    - `Icon=conda-control` (install SVG/PNG icon).
- **Installation Options**
  - Simple **pip install --user conda-control** that:
    - Installs CLI launcher script `conda-control`.
    - Installs desktop file and icons on first run.
  - Optional **Debian package** later:
    - `debian/` packaging metadata.
    - Handle dependencies (`python3-gi`, `gir1.2-gtk-4.0`, etc.).

---

### 6. Testing, Logging, and Robustness

- **Testing**
  - Unit tests for backend command utilities (mock `subprocess`).
  - Test against different setups: Conda only, Mamba only, both, no backend found.
- **Logging**
  - Lightweight logging to `~/.cache/conda-control/log.txt` with command and stderr captures.
- **Failure Handling**
  - Graceful errors when:
    - Backend missing or misconfigured.
    - Env paths missing.
    - `du` and `clean` commands fail.
  - Clear user feedback with retry hints.

---

### 7. Stretch Goals (Post‑MVP)

- **Feature Ideas**
  - Simple **“Clone env”** and **“Export YAML”** buttons.
  - Show **channel priorities** and allow quick toggle.
  - Minimal **update all** UX for a selected env.
  - **Keyboard shortcuts** and command palette for power users.



  extra features 
  The "Killer Feature" for Linux Users
The Shell Integration Toggle. Provide a simple switch that adds/removes the conda init code from the user's .bashrc or .zshrc. Many Linux users hate that Conda modifies their shell by default; a GUI that lets them "Turn on Conda mode" only when needed would be highly popular.



…or push an existing repository from the command line
git remote add origin https://github.com/aradar46/condanest.git
git branch -M main
git push -u origin main