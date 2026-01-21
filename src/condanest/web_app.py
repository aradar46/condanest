from __future__ import annotations

"""
Minimal local web UI for CondaNest.

Run with:

    condanest-web

This starts a FastAPI app on http://127.0.0.1:8765 and opens it in your browser.
It reuses the existing backend.py functions; no Qt/GTK or native plugins required.
"""

import webbrowser
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

from .backend import (
    BackendNotFoundError,
    EnvOperationError,
    TermsOfServiceError,
    detect_backend,
    env_disk_usage_bytes,
    list_envs,
    list_installed_packages,
    clone_environment,
    create_environment,
    create_environment_from_file,
    remove_environment,
    update_all_packages,
    install_packages,
    remove_packages,
    export_environment_yaml,
    get_disk_usage_report,
    get_channel_priorities,
    get_channel_priority_mode,
    set_channel_priorities,
    set_channel_priority_mode,
    install_miniforge,
    run_global_clean,
)
from .config import load_config, save_config
from .models import BackendInfo, Environment, Package, DiskUsageReport


class EnvSummary(BaseModel):
    name: str
    path: str
    is_active: bool
    size: str


class PackageInfo(BaseModel):
    name: str
    version: str
    channel: Optional[str]


class EnvDetail(BaseModel):
    env: EnvSummary
    packages: List[PackageInfo]


class CloneRequest(BaseModel):
    new_name: str


class CreateRequest(BaseModel):
    name: str
    python_version: Optional[str] = None


class InstallRequest(BaseModel):
    specs: List[str]


class ExportResponse(BaseModel):
    filename: str
    content: str


class CleanResponse(BaseModel):
    before: str
    after: str


class ChannelsState(BaseModel):
    channels: List[str]
    strict: bool


app = FastAPI(title="CondaNest Web UI")

# Serve static assets (e.g., condanest.png) from the project root.
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).resolve().parent.parent)),
    name="static",
)

_backend: Optional[BackendInfo] = None


def _format_bytes(num_bytes: int) -> str:
    if num_bytes <= 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num_bytes < 1024 or unit == "TB":
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} TB"


def _must_backend() -> BackendInfo:
    if _backend is None:
        raise HTTPException(status_code=500, detail="No Conda/Mamba backend detected.")
    return _backend


def _find_env(name: str) -> Environment:
    backend = _must_backend()
    for env in list_envs(backend):
        if env.name == name:
            return env
    raise HTTPException(status_code=404, detail=f"Environment '{name}' not found.")


@app.on_event("startup")
def _startup() -> None:
    global _backend
    try:
        _backend = detect_backend()
    except BackendNotFoundError as exc:
        raise RuntimeError(f"Conda/Mamba backend not found: {exc}") from exc


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """Serve a minimal HTML/JS single-page UI."""
    return """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>CondaNest Web</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #000000;
      --bg-elevated: #020617;
      --border-subtle: #1f2937;
      --border-strong: #374151;
      --accent: #facc15;      /* bright yellow highlight */
      --accent-soft: #facc15;
      --text-main: #ffffff;
      --text-muted: #9ca3af;
      --danger: #f97373;
    }
    * { box-sizing: border-box; }
    body {
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      margin: 0;
      display: flex;
      flex-direction: column;
      height: 100vh;
      background: var(--bg);
      color: var(--text-main);
    }
    #topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 10px 16px;
      border-bottom: 1px solid var(--border-subtle);
      background: #020617;
      backdrop-filter: blur(8px);
    }
    #topbar-title {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 1rem;
      font-weight: 600;
    }
    #topbar-title span.icon {
      width: 22px;
      height: 22px;
      border-radius: 6px;
      overflow: hidden;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      background: transparent;
    }
    #topbar-title span.icon img {
      width: 100%;
      height: 100%;
      object-fit: contain;
      display: block;
    }
    #topbar a.github-link {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 32px;
      height: 32px;
      border-radius: 999px;
      border: 1px solid var(--border-subtle);
      background: var(--bg-elevated);
      color: var(--text-main);
      text-decoration: none;
      transition: background 80ms ease, border-color 80ms ease, transform 80ms ease, box-shadow 80ms ease;
    }
    #topbar a.github-link:hover {
      background: var(--accent-soft);
      border-color: var(--accent);
      box-shadow: 0 1px 2px rgba(15,23,42,0.15);
      transform: translateY(-0.5px);
    }
    #topbar a.github-link svg {
      width: 18px;
      height: 18px;
      fill: currentColor;
    }
    #layout { flex: 1; display: flex; min-height: 0; }
    #sidebar {
      width: 270px;
      border-right: 1px solid var(--border-subtle);
      padding: 16px 14px;
      overflow-y: auto;
      background: linear-gradient(to bottom, rgba(15,23,42,0.6), transparent), var(--bg-elevated);
    }
    #sidebar h3 {
      margin: 0 0 8px 0;
      font-size: 0.95rem;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: var(--text-muted);
    }
    #envs {
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    .env {
      padding: 6px 8px;
      cursor: pointer;
      border-radius: 6px;
      font-size: 0.9rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
      color: var(--text-main);
      border: 1px solid transparent;
      transition: background 80ms ease, border-color 80ms ease, transform 80ms ease;
    }
    .env span.size {
      font-size: 0.78rem;
      color: var(--text-muted);
    }
    .env.selected {
      background: var(--accent);
      border-color: var(--accent);
      color: #000000;
      font-weight: 600;
    }
    .env.selected span.size {
      color: #000000;
    }
    .env.active {
      background: var(--accent);
      border-color: var(--accent);
      color: #000000;
      font-weight: 600;
    }
    .env.active span.size {
      color: #000000;
    }
    .env:hover {
      background: var(--accent);
      color: #000000;
      border-color: var(--accent);
      transform: translateX(1px);
    }
    .env:hover span.size {
      color: #000000;
    }
    #main {
      flex: 1;
      padding: 16px 18px;
      overflow-y: auto;
    }
    #env-actions {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
    }
    #pkg-management {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
    }
    #toolbar {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 6px;
      padding-bottom: 10px;
      margin-bottom: 12px;
      border-bottom: 1px solid var(--border-subtle);
    }
    #toolbar span#disk {
      margin-left: auto;
      font-size: 0.85rem;
      color: var(--text-muted);
    }
    button {
      margin: 0;
      padding: 5px 10px;
      border-radius: 6px;
      border: 1px solid var(--border-strong);
      background: var(--bg-elevated);
      color: var(--text-main);
      font-size: 0.85rem;
      cursor: pointer;
      text-align: center;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      transition: background 80ms ease, border-color 80ms ease, transform 80ms ease, box-shadow 80ms ease;
    }
    button:hover {
      background: var(--accent);
      border-color: var(--accent);
      color: #000000;
      box-shadow: 0 1px 2px rgba(15,23,42,0.15);
      transform: translateY(-0.5px);
    }
    button:active {
      transform: translateY(0);
      box-shadow: none;
    }
    .dropdown {
      position: relative;
      display: inline-block;
    }
    .dropdown-content {
      display: none;
      position: absolute;
      right: 0;
      top: calc(100% + 4px);
      background: var(--bg-elevated);
      min-width: 200px;
      border-radius: 8px;
      box-shadow: 0 4px 12px rgba(15,23,42,0.15), 0 0 0 1px var(--border-subtle);
      z-index: 1000;
      overflow: hidden;
    }
    .dropdown-content a {
      display: block;
      padding: 10px 14px;
      color: var(--text-main);
      text-decoration: none;
      font-size: 0.9rem;
      transition: background 80ms ease;
      border-bottom: 1px solid var(--border-subtle);
    }
    .dropdown-content a:last-child {
      border-bottom: none;
    }
    .dropdown-content a:hover {
      background: var(--accent-soft);
      color: #000000;
    }
    .dropdown.show .dropdown-content {
      display: block;
    }
    button.primary {
      background: var(--accent);
      border-color: var(--accent);
      color: #000000;
    }
    button.primary:hover {
      background: var(--accent);
      border-color: var(--accent);
    }
    button.chip {
      font-size: 0.8rem;
      padding: 4px 9px;
      border-radius: 999px;
      border-color: var(--accent);
      background: transparent;
    }
    button.chip:hover {
      color: var(--accent);
      background: transparent;
      border-color: var(--accent);
    }
    button.danger {
      border-color: var(--danger);
      color: var(--danger);
    }
    input[type="text"] {
      padding: 5px 8px;
      font-size: 0.9rem;
      border-radius: 999px;
      border: 1px solid var(--border-subtle);
      background: var(--bg-elevated);
      color: var(--text-main);
      min-width: 220px;
    }
    input[type="text"]:focus {
      outline: none;
      border-color: var(--accent);
      box-shadow: 0 0 0 1px var(--accent-soft);
    }
    table {
      border-collapse: collapse;
      width: 100%;
      margin-top: 10px;
      background: var(--bg-elevated);
      border-radius: 8px;
      overflow: hidden;
      box-shadow: 0 1px 3px rgba(15,23,42,0.06);
    }
    th, td {
      padding: 6px 10px;
      border-bottom: 1px solid var(--border-subtle);
      text-align: left;
      font-size: 0.85rem;
    }
    th {
      background: rgba(148,163,184,0.32);
      font-weight: 600;
      color: var(--text-muted);
    }
    tr:nth-child(even) td {
      background: rgba(15,23,42,0.01);
    }
    tr:hover td {
      background: rgba(37,99,235,0.06);
    }
    #status {
      font-size: 0.95rem;
      color: #000000;
      padding: 6px 12px;
      border-top: 1px solid var(--border-subtle);
      background: #facc15;
    }
    .modal-overlay {
      display: none;
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background: rgba(0, 0, 0, 0.5);
      z-index: 2000;
      align-items: center;
      justify-content: center;
    }
    .modal-overlay.show {
      display: flex;
    }
    .modal-dialog {
      background: var(--bg-elevated);
      border-radius: 12px;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.3);
      min-width: 500px;
      max-width: 90vw;
      max-height: 90vh;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
    .modal-header {
      padding: 18px 20px;
      border-bottom: 1px solid var(--border-subtle);
    }
    .modal-header h3 {
      margin: 0;
      font-size: 1.1rem;
      font-weight: 600;
    }
    .modal-body {
      padding: 20px;
      overflow-y: auto;
      flex: 1;
    }
    .modal-footer {
      padding: 12px 20px;
      border-top: 1px solid var(--border-subtle);
      display: flex;
      gap: 8px;
      justify-content: flex-end;
    }
    textarea {
      width: 100%;
      min-height: 150px;
      padding: 10px;
      font-family: monospace;
      font-size: 0.9rem;
      border-radius: 6px;
      border: 1px solid var(--border-subtle);
      background: var(--bg-elevated);
      color: var(--text-main);
      resize: vertical;
      box-sizing: border-box;
    }
    textarea:focus {
      outline: none;
      border-color: var(--accent);
      box-shadow: 0 0 0 2px var(--accent-soft);
    }
    .checkbox-group {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 12px;
    }
    input[type="checkbox"] {
      width: 18px;
      height: 18px;
      cursor: pointer;
    }
    label {
      font-size: 0.9rem;
      cursor: pointer;
      user-select: none;
    }
    .help-text {
      font-size: 0.85rem;
      color: var(--text-muted);
      margin-top: 6px;
    }
  </style>
</head>
<body>
  <div id="topbar">
    <div id="topbar-title">
      <span>CondaNest</span>
    </div>
    <a class="github-link" href="https://github.com/aradar46/condanest" target="_blank" rel="noopener noreferrer" aria-label="Open CondaNest on GitHub">
      <svg viewBox="0 0 16 16" aria-hidden="true">
        <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38
                 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52
                 -.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95
                 0-.87.31-1.59.82-2.15-.08-.2-.36-1.01.08-2.11 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2
                 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.91.08 2.11.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65
                 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.19 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8z"/>
      </svg>
    </a>
  </div>
  <div id="layout">
    <div id="sidebar">
      <h3>Environments</h3>
      <div id="envs"></div>
    </div>
    <div id="main">
      <div id="toolbar">
        <div style="display: flex; gap: 6px; margin-left: auto;">
          <button class="primary" onclick="refreshAll()">Refresh</button>
          <button class="primary" onclick="showCreateEnv()">Create…</button>
          <button class="primary" onclick="clean()">Clean…</button>
        </div>
        <div class="dropdown">
          <button class="chip" onclick="toggleMoreMenu()">More ▾</button>
          <div id="more-menu" class="dropdown-content">
            <a href="#" onclick="event.preventDefault(); manageChannels(); closeMoreMenu();">Channels…</a>
            <a href="#" onclick="event.preventDefault(); exportAll(); closeMoreMenu();">Export all…</a>
            <a href="#" onclick="event.preventDefault(); showCreateFromFiles(); closeMoreMenu();">Create from files…</a>
            <a href="#" onclick="event.preventDefault(); installMiniforge(); closeMoreMenu();">Install Miniforge…</a>
            <a href="#" onclick="event.preventDefault(); locateConda(); closeMoreMenu();">Locate Conda…</a>
          </div>
        </div>
        <span id="disk"></span>
      </div>
      <div id="content">
        <div>
          <div style="display:flex; flex-wrap:wrap; align-items:center; gap:8px;">
            <h2 id="env-title" style="margin:0;">No environment selected</h2>
            <div id="env-actions" style="display:none;">
              <button onclick="cloneEnv()">Clone…</button>
              <button onclick="renameEnv()">Rename…</button>
              <button class="danger" onclick="deleteEnv()">Delete…</button>
              <button onclick="exportYaml()">Export YAML…</button>
            </div>
          </div>
          <p id="env-path" style="margin-top:6px;"></p>
        </div>
        <div id="pkg-management" style="display:none; margin-top:8px;">
          <input id="pkg-search" type="text" placeholder="Search packages…" oninput="filterPackages()" />
          <button onclick="installPackage()">Install…</button>
          <button onclick="removePackage()">Remove…</button>
          <button onclick="updateAll()">Update all…</button>
        </div>
        <table id="pkgs-table" style="display:none;">
          <thead><tr><th>Name</th><th>Version</th><th>Channel</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>
  </div>
  <div id="status"></div>

  <!-- Channel Management Modal -->
  <div id="channel-modal" class="modal-overlay" onclick="if(event.target === this) closeChannelModal()">
    <div class="modal-dialog" onclick="event.stopPropagation()">
      <div class="modal-header">
        <h3>Manage Conda Channels</h3>
      </div>
      <div class="modal-body">
        <label for="channels-textarea" style="display:block; margin-bottom:6px; font-weight:500;">Channels (one per line, top = highest priority):</label>
        <textarea id="channels-textarea" placeholder="conda-forge&#10;bioconda&#10;defaults"></textarea>
        <div class="help-text">Enter each channel on its own line. The channel at the top has the highest priority.</div>
        <div class="checkbox-group">
          <input type="checkbox" id="strict-checkbox">
          <label for="strict-checkbox">Strict channel priority</label>
        </div>
        <div class="help-text">When enabled, prefer packages from the highest priority channel first.</div>
      </div>
      <div class="modal-footer">
        <button onclick="closeChannelModal()">Cancel</button>
        <button class="primary" onclick="applyChannelChanges()">Apply</button>
      </div>
    </div>
  </div>

  <script>
    let currentEnv = null;
    let allPackages = [];

    function setStatus(msg) {
      document.getElementById('status').textContent = msg || '';
    }

    async function api(path, opts) {
      const res = await fetch(path, opts);
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || res.statusText);
      }
      if (res.headers.get('content-type')?.includes('application/json')) {
        return res.json();
      }
      return res.text();
    }

    async function loadEnvs() {
      setStatus('Loading environments…');
      const envs = await api('/api/envs');
      const container = document.getElementById('envs');
      container.innerHTML = '';
      envs.forEach(env => {
        const div = document.createElement('div');
        let cls = 'env';
        if (env.is_active) cls += ' active';
        if (env.name === currentEnv) cls += ' selected';
        div.className = cls;
        const nameSpan = document.createElement('span');
        nameSpan.textContent = env.name;
        const sizeSpan = document.createElement('span');
        sizeSpan.className = 'size';
        sizeSpan.textContent = env.size;
        div.appendChild(nameSpan);
        div.appendChild(sizeSpan);
        div.onclick = () => selectEnv(env.name);
        container.appendChild(div);
      });
      const disk = await api('/api/disk');
      document.getElementById('disk').textContent = 'Total: ' + disk.total;
      setStatus('');
    }

    async function selectEnv(name) {
      setStatus('Loading environment ' + name + '…');
      currentEnv = name;
      const detail = await api('/api/envs/' + encodeURIComponent(name));
      document.getElementById('env-title').textContent = detail.env.name;
      document.getElementById('env-path').textContent = detail.env.path + ' • ' + detail.env.size;
      allPackages = detail.packages;
      renderPackages(allPackages);
      // Show environment and package management sections
      document.getElementById('env-actions').style.display = 'flex';
      document.getElementById('pkg-management').style.display = 'flex';
      document.getElementById('pkgs-table').style.display = 'table';
      // Update selected state in sidebar
      const envItems = document.querySelectorAll('#envs .env');
      envItems.forEach(item => {
        const label = item.querySelector('span')?.textContent || '';
        let cls = 'env';
        if (label === name) {
          if (item.classList.contains('active')) {
            cls += ' active';
          }
          cls += ' selected';
        } else if (item.classList.contains('active')) {
          cls += ' active';
        }
        item.className = cls;
      });
      setStatus('');
    }

    function renderPackages(pkgs) {
      const tbody = document.querySelector('#pkgs-table tbody');
      tbody.innerHTML = '';
      pkgs.forEach(p => {
        const tr = document.createElement('tr');
        tr.innerHTML = '<td>' + p.name + '</td><td>' + p.version + '</td><td>' + (p.channel || '') + '</td>';
        tbody.appendChild(tr);
      });
    }

    function filterPackages() {
      const q = document.getElementById('pkg-search').value.toLowerCase().trim();
      if (!q) { renderPackages(allPackages); return; }
      renderPackages(allPackages.filter(p =>
        p.name.toLowerCase().includes(q) ||
        (p.channel || '').toLowerCase().includes(q)
      ));
    }

    async function installPackage() {
      if (!currentEnv) return alert('Select an environment first.');
      const spec = prompt('Package spec (e.g. numpy or numpy=1.26):');
      if (!spec) return;
      setStatus('Installing ' + spec + ' into ' + currentEnv + '…');
      await api('/api/envs/' + encodeURIComponent(currentEnv) + '/install', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({specs: [spec]}),
      });
      await selectEnv(currentEnv);
      setStatus('');
    }

    async function removePackage() {
      if (!currentEnv) return alert('Select an environment first.');
      const spec = prompt('Package name to remove (e.g. numpy):');
      if (!spec) return;
      if (!confirm("Remove '" + spec + "' from " + currentEnv + '?')) return;
      setStatus('Removing ' + spec + ' from ' + currentEnv + '…');
      await api('/api/envs/' + encodeURIComponent(currentEnv) + '/remove', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({specs: [spec]}),
      });
      await selectEnv(currentEnv);
      setStatus('');
    }

    async function updateAll() {
      if (!currentEnv) return alert('Select an environment first.');
      if (!confirm(\"Run 'conda update --all' on \" + currentEnv + '?')) return;
      setStatus('Updating all packages in ' + currentEnv + '…');
      await api('/api/envs/' + encodeURIComponent(currentEnv) + '/update_all', {method: 'POST'});
      await selectEnv(currentEnv);
      setStatus('');
    }

    async function exportYaml() {
      if (!currentEnv) return alert('Select an environment first.');
      setStatus('Exporting ' + currentEnv + '…');
      const resp = await api('/api/envs/' + encodeURIComponent(currentEnv) + '/export?no_builds=false');
      const blob = new Blob([resp.content], {type: 'text/x-yaml'});
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = resp.filename;
      a.click();
      setStatus('');
    }

    async function showCreateEnv() {
      const name = prompt('Environment name:');
      if (!name) return;
      const py = prompt('Python version (optional, e.g. 3.11):') || null;
      setStatus('Creating environment ' + name + '…');
      await api('/api/envs', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name, python_version: py}),
      });
      await loadEnvs();
      await selectEnv(name);
      setStatus('');
    }

    async function cloneEnv() {
      if (!currentEnv) return alert('Select an environment first.');
      const newName = prompt('New name for cloned environment:', currentEnv + '-copy');
      if (!newName || newName === currentEnv) return;
      setStatus('Cloning environment ' + currentEnv + ' → ' + newName + '…');
      await api('/api/envs/' + encodeURIComponent(currentEnv) + '/clone', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({new_name: newName}),
      });
      await loadEnvs();
      currentEnv = newName;
      await selectEnv(newName);
      setStatus('');
    }

    async function deleteEnv() {
      if (!currentEnv) return alert('Select an environment first.');
      if (!confirm(\"Delete environment '\" + currentEnv + \"'? This cannot be undone.\")) return;
      setStatus('Deleting environment ' + currentEnv + '…');
      await api('/api/envs/' + encodeURIComponent(currentEnv), {method: 'DELETE'});
      currentEnv = null;
      await loadEnvs();
      document.getElementById('env-title').textContent = 'No environment selected';
      document.getElementById('env-path').textContent = '';
      renderPackages([]);
      // Hide environment and package management sections
      document.getElementById('env-actions').style.display = 'none';
      document.getElementById('pkg-management').style.display = 'none';
      document.getElementById('pkgs-table').style.display = 'none';
      setStatus('');
    }

    async function renameEnv() {
      if (!currentEnv) return alert('Select an environment first.');
      const newName = prompt('New name for this environment:', currentEnv);
      if (!newName || newName === currentEnv) return;
      // Implement rename as clone + delete, mirroring GTK behavior.
      setStatus('Renaming environment ' + currentEnv + ' → ' + newName + '…');
      await api('/api/envs/' + encodeURIComponent(currentEnv) + '/clone', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({new_name: newName}),
      });
      await api('/api/envs/' + encodeURIComponent(currentEnv), {method: 'DELETE'});
      currentEnv = newName;
      await loadEnvs();
      await selectEnv(newName);
      setStatus('');
    }

    async function clean() {
      if (!confirm(\"Run 'conda clean --all'?\")) return;
      setStatus('Cleaning Conda caches and package data…');
      const resp = await api('/api/clean', {method: 'POST'});
      await loadEnvs();
      setStatus('Cleaned. Before: ' + resp.before + ', after: ' + resp.after);
    }

    async function manageChannels() {
      const state = await api('/api/channels');
      const current = state.channels.join('\\n');
      const strict = state.strict || false;
      
      document.getElementById('channels-textarea').value = current;
      document.getElementById('strict-checkbox').checked = strict;
      document.getElementById('channel-modal').classList.add('show');
    }

    function closeChannelModal() {
      document.getElementById('channel-modal').classList.remove('show');
    }

    async function applyChannelChanges() {
      const textarea = document.getElementById('channels-textarea');
      const strict = document.getElementById('strict-checkbox').checked;
      const channels = textarea.value.split(/\\r?\\n/).map(s => s.trim()).filter(Boolean);
      
      setStatus('Updating channels…');
      await api('/api/channels', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({channels, strict}),
      });
      await refreshAll();
      closeChannelModal();
      setStatus('');
    }

    async function exportAll() {
      setStatus('Exporting all environments…');
      const res = await fetch('/api/export_all');
      if (!res.ok) {
        const text = await res.text();
        alert('Export failed: ' + text);
        setStatus('');
        return;
      }
      const blob = await res.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'conda-envs.zip';
      a.click();
      setStatus('');
    }

    async function showCreateFromFiles() {
      alert('Select one or more environment.yml files in the file picker that will open next.');
      const input = document.createElement('input');
      input.type = 'file';
      input.multiple = true;
      input.accept = '.yml,.yaml,text/yaml';
      input.onchange = async () => {
        if (!input.files.length) return;
        const form = new FormData();
        for (const file of input.files) {
          form.append('files', file);
        }
        setStatus('Creating environments from uploaded files…');
        const res = await fetch('/api/create_from_files', {
          method: 'POST',
          body: form,
        });
        if (!res.ok) {
          const text = await res.text();
          alert('Create from files failed: ' + text);
        } else {
          await refreshAll();
          setStatus('Created environments from ' + input.files.length + ' files.');
        }
      };
      input.click();
    }

    async function installMiniforge() {
      if (!confirm('Install Miniforge into your home directory?')) return;
      setStatus('Installing Miniforge…');
      const res = await fetch('/api/install_miniforge', {method: 'POST'});
      if (!res.ok) {
        const text = await res.text();
        alert('Miniforge installation failed: ' + text);
      } else {
        alert('Miniforge installed. Backend will be re-detected on next refresh.');
      }
      await refreshAll();
      setStatus('');
    }

    async function locateConda() {
      const path = prompt('Full path to Conda/Mamba executable (e.g. /home/you/miniforge3/bin/conda):');
      if (!path) return;
      setStatus('Saving Conda path and re-detecting backend…');
      const res = await fetch('/api/locate_conda', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({path}),
      });
      if (!res.ok) {
        const text = await res.text();
        alert('Failed to set Conda path: ' + text);
      }
      await refreshAll();
      setStatus('');
    }

    function toggleMoreMenu() {
      const menu = document.getElementById('more-menu');
      const dropdown = menu.closest('.dropdown');
      dropdown.classList.toggle('show');
    }
    function closeMoreMenu() {
      const menu = document.getElementById('more-menu');
      const dropdown = menu.closest('.dropdown');
      dropdown.classList.remove('show');
    }
    // Close dropdown when clicking outside
    window.onclick = function(event) {
      if (!event.target.matches('.dropdown button')) {
        const dropdowns = document.getElementsByClassName('dropdown-content');
        for (let i = 0; i < dropdowns.length; i++) {
          const openDropdown = dropdowns[i].closest('.dropdown');
          if (openDropdown && openDropdown.classList.contains('show')) {
            openDropdown.classList.remove('show');
          }
        }
      }
    }

    async function refreshAll() {
      await loadEnvs();
      if (currentEnv) {
        await selectEnv(currentEnv);
      }
    }

    loadEnvs();
  </script>
</body>
</html>
"""


@app.get("/api/envs", response_model=List[EnvSummary])
def api_list_envs() -> List[EnvSummary]:
    backend = _must_backend()
    envs = list_envs(backend)
    result: List[EnvSummary] = []
    for env in envs:
        size_bytes = env_disk_usage_bytes(env)
        result.append(
            EnvSummary(
                name=env.name,
                path=str(env.path),
                is_active=env.is_active,
                size=_format_bytes(size_bytes),
            )
        )
    return result


@app.get("/api/envs/{name}", response_model=EnvDetail)
def api_env_detail(name: str) -> EnvDetail:
    backend = _must_backend()
    env = _find_env(name)
    pkgs = list_installed_packages(backend, env)
    size_bytes = env_disk_usage_bytes(env)
    summary = EnvSummary(
        name=env.name,
        path=str(env.path),
        is_active=env.is_active,
        size=_format_bytes(size_bytes),
    )
    return EnvDetail(
        env=summary,
        packages=[
            PackageInfo(name=p.name, version=p.version, channel=p.channel)
            for p in pkgs
        ],
    )


@app.post("/api/envs", status_code=201)
def api_create_env(req: CreateRequest) -> None:
    backend = _must_backend()
    try:
        create_environment(backend, req.name, req.python_version)
    except TermsOfServiceError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Terms of Service error:\n{exc}",
        ) from exc
    except EnvOperationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/envs/{name}/clone")
def api_clone_env(name: str, req: CloneRequest) -> None:
    backend = _must_backend()
    env = _find_env(name)
    try:
        clone_environment(backend, env, req.new_name)
    except TermsOfServiceError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Terms of Service error:\n{exc}",
        ) from exc
    except EnvOperationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/envs/{name}")
def api_delete_env(name: str) -> None:
    backend = _must_backend()
    env = _find_env(name)
    try:
        remove_environment(backend, env)
    except TermsOfServiceError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Terms of Service error:\n{exc}",
        ) from exc
    except EnvOperationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/envs/{name}/install")
def api_install(name: str, req: InstallRequest) -> None:
    backend = _must_backend()
    env = _find_env(name)
    try:
        install_packages(backend, env, req.specs)
    except TermsOfServiceError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Terms of Service error:\n{exc}",
        ) from exc
    except EnvOperationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/envs/{name}/remove")
def api_remove(name: str, req: InstallRequest) -> None:
    backend = _must_backend()
    env = _find_env(name)
    try:
        remove_packages(backend, env, req.specs)
    except TermsOfServiceError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Terms of Service error:\n{exc}",
        ) from exc
    except EnvOperationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/envs/{name}/update_all")
def api_update_all(name: str) -> None:
    backend = _must_backend()
    env = _find_env(name)
    try:
        update_all_packages(backend, env)
    except TermsOfServiceError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Terms of Service error:\n{exc}",
        ) from exc
    except EnvOperationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/envs/{name}/export", response_model=ExportResponse)
def api_export_env(name: str, no_builds: bool = False) -> ExportResponse:
    """Return environment.yml content to browser."""
    backend = _must_backend()
    env = _find_env(name)
    # Export to a temporary file and read it back.
    from tempfile import NamedTemporaryFile

    with NamedTemporaryFile("w+", suffix=".yml", delete=True) as tmp:
        dest = Path(tmp.name)
        export_environment_yaml(backend, env, dest, no_builds=no_builds)
        tmp.seek(0)
        content = tmp.read()
    filename = f"{env.name}.yml"
    return ExportResponse(filename=filename, content=content)


@app.get("/api/disk")
def api_disk() -> dict:
    backend = _must_backend()
    report: DiskUsageReport = get_disk_usage_report(backend)
    return {
        "total": _format_bytes(report.total),
    }


@app.post("/api/clean", response_model=CleanResponse)
def api_clean() -> CleanResponse:
    backend = _must_backend()
    before = get_disk_usage_report(backend)
    try:
        run_global_clean(backend)
    except EnvOperationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    after = get_disk_usage_report(backend)
    return CleanResponse(
        before=_format_bytes(before.total),
        after=_format_bytes(after.total),
    )


@app.get("/api/channels", response_model=ChannelsState)
def api_get_channels() -> ChannelsState:
    backend = _must_backend()
    channels = get_channel_priorities(backend)
    mode = get_channel_priority_mode(backend) or ""
    strict = mode.strip().lower() == "strict"
    return ChannelsState(channels=channels, strict=strict)


@app.post("/api/channels")
def api_set_channels(state: ChannelsState) -> None:
    backend = _must_backend()
    try:
        set_channel_priorities(backend, state.channels)
        set_channel_priority_mode(backend, "strict" if state.strict else "flexible")
    except EnvOperationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/export_all")
def api_export_all() -> StreamingResponse:
    """Export all environments as a zip of YAML files."""
    backend = _must_backend()
    envs = list_envs(backend)
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        from tempfile import NamedTemporaryFile

        for env in envs:
            with NamedTemporaryFile("w+", suffix=".yml", delete=True) as tmp:
                dest = Path(tmp.name)
                export_environment_yaml(backend, env, dest, no_builds=False)
                tmp.seek(0)
                content = tmp.read()
            zf.writestr(f"{env.name}.yml", content)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="conda-envs.zip"'},
    )


@app.post("/api/create_from_files")
async def api_create_from_files(files: List[UploadFile] = File(...)) -> None:
    """Create environments from uploaded YAML files (one env per file)."""
    backend = _must_backend()
    from tempfile import NamedTemporaryFile

    try:
        for upload in files:
            content = await upload.read()
            if not content:
                continue
            with NamedTemporaryFile("wb", suffix=Path(upload.filename or "").suffix, delete=True) as tmp:
                tmp.write(content)
                tmp.flush()
                dest = Path(tmp.name)
                name = Path(upload.filename or dest.name).stem
                create_environment_from_file(backend, dest, name=name)
    except EnvOperationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/install_miniforge")
def api_install_miniforge() -> None:
    """Install Miniforge into the user's home directory and update config."""
    def progress(msg: str) -> None:
        # For now we ignore progress messages; the frontend just shows a spinner.
        return

    try:
        install_miniforge(progress=progress)
    except EnvOperationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class LocateCondaRequest(BaseModel):
    path: str


@app.post("/api/locate_conda")
def api_locate_conda(req: LocateCondaRequest) -> None:
    cfg = load_config()
    cfg.conda_executable = req.path
    try:
        save_config(cfg)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Failed to save config: {exc}") from exc


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point for `condanest-web` script."""
    port = 8765
    url = f"http://127.0.0.1:{port}/"
    # Start browser after server boots; uvicorn will run forever.
    webbrowser.open(url)
    uvicorn.run("condanest.web_app:app", host="127.0.0.1", port=port, reload=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

