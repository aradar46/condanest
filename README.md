<p align="center">
  <img src="condanest.png" alt="CondaNest logo" width="200">
</p>

## CondaNest

 A fast, minimal GTK4 desktop app for **managing, and cleaning Conda/Mamba environments** on Linux.  
CondaNest gives you a single, focused window to see which envs exist, what they contain, and how much disk space they use – without touching your shell config.

<p align="center">
  <img src="app.gif" alt="CondaNest in action">
</p>



### Features (current prototype)

- **Environment list** with name, path and approximate disk usage.
- **Per‑env actions**: clone, rename (clone‑then‑remove), delete, export to `environment.yml`.
- **Bulk tools**: export all envs to YAML, create multiple envs from a folder of YAMLs.
- **Janitor**: estimate Conda disk usage and run `conda clean --all` from a simple dialog.
- **Channel management**: reorder global channels and toggle strict priority.

### Quick start (development)

```bash
git clone https://github.com/aradar46/condanest.git
cd condanest
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -e .
condanest
```

### Downloadable AppImage

For users who don’t want to set up Python or a venv, CondaNest is also shipped as a self‑contained **AppImage** (`CondaNest-x86_64.AppImage`) on the GitHub Releases page.

```bash
chmod +x CondaNest-x86_64.AppImage
./CondaNest-x86_64.AppImage
```

This bundles CondaNest and its Python dependencies in an internal virtualenv. Your system still needs GTK4/Libadwaita + PyGObject installed as listed in the requirements above.

### Conda/Mamba Detection & Setup

CondaNest **automatically detects** your existing Conda or Mamba installation by checking:

- Environment variables (`$CONDA_EXE`, `$MAMBA_EXE`)
- Common installation locations (`~/miniforge3`, `~/miniconda3`, `~/anaconda3`)
- System PATH

If no Conda/Mamba installation is found, CondaNest can **install Miniforge for you** (recommended) or help you locate an existing installation. Miniforge is a community-driven distribution that uses the `conda-forge` channel by default and doesn't require accepting Anaconda's Terms of Service.

#### Anaconda Terms of Service

If you're using **Anaconda/miniconda** (not Miniforge/Mambaforge) and the default Anaconda channels are enabled, you may need to accept their Terms of Service before certain operations work. If CondaNest shows a ToS error, copy and run these commands **once** in a terminal (replace `conda` with your actual conda executable path if needed):

```bash
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r
```

After running these commands, retry the operation in CondaNest.

### Requirements

- **OS**: Linux (X11 or Wayland), GNOME / Libadwaita recommended
- **Python**: 3.10+
- **System packages** (installed via your distro, not pip):
  - GTK4 + Libadwaita runtime and introspection data
  - PyGObject bindings (`gi`)

On Debian/Ubuntu, you typically need:

- `python3-gi`
- `gir1.2-gtk-4.0`
- `gir1.2-adw-1`
On other distros, install the equivalent GTK4/Libadwaita and PyGObject packages from your package manager.

### License

MIT License

Copyright (c) 2024 Adr

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
