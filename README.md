<p align="center">
  <img src="condanest.png" alt="CondaNest logo" width="200">
</p>

## CondaNest: GUI Manager for Conda


 A fast, minimal GTK4 desktop app for **managing, and cleaning Conda/Mamba environments** on Linux.   

<p align="center">
  <img src="app.gif" alt="CondaNest in action">
</p>


### Features

* List environments with path and disk usage
* Clone, rename, delete, export to `environment.yml`
* Bulk export or create envs from YAML folders
* Run `conda clean --all` from a simple dialog
* Manage global channels and strict priority
* Install packages in a environment

### Quick start (development)

```bash
git clone https://github.com/aradar46/condanest.git
cd condanest
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -e .
condanest
```

### AppImage

```bash
chmod +x CondaNest-x86_64.AppImage
./CondaNest-x86_64.AppImage
```

### Anaconda Terms of Service

If you use **Anaconda or Miniconda** with default Anaconda channels, you may need to accept the Terms of Service once:

```bash
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r
```

After running these commands, retry the operation in CondaNest.

### Requirements

* Linux with GTK4 and Libadwaita
* Python 3.10+
* `python3-gi`, `gir1.2-gtk-4.0`, `gir1.2-adw-1`

### License

MIT License
