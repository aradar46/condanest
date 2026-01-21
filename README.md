<p align="center">
  <img src="condanest.png" alt="CondaNest logo" width="200">
</p>

<p align="center">
  <a href="https://pypi.org/project/condanest/">
    <img src="https://img.shields.io/pypi/v/condanest.svg" alt="PyPI version">
  </a>
  <a href="https://pypi.org/project/condanest/">
    <img src="https://img.shields.io/pypi/pyversions/condanest.svg" alt="Python versions">
  </a>
  <img src="https://img.shields.io/badge/platform-Linux%20%7C%20Windows%20%7C%20macOS-blue" alt="Platform support">
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License">
  </a>
</p>

## CondaNest: Web-based GUI Conda Manager

A fast, minimal web-based GUI for **managing and cleaning Conda/Mamba environments** on Linux, Windows, and macOS.

Runs a local web server that opens in your browser - no native GUI dependencies required.
 


### Features

* List environments with path and disk usage
* Clone, rename, delete, export to `environment.yml`
* Bulk export or create envs from YAML folders
* Run `conda clean --all` from a simple dialog
* Manage global channels and strict priority
* Install packages in a environment

### Installation

Install CondaNest from PyPI:

```bash
pip install condanest
```

Or install from source:

```bash
git clone https://github.com/aradar46/condanest.git
cd condanest
pip install .
```

### Usage

After installation, run:

```bash
condanest
```

The app will automatically start a web server and open in your browser at http://127.0.0.1:8765

**Platform support:**
- ✅ **Linux**: Fully supported
- ✅ **Windows**: Fully supported (uses `Scripts/conda.exe`)
- ✅ **macOS**: Fully supported

The browser should open automatically on all platforms. If it doesn't, manually navigate to http://127.0.0.1:8765

### Anaconda Terms of Service

If you use **Anaconda or Miniconda** with default Anaconda channels, you may need to accept the Terms of Service once:

```bash
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r
```

After running these commands, retry the operation in CondaNest.

### Requirements

* Python 3.10+
* Conda or Mamba installed on your system (or use the built-in Miniforge installer)
* FastAPI and Uvicorn (installed automatically via pip)

**Note:** CondaNest will automatically detect Conda/Mamba installations. If none are found, you can use the built-in installer to install Miniforge directly from the app.

### License

MIT License
