from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional, List, Set

# Work around Qt Wayland decoration plugin crashes (libadwaita.so) by
# forcing the X11 (xcb) platform when not explicitly overridden. This
# makes the Qt app run via XWayland instead of native Wayland, which
# avoids the segmentation fault you're seeing.
os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

from PySide6.QtCore import Qt, QObject, Signal, QRunnable, QThreadPool, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QLabel,
    QSplitter,
    QStatusBar,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QInputDialog,
    QMessageBox,
    QProgressBar,
    QFileDialog,
    QDialog,
    QCheckBox,
    QToolBar,
    QMenu,
    QDialogButtonBox,
    QTextEdit,
    QScrollArea,
)

from .backend import (
    BackendNotFoundError,
    EnvOperationError,
    TermsOfServiceError,
    clone_environment,
    create_environment,
    create_environment_from_file,
    detect_backend,
    env_disk_usage_bytes,
    export_environment_yaml,
    get_channel_priorities,
    get_channel_priority_mode,
    get_disk_usage_report,
    install_miniforge,
    install_packages,
    list_envs,
    list_installed_packages,
    remove_environment,
    run_global_clean,
    set_channel_priorities,
    set_channel_priority_mode,
    update_all_packages,
)
from .config import load_config, save_config
from .models import BackendInfo, DiskUsageReport, Environment, Package


class _BackendProbeTask(QRunnable):
    """Background task to detect the Conda/Mamba backend."""

    class Signals(QObject):
        finished = Signal(object, object)  # backend: Optional[BackendInfo], error: Optional[BaseException]

    def __init__(self) -> None:
        super().__init__()
        self.signals = self.Signals()

    def run(self) -> None:  # type: ignore[override]
        backend: Optional[BackendInfo] = None
        error: Optional[BaseException] = None
        try:
            backend = detect_backend()
        except BackendNotFoundError as exc:
            error = exc
        except BaseException as exc:  # noqa: BLE001
            error = exc
        self.signals.finished.emit(backend, error)


class _EnvListTask(QRunnable):
    """Background task to list environments for a backend."""

    class Signals(QObject):
        finished = Signal(list, object)  # envs: List[Environment], error: Optional[BaseException]

    def __init__(self, backend: BackendInfo) -> None:
        super().__init__()
        self._backend = backend
        self.signals = self.Signals()

    def run(self) -> None:  # type: ignore[override]
        envs: List[Environment] = []
        error: Optional[BaseException] = None
        try:
            envs = list_envs(self._backend)
        except BaseException as exc:  # noqa: BLE001
            error = exc
        self.signals.finished.emit(envs, error)


class _PackageListTask(QRunnable):
    """Background task to load installed packages for an environment."""

    class Signals(QObject):
        finished = Signal(list, object)  # packages: List[Package], error: Optional[BaseException]

    def __init__(self, backend: BackendInfo, env: Environment) -> None:
        super().__init__()
        self._backend = backend
        self._env = env
        self.signals = self.Signals()

    def run(self) -> None:  # type: ignore[override]
        packages: List[Package] = []
        error: Optional[BaseException] = None
        try:
            packages = list_installed_packages(self._backend, self._env)
        except BaseException as exc:  # noqa: BLE001
            error = exc
        self.signals.finished.emit(packages, error)


class _EnvSizeTask(QRunnable):
    """Background task to calculate disk usage for a single environment."""

    class Signals(QObject):
        finished = Signal(str, str)  # env_name: str, size_str: str

    def __init__(self, env: Environment) -> None:
        super().__init__()
        self._env = env
        self.signals = self.Signals()

    def run(self) -> None:  # type: ignore[override]
        try:
            size_bytes = env_disk_usage_bytes(self._env)
            size_str = MainWindow._format_bytes(size_bytes)
        except Exception:  # noqa: BLE001
            size_str = "?"
        self.signals.finished.emit(self._env.name, size_str)


class _GenericTask(QRunnable):
    """Generic background task that runs a callable and emits result/error."""

    class Signals(QObject):
        finished = Signal(object, object)  # result: object, error: Optional[BaseException]

    def __init__(self, func, *args, **kwargs) -> None:
        super().__init__()
        self._func = func
        self._args = args
        self._kwargs = kwargs
        self.signals = self.Signals()

    def run(self) -> None:  # type: ignore[override]
        result = None
        error: Optional[BaseException] = None
        try:
            result = self._func(*self._args, **self._kwargs)
        except BaseException as exc:  # noqa: BLE001
            error = exc
        self.signals.finished.emit(result, error)


class MainWindow(QMainWindow):
    """Complete Qt port of CondaNest with all features from the GTK version."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("CondaNest (Qt)")
        self.resize(1000, 650)

        self._backend: Optional[BackendInfo] = None
        self._envs: list[Environment] = []
        self._current_env: Optional[Environment] = None
        self._packages: list[Package] = []
        self._package_query: str = ""
        self._disk_usage: Optional[DiskUsageReport] = None
        self._env_sizes: dict[str, str] = {}  # env_name -> size_str

        self._thread_pool = QThreadPool.globalInstance()
        # Keep references to background tasks so they are not garbage-collected
        # before their signals are delivered.
        self._tasks: list[QRunnable] = []

        # --- Status bar with disk usage and progress ---
        status = QStatusBar()
        self._disk_label = QLabel("")
        self._status_label = QLabel("")
        self._progress = QProgressBar()
        self._progress.setMaximum(0)  # indeterminate
        self._progress.setVisible(False)

        status.addWidget(self._disk_label)
        status.addPermanentWidget(self._status_label, 1)
        status.addPermanentWidget(self._progress, 0)
        self.setStatusBar(status)

        # --- Toolbar ---
        toolbar = QToolBar()
        self.addToolBar(toolbar)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._on_refresh_clicked)
        toolbar.addWidget(refresh_btn)

        create_btn = QPushButton("Create…")
        create_btn.clicked.connect(self._on_create_env_clicked)
        toolbar.addWidget(create_btn)

        clean_btn = QPushButton("Clean…")
        clean_btn.clicked.connect(self._on_clean_clicked)
        clean_btn.setEnabled(False)
        self._clean_button = clean_btn
        toolbar.addWidget(clean_btn)

        bulk_menu = QMenu("Bulk actions", self)
        bulk_menu.addAction("Export all envs to YAML…", self._on_export_all_clicked)
        bulk_menu.addAction("Create envs from folder…", self._on_create_all_clicked)
        bulk_menu.addAction("Manage channels…", self._on_manage_channels_clicked)
        bulk_menu.addSeparator()
        bulk_menu.addAction("About CondaNest…", self._on_about_clicked)
        bulk_btn = QPushButton("Bulk actions ▼")
        bulk_btn.setMenu(bulk_menu)
        toolbar.addWidget(bulk_btn)

        # --- Central layout: sidebar + main panel ---
        outer = QWidget()
        outer_layout = QHBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Horizontal)
        outer_layout.addWidget(splitter)

        # Sidebar: environments list with header
        sidebar_widget = QWidget()
        sidebar_layout = QVBoxLayout(sidebar_widget)
        sidebar_layout.setContentsMargins(6, 6, 6, 6)
        sidebar_layout.setSpacing(6)

        sidebar_label = QLabel("Environments")
        font = sidebar_label.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 1)
        sidebar_label.setFont(font)
        sidebar_layout.addWidget(sidebar_label)

        self._env_list = QListWidget()
        self._env_list.currentItemChanged.connect(self._on_env_selected)
        self._env_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._env_list.customContextMenuRequested.connect(self._on_env_context_menu)
        sidebar_layout.addWidget(self._env_list)

        splitter.addWidget(sidebar_widget)

        # Main panel: environment details + packages
        main_panel = QWidget()
        main_layout = QVBoxLayout(main_panel)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(8)

        self._backend_label = QLabel("Probing Conda/Mamba backend…")
        self._backend_label.setWordWrap(True)
        font = self._backend_label.font()
        font.setPointSize(font.pointSize() + 1)
        self._backend_label.setFont(font)

        self._env_title_label = QLabel("No environment selected")
        env_title_font = self._env_title_label.font()
        env_title_font.setBold(True)
        env_title_font.setPointSize(env_title_font.pointSize() + 2)
        self._env_title_label.setFont(env_title_font)

        self._env_path_label = QLabel("")
        self._env_path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self._channel_label = QLabel("")
        self._channel_label.setWordWrap(True)

        main_layout.addWidget(self._backend_label)
        main_layout.addSpacing(12)
        main_layout.addWidget(self._env_title_label)
        main_layout.addWidget(self._env_path_label)
        main_layout.addWidget(self._channel_label)

        # Packages header row: search + actions
        header_row = QHBoxLayout()
        self._search_entry = QLineEdit()
        self._search_entry.setPlaceholderText("Search packages…")
        self._search_entry.textChanged.connect(self._on_search_changed)

        self._install_button = QPushButton("Install…")
        self._install_button.clicked.connect(self._on_install_clicked)
        self._install_button.setEnabled(False)

        self._update_all_button = QPushButton("Update all…")
        self._update_all_button.clicked.connect(self._on_update_all_clicked)
        self._update_all_button.setEnabled(False)

        header_row.addWidget(self._search_entry, 1)
        header_row.addWidget(self._install_button)
        header_row.addWidget(self._update_all_button)

        main_layout.addSpacing(8)
        main_layout.addLayout(header_row)

        # Packages table.
        self._package_table = QTableWidget(0, 3, main_panel)
        self._package_table.setHorizontalHeaderLabels(["Name", "Version", "Channel"])
        self._package_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._package_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._package_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._package_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._package_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)

        main_layout.addWidget(self._package_table, 1)

        splitter.addWidget(main_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 740])

        self.setCentralWidget(outer)

        # Status bar with disk usage and progress
        status = QStatusBar()
        self._disk_label = QLabel("")
        self._status_label = QLabel("")
        self._progress = QProgressBar()
        self._progress.setMaximum(0)  # indeterminate
        self._progress.setVisible(False)

        status.addWidget(self._disk_label)
        status.addPermanentWidget(self._status_label, 1)
        status.addPermanentWidget(self._progress, 0)
        self.setStatusBar(status)

        # Kick off backend probing now that the UI is fully built.
        self._probe_backend()

    def _start_task(self, task: QRunnable) -> None:
        """Start a background task and keep a reference to it."""
        self._tasks.append(task)
        self._thread_pool.start(task)

    # --- Backend probe + env loading ---

    def _set_busy(self, message: Optional[str]) -> None:
        # Be defensive in case status widgets are not ready yet.
        if not hasattr(self, "_status_label") or not hasattr(self, "_progress"):
            return
        if message:
            self._status_label.setText(message)
            self._progress.setVisible(True)
        else:
            self._status_label.setText("")
            self._progress.setVisible(False)

    def _probe_backend(self) -> None:
        self._set_busy("Detecting Conda/Mamba backend…")
        task = _BackendProbeTask()
        task.signals.finished.connect(self._on_backend_detected)
        self._thread_pool.start(task)

    def _on_backend_detected(self, backend: Optional[BackendInfo], error: Optional[BaseException]) -> None:
        if error or backend is None:
            self._backend = None
            if isinstance(error, BackendNotFoundError):
                msg = (
                    "No Conda/Mamba backend found.\n\n"
                    "CondaNest can install Miniforge for you (recommended), or you can "
                    "point it at an existing Conda/Miniconda/Anaconda installation."
                )
                self._backend_label.setText(msg)
                self._set_busy(None)
                reply = QMessageBox.question(
                    self,
                    "No backend found",
                    msg + "\n\nWould you like to install Miniforge now?",
                    QMessageBox.Yes | QMessageBox.No,
                )
                if reply == QMessageBox.Yes:
                    self._on_install_miniforge_clicked()
                else:
                    reply2 = QMessageBox.question(
                        self,
                        "Locate existing installation",
                        "Would you like to locate an existing Conda/Mamba installation?",
                        QMessageBox.Yes | QMessageBox.No,
                    )
                    if reply2 == QMessageBox.Yes:
                        self._on_locate_conda_clicked()
            else:
                msg = f"Failed to detect backend:\n{error}" if error else "No Conda/Mamba backend found."
                self._backend_label.setText(msg)
                self._set_busy(None)
                QMessageBox.warning(self, "Backend not found", msg)
            return

        self._backend = backend
        self._backend_label.setText(
            f"Backend: {backend.kind} ({backend.version})\n"
            f"Root prefix: {backend.base_prefix or 'unknown'}"
        )
        self._load_envs()
        self._load_disk_usage()
        self._load_channels()

    def _load_envs(self) -> None:
        if self._backend is None:
            return
        self._set_busy("Loading environments…")
        task = _EnvListTask(self._backend)
        task.signals.finished.connect(self._on_envs_loaded)
        self._thread_pool.start(task)

    def _on_envs_loaded(self, envs: List[Environment], error: Optional[BaseException]) -> None:
        self._set_busy(None)
        self._env_list.clear()
        self._envs = envs or []

        if error is not None:
            QMessageBox.critical(self, "Failed to list environments", str(error))

        for env in self._envs:
            label = env.name
            if env.is_active:
                label += "  (active)"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, env)
            self._env_list.addItem(item)
            # Start async size calculation for this env.
            self._load_env_size(env)

        if self._env_list.count() > 0:
            self._env_list.setCurrentRow(0)

    def _load_env_size(self, env: Environment) -> None:
        """Load disk usage for a single environment asynchronously."""
        task = _EnvSizeTask(env)
        task.signals.finished.connect(self._on_env_size_loaded)
        self._thread_pool.start(task)

    def _on_env_size_loaded(self, env_name: str, size_str: str) -> None:
        """Update the displayed size for an environment in the sidebar."""
        self._env_sizes[env_name] = size_str
        # Update the item text to include size.
        for i in range(self._env_list.count()):
            item = self._env_list.item(i)
            env = item.data(Qt.UserRole)
            if isinstance(env, Environment) and env.name == env_name:
                label = env.name
                if env.is_active:
                    label += "  (active)"
                if size_str != "?":
                    label += f" • {size_str}"
                item.setText(label)
                break

    def _on_refresh_clicked(self) -> None:
        if self._backend is not None:
            self._load_envs()
            self._load_disk_usage()
            self._load_channels()

    # --- Selection handling ---

    def _on_env_selected(self, current: Optional[QListWidgetItem], _previous: Optional[QListWidgetItem]) -> None:
        if current is None:
            self._env_title_label.setText("No environment selected")
            self._env_path_label.setText("")
            self._current_env = None
            self._packages = []
            self._refresh_package_table()
            self._install_button.setEnabled(False)
            self._update_all_button.setEnabled(False)
            return

        env = current.data(Qt.UserRole)
        if not isinstance(env, Environment):
            self._env_title_label.setText("No environment selected")
            self._env_path_label.setText("")
            self._current_env = None
            self._packages = []
            self._refresh_package_table()
            self._install_button.setEnabled(False)
            self._update_all_button.setEnabled(False)
            return

        self._current_env = env
        self._env_title_label.setText(env.name)
        self._env_path_label.setText(str(env.path))
        self._load_packages(env)

    def _on_env_context_menu(self, pos) -> None:
        item = self._env_list.itemAt(pos)
        if item is None:
            return
        env = item.data(Qt.UserRole)
        if not isinstance(env, Environment):
            return

        menu = QMenu(self)
        menu.addAction("Clone…", lambda: self._on_clone_clicked(env))
        menu.addAction("Rename…", lambda: self._on_rename_clicked(env))
        menu.addAction("Export YAML…", lambda: self._on_export_yaml_clicked(env, no_builds=False))
        menu.addSeparator()
        menu.addAction("Delete…", lambda: self._on_delete_clicked(env))
        menu.exec(self._env_list.mapToGlobal(pos))

    # --- Packages handling ---

    def _load_packages(self, env: Environment) -> None:
        if self._backend is None:
            return
        self._set_busy(f"Loading packages for '{env.name}'…")
        task = _PackageListTask(self._backend, env)
        task.signals.finished.connect(self._on_packages_loaded)
        self._thread_pool.start(task)

    def _on_packages_loaded(
        self,
        packages: List[Package],
        error: Optional[BaseException],
    ) -> None:
        self._set_busy(None)
        if error is not None:
            QMessageBox.warning(self, "Failed to load packages", str(error))
        self._packages = packages or []
        self._install_button.setEnabled(self._current_env is not None)
        self._update_all_button.setEnabled(bool(self._packages) and self._current_env is not None)
        self._refresh_package_table()

    def _refresh_package_table(self) -> None:
        self._package_table.setRowCount(0)
        query = self._package_query.strip().lower()
        for pkg in self._packages:
            if query:
                name = pkg.name.lower()
                channel = (pkg.channel or "").lower()
                if query not in name and query not in channel:
                    continue
            row = self._package_table.rowCount()
            self._package_table.insertRow(row)

            name_item = QTableWidgetItem(pkg.name)
            version_item = QTableWidgetItem(pkg.version)
            channel_item = QTableWidgetItem(pkg.channel or "")

            name_item.setData(Qt.UserRole, pkg)

            self._package_table.setItem(row, 0, name_item)
            self._package_table.setItem(row, 1, version_item)
            self._package_table.setItem(row, 2, channel_item)

    def _on_search_changed(self, text: str) -> None:
        self._package_query = text
        self._refresh_package_table()

    # --- Package operations ---

    def _on_install_clicked(self) -> None:
        if self._backend is None or self._current_env is None:
            return
        spec, ok = QInputDialog.getText(
            self,
            "Install package",
            "Enter Conda package spec (e.g. numpy or numpy=1.26):",
        )
        if not ok:
            return
        spec = spec.strip()
        if not spec:
            return
        self._run_install_flow([spec])

    def _run_install_flow(self, specs: List[str]) -> None:
        if self._backend is None or self._current_env is None or not specs:
            return

        backend = self._backend
        env = self._current_env
        pretty = ", ".join(specs)

        def task_body() -> tuple[Optional[str], Optional[str]]:
            try:
                install_packages(backend, env, specs)
                return None, None
            except TermsOfServiceError as exc:
                return None, str(exc)
            except EnvOperationError as exc:
                return str(exc), None

        self._set_busy(f"Installing {pretty} into '{env.name}'…")

        class _InstallTask(QRunnable):
            class Signals(QObject):
                finished = Signal(object, object)  # error_msg: Optional[str], tos_error: Optional[str]

            def __init__(self) -> None:
                super().__init__()
                self.signals = self.Signals()

            def run(self) -> None:  # type: ignore[override]
                err, tos = task_body()
                self.signals.finished.emit(err, tos)

        task = _InstallTask()
        task.signals.finished.connect(
            lambda err, tos: self._on_install_finished(err, tos, env)
        )
        self._thread_pool.start(task)

    def _on_install_finished(
        self, error_msg: Optional[str], tos_error: Optional[str], env: Environment
    ) -> None:
        self._set_busy(None)
        if tos_error:
            self._show_tos_dialog("install packages into this environment", tos_error)
        elif error_msg:
            QMessageBox.critical(self, "Install failed", error_msg)
        else:
            if self._current_env is not None:
                self._load_packages(env)

    def _on_update_all_clicked(self) -> None:
        if self._backend is None or self._current_env is None:
            return
        env = self._current_env
        reply = QMessageBox.question(
            self,
            "Update all packages?",
            f"This will run 'conda update --all' on environment '{env.name}'.\n\nContinue?",
        )
        if reply != QMessageBox.Yes:
            return
        self._run_update_all_flow()

    def _run_update_all_flow(self) -> None:
        if self._backend is None or self._current_env is None:
            return

        backend = self._backend
        env = self._current_env

        def task_body() -> tuple[Optional[str], Optional[str]]:
            try:
                update_all_packages(backend, env)
                return None, None
            except TermsOfServiceError as exc:
                return None, str(exc)
            except EnvOperationError as exc:
                return str(exc), None

        self._set_busy(f"Updating all packages in '{env.name}'…")

        class _UpdateTask(QRunnable):
            class Signals(QObject):
                finished = Signal(object, object)  # error_msg: Optional[str], tos_error: Optional[str]

            def __init__(self) -> None:
                super().__init__()
                self.signals = self.Signals()

            def run(self) -> None:  # type: ignore[override]
                err, tos = task_body()
                self.signals.finished.emit(err, tos)

        task = _UpdateTask()
        task.signals.finished.connect(
            lambda err, tos: self._on_update_finished(err, tos, env)
        )
        self._thread_pool.start(task)

    def _on_update_finished(
        self, error_msg: Optional[str], tos_error: Optional[str], env: Environment
    ) -> None:
        self._set_busy(None)
        if tos_error:
            self._show_tos_dialog("update all packages in this environment", tos_error)
        elif error_msg:
            QMessageBox.critical(self, "Update all failed", error_msg)
        else:
            if self._current_env is not None:
                self._load_packages(env)

    def _show_tos_dialog(self, action: str, raw_error: str) -> None:
        """Show a friendly explanation for CondaToSNonInteractiveError with copy-paste commands."""
        exe = None
        if self._backend is not None:
            exe = self._backend.executable
        exe_display = str(exe) if exe else "conda"

        msg = (
            f"Conda reported that it cannot {action} because the Anaconda defaults "
            "channels require accepting their Terms of Service first.\n\n"
            "To accept these terms for the defaults channels, run the following "
            "commands once in a terminal (they use the same Conda executable "
            "that CondaNest is using), then retry this action:"
        )

        dialog = QDialog(self)
        dialog.setWindowTitle("Accept Anaconda Terms of Service in a terminal")
        layout = QVBoxLayout(dialog)

        msg_label = QLabel(msg)
        msg_label.setWordWrap(True)
        layout.addWidget(msg_label)

        commands_label = QLabel(
            "Commands to copy:\n"
            "1) Open a new terminal\n"
            "2) Copy the following commands and run them"
        )
        layout.addWidget(commands_label)

        commands_text = (
            f"{exe_display} tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main\n"
            f"{exe_display} tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r"
        )

        commands_view = QTextEdit()
        commands_view.setReadOnly(True)
        commands_view.setPlainText(commands_text)
        commands_view.setFont(Qt.FontFamily.Monospace)
        commands_view.setMinimumHeight(100)
        layout.addWidget(commands_view)

        if raw_error:
            from PySide6.QtWidgets import QGroupBox
            error_group = QGroupBox("Show Conda error details")
            error_layout = QVBoxLayout()
            error_view = QTextEdit()
            error_view.setReadOnly(True)
            error_view.setPlainText(raw_error)
            error_view.setFont(Qt.FontFamily.Monospace)
            error_view.setMaximumHeight(150)
            error_layout.addWidget(error_view)
            error_group.setLayout(error_layout)
            layout.addWidget(error_group)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        dialog.exec()

    # --- Environment operations ---

    def _on_create_env_clicked(self) -> None:
        if self._backend is None:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Create new environment")
        layout = QVBoxLayout(dialog)

        name_label = QLabel("Environment name:")
        name_entry = QLineEdit()
        layout.addWidget(name_label)
        layout.addWidget(name_entry)

        version_label = QLabel("Python version (optional, e.g. 3.11):")
        version_entry = QLineEdit()
        layout.addWidget(version_label)
        layout.addWidget(version_entry)

        file_label = QLabel("Or create from environment.yml file:")
        file_row = QHBoxLayout()
        file_entry = QLineEdit()
        file_btn = QPushButton("Browse…")
        file_btn.clicked.connect(
            lambda: file_entry.setText(
                QFileDialog.getOpenFileName(
                    dialog, "Select environment.yml", "", "YAML files (*.yml *.yaml)"
                )[0] or ""
            )
        )
        file_row.addWidget(file_entry, 1)
        file_row.addWidget(file_btn)
        layout.addWidget(file_label)
        layout.addLayout(file_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.Accepted:
            return

        yaml_path = file_entry.text().strip()
        name = name_entry.text().strip()
        python_version = version_entry.text().strip() or None

        if yaml_path:
            self._run_create_from_file_flow(name or None, Path(yaml_path))
        elif name:
            self._run_create_flow(name, python_version)

    def _run_create_flow(self, name: str, python_version: Optional[str]) -> None:
        if self._backend is None:
            return

        def task_body() -> tuple[Optional[str], Optional[str]]:
            try:
                create_environment(self._backend, name, python_version)
                return None, None
            except TermsOfServiceError as exc:
                return None, str(exc)
            except EnvOperationError as exc:
                return str(exc), None

        self._set_busy(f"Creating environment '{name}'…")

        class _CreateTask(QRunnable):
            class Signals(QObject):
                finished = Signal(object, object)  # error_msg: Optional[str], tos_error: Optional[str]

            def __init__(self) -> None:
                super().__init__()
                self.signals = self.Signals()

            def run(self) -> None:  # type: ignore[override]
                err, tos = task_body()
                self.signals.finished.emit(err, tos)

        task = _CreateTask()
        task.signals.finished.connect(
            lambda err, tos: self._on_create_finished(err, tos, name)
        )
        self._thread_pool.start(task)

    def _on_create_finished(
        self, error_msg: Optional[str], tos_error: Optional[str], name: str
    ) -> None:
        self._set_busy(None)
        if tos_error:
            self._show_tos_dialog("create this environment", tos_error)
        elif error_msg:
            QMessageBox.critical(self, "Create failed", error_msg)
        else:
            self._load_envs()
            self._select_env_by_name(name)

    def _run_create_from_file_flow(self, name: Optional[str], yaml_path: Path) -> None:
        if self._backend is None:
            return

        def task_body() -> tuple[Optional[str], Optional[str]]:
            try:
                create_environment_from_file(self._backend, yaml_path, name=name)
                return None, None
            except TermsOfServiceError as exc:
                return None, str(exc)
            except EnvOperationError as exc:
                return str(exc), None

        label_name = name or yaml_path.stem
        self._set_busy(f"Creating environment '{label_name}' from file…")

        class _CreateFromFileTask(QRunnable):
            class Signals(QObject):
                finished = Signal(object, object)  # error_msg: Optional[str], tos_error: Optional[str]

            def __init__(self) -> None:
                super().__init__()
                self.signals = self.Signals()

            def run(self) -> None:  # type: ignore[override]
                err, tos = task_body()
                self.signals.finished.emit(err, tos)

        task = _CreateFromFileTask()
        task.signals.finished.connect(
            lambda err, tos: self._on_create_from_file_finished(err, tos, label_name, name)
        )
        self._thread_pool.start(task)

    def _on_create_from_file_finished(
        self, error_msg: Optional[str], tos_error: Optional[str], label_name: str, name: Optional[str]
    ) -> None:
        self._set_busy(None)
        if tos_error:
            self._show_tos_dialog("create this environment from file", tos_error)
        elif error_msg:
            QMessageBox.critical(self, "Create from file failed", error_msg)
        else:
            self._load_envs()
            if name:
                self._select_env_by_name(label_name)

    def _on_clone_clicked(self, env: Environment) -> None:
        if self._backend is None:
            return
        new_name, ok = QInputDialog.getText(
            self, "Clone environment", "Enter a name for the cloned environment:", text=f"{env.name}-copy"
        )
        if not ok or not new_name.strip():
            return
        new_name = new_name.strip()
        self._run_clone_flow(env, new_name)

    def _run_clone_flow(self, env: Environment, new_name: str) -> None:
        if self._backend is None:
            return

        def task_body() -> tuple[Optional[str], Optional[str]]:
            try:
                clone_environment(self._backend, env, new_name)
                return None, None
            except TermsOfServiceError as exc:
                return None, str(exc)
            except EnvOperationError as exc:
                return str(exc), None

        self._set_busy(f"Cloning environment '{env.name}' → '{new_name}'…")

        class _CloneTask(QRunnable):
            class Signals(QObject):
                finished = Signal(object, object)  # error_msg: Optional[str], tos_error: Optional[str]

            def __init__(self) -> None:
                super().__init__()
                self.signals = self.Signals()

            def run(self) -> None:  # type: ignore[override]
                err, tos = task_body()
                self.signals.finished.emit(err, tos)

        task = _CloneTask()
        task.signals.finished.connect(
            lambda err, tos: self._on_clone_finished(err, tos, new_name)
        )
        self._thread_pool.start(task)

    def _on_clone_finished(
        self, error_msg: Optional[str], tos_error: Optional[str], new_name: str
    ) -> None:
        self._set_busy(None)
        if tos_error:
            self._show_tos_dialog("clone this environment", tos_error)
        elif error_msg:
            QMessageBox.critical(self, "Clone failed", error_msg)
        else:
            self._load_envs()
            self._select_env_by_name(new_name)

    def _on_rename_clicked(self, env: Environment) -> None:
        if self._backend is None:
            return
        new_name, ok = QInputDialog.getText(
            self, "Rename environment", "Enter a new name for this environment:", text=env.name
        )
        if not ok or not new_name.strip() or new_name.strip() == env.name:
            return
        new_name = new_name.strip()
        # Rename = clone + delete old
        self._run_rename_flow(env, new_name)

    def _run_rename_flow(self, env: Environment, new_name: str) -> None:
        if self._backend is None:
            return

        def task_body() -> tuple[Optional[str], Optional[str]]:
            try:
                new_env = clone_environment(self._backend, env, new_name)
                remove_environment(self._backend, env)
                return None, None
            except TermsOfServiceError as exc:
                return None, str(exc)
            except EnvOperationError as exc:
                return str(exc), None

        self._set_busy(f"Renaming environment '{env.name}' → '{new_name}'…")

        class _RenameTask(QRunnable):
            class Signals(QObject):
                finished = Signal(object, object)  # error_msg: Optional[str], tos_error: Optional[str]

            def __init__(self) -> None:
                super().__init__()
                self.signals = self.Signals()

            def run(self) -> None:  # type: ignore[override]
                err, tos = task_body()
                self.signals.finished.emit(err, tos)

        task = _RenameTask()
        task.signals.finished.connect(
            lambda err, tos: self._on_rename_finished(err, tos, new_name)
        )
        self._thread_pool.start(task)

    def _on_rename_finished(
        self, error_msg: Optional[str], tos_error: Optional[str], new_name: str
    ) -> None:
        self._set_busy(None)
        if tos_error:
            self._show_tos_dialog("rename this environment", tos_error)
        elif error_msg:
            QMessageBox.critical(self, "Rename failed", error_msg)
        else:
            self._load_envs()
            self._select_env_by_name(new_name)

    def _on_delete_clicked(self, env: Environment) -> None:
        if self._backend is None:
            return
        reply = QMessageBox.question(
            self,
            "Delete environment",
            f"Are you sure you want to permanently delete '{env.name}'?\nThis cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self._run_delete_flow(env)

    def _run_delete_flow(self, env: Environment) -> None:
        if self._backend is None:
            return

        def task_body() -> tuple[Optional[str], Optional[str]]:
            try:
                remove_environment(self._backend, env)
                return None, None
            except TermsOfServiceError as exc:
                return None, str(exc)
            except EnvOperationError as exc:
                return str(exc), None

        self._set_busy(f"Deleting environment '{env.name}'…")

        class _DeleteTask(QRunnable):
            class Signals(QObject):
                finished = Signal(object, object)  # error_msg: Optional[str], tos_error: Optional[str]

            def __init__(self) -> None:
                super().__init__()
                self.signals = self.Signals()

            def run(self) -> None:  # type: ignore[override]
                err, tos = task_body()
                self.signals.finished.emit(err, tos)

        task = _DeleteTask()
        task.signals.finished.connect(
            lambda err, tos: self._on_delete_finished(err, tos)
        )
        self._thread_pool.start(task)

    def _on_delete_finished(
        self, error_msg: Optional[str], tos_error: Optional[str]
    ) -> None:
        self._set_busy(None)
        if tos_error:
            self._show_tos_dialog("delete this environment", tos_error)
        elif error_msg:
            QMessageBox.critical(self, "Delete failed", error_msg)
        else:
            self._load_envs()
            self._current_env = None

    def _on_export_yaml_clicked(self, env: Environment, no_builds: bool = False) -> None:
        if self._backend is None:
            return
        suggested_name = f"{env.name}.yml"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export environment YAML (no builds)" if no_builds else "Export environment YAML",
            suggested_name,
            "YAML files (*.yml *.yaml)",
        )
        if not path:
            return
        self._run_export_yaml_flow(env, Path(path), no_builds=no_builds)

    def _run_export_yaml_flow(self, env: Environment, dest: Path, no_builds: bool = False) -> None:
        if self._backend is None:
            return

        def task_body() -> Optional[str]:
            try:
                # Mirror: conda env export [--no-builds] > environment.yml
                export_environment_yaml(self._backend, env, dest, no_builds=no_builds)
                return None
            except EnvOperationError as exc:
                return str(exc)

        self._set_busy(f"Exporting environment '{env.name}' to YAML…")
        task = _GenericTask(task_body)
        task.signals.finished.connect(
            lambda result, error: (
                self._set_busy(None),
                QMessageBox.critical(self, "Export failed", str(error)) if error
                else QMessageBox.information(self, "Export complete", f"Environment exported to:\n{dest}")
            )
        )
        self._thread_pool.start(task)


    def _select_env_by_name(self, name: str) -> None:
        for i in range(self._env_list.count()):
            item = self._env_list.item(i)
            env = item.data(Qt.UserRole)
            if isinstance(env, Environment) and env.name == name:
                self._env_list.setCurrentRow(i)
                break

    # --- Disk usage and clean ---

    def _load_disk_usage(self) -> None:
        if self._backend is None:
            return

        def task_body() -> DiskUsageReport:
            return get_disk_usage_report(self._backend)

        task = _GenericTask(task_body)
        task.signals.finished.connect(self._on_disk_usage_loaded)
        self._thread_pool.start(task)

    def _on_disk_usage_loaded(self, report: DiskUsageReport, error: Optional[BaseException]) -> None:
        if error:
            return
        self._disk_usage = report
        total_str = self._format_bytes(report.total)
        self._disk_label.setText(f"Total Conda disk usage: {total_str}")
        self._clean_button.setEnabled(report.total > 0)

    @staticmethod
    def _format_bytes(num_bytes: int) -> str:
        if num_bytes <= 0:
            return "0 B"
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if num_bytes < 1024 or unit == "TB":
                return f"{num_bytes:.1f} {unit}"
            num_bytes /= 1024.0
        return f"{num_bytes:.1f} TB"

    def _on_clean_clicked(self) -> None:
        if self._backend is None:
            return
        est = self._format_bytes(self._disk_usage.total) if self._disk_usage else "unknown"
        reply = QMessageBox.question(
            self,
            "Clean Conda caches and packages?",
            f"This will run 'conda clean --all' using the detected backend.\nEstimated total Conda usage: {est}.\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self._run_clean_flow()

    def _run_clean_flow(self) -> None:
        if self._backend is None:
            return

        def task_body() -> Optional[str]:
            try:
                run_global_clean(self._backend)
                return None
            except EnvOperationError as exc:
                return str(exc)

        self._set_busy("Cleaning Conda caches and package data…")
        task = _GenericTask(task_body)
        task.signals.finished.connect(
            lambda result, error: (
                self._set_busy(None),
                QMessageBox.critical(self, "Clean failed", str(error)) if error else self._load_disk_usage()
            )
        )
        self._thread_pool.start(task)

    # --- Channels ---

    def _load_channels(self) -> None:
        if self._backend is None:
            return

        def task_body() -> List[str]:
            return get_channel_priorities(self._backend)

        task = _GenericTask(task_body)
        task.signals.finished.connect(self._on_channels_loaded)
        self._thread_pool.start(task)

    def _on_channels_loaded(self, channels: List[str], error: Optional[BaseException]) -> None:
        if error:
            return
        if channels:
            self._channel_label.setText("Channel priority: " + ", ".join(channels))
        else:
            self._channel_label.setText("")

    def _on_manage_channels_clicked(self) -> None:
        if self._backend is None:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Manage Conda channels")
        layout = QVBoxLayout(dialog)

        channels = get_channel_priorities(self._backend)
        mode = get_channel_priority_mode(self._backend) or ""
        strict_enabled = mode.strip().lower() == "strict"

        strict_check = QCheckBox("Strict channel priority")
        strict_check.setChecked(strict_enabled)
        layout.addWidget(strict_check)

        channels_label = QLabel("Channels (top = highest priority):")
        layout.addWidget(channels_label)

        channels_list = QListWidget()
        channels_list.addItems(channels)
        layout.addWidget(channels_list)

        move_row = QHBoxLayout()
        move_up_btn = QPushButton("Move up")
        move_down_btn = QPushButton("Move down")

        def move_selected(delta: int) -> None:
            row = channels_list.currentRow()
            if row < 0:
                return
            new_row = row + delta
            if new_row < 0 or new_row >= channels_list.count():
                return
            item = channels_list.takeItem(row)
            channels_list.insertItem(new_row, item)
            channels_list.setCurrentRow(new_row)

        move_up_btn.clicked.connect(lambda: move_selected(-1))
        move_down_btn.clicked.connect(lambda: move_selected(1))
        move_row.addWidget(move_up_btn)
        move_row.addWidget(move_down_btn)
        layout.addLayout(move_row)

        add_row = QHBoxLayout()
        add_entry = QLineEdit()
        add_entry.setPlaceholderText("Enter channel name or URL (e.g., conda-forge)")
        add_btn = QPushButton("Add")
        add_btn.clicked.connect(
            lambda: (
                channels_list.addItem(add_entry.text().strip()) if add_entry.text().strip() else None,
                add_entry.clear(),
            )
        )
        add_row.addWidget(add_entry, 1)
        add_row.addWidget(add_btn)
        layout.addLayout(add_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.Accepted:
            return

        new_channels = [channels_list.item(i).text() for i in range(channels_list.count())]
        new_mode = "strict" if strict_check.isChecked() else "flexible"

        def task_body() -> Optional[str]:
            try:
                set_channel_priorities(self._backend, new_channels)
                set_channel_priority_mode(self._backend, new_mode)
                return None
            except EnvOperationError as exc:
                return str(exc)

        task = _GenericTask(task_body)
        task.signals.finished.connect(
            lambda result, error: (
                self._set_busy(None),
                QMessageBox.critical(self, "Failed to update channels", str(error)) if error else self._load_channels()
            )
        )
        self._thread_pool.start(task)

    # --- Bulk operations ---

    def _on_export_all_clicked(self) -> None:
        if not self._envs or self._backend is None:
            return
        folder = QFileDialog.getExistingDirectory(self, "Select folder to export all environments")
        if not folder:
            return
        self._run_export_all_flow(Path(folder))

    def _run_export_all_flow(self, dest_dir: Path) -> None:
        if self._backend is None:
            return

        def task_body() -> Optional[str]:
            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
                for env in self._envs:
                    dest = dest_dir / f"{env.name}.yml"
                    export_environment_yaml(self._backend, env, dest)
                return None
            except EnvOperationError as exc:
                return str(exc)

        self._set_busy("Exporting all environments to YAML…")
        task = _GenericTask(task_body)
        task.signals.finished.connect(
            lambda result, error: (
                self._set_busy(None),
                QMessageBox.critical(self, "Export all failed", str(error)) if error
                else QMessageBox.information(
                    self, "Export complete", f"Exported {len(self._envs)} environments to:\n{dest_dir}"
                )
            )
        )
        self._thread_pool.start(task)

    def _on_create_all_clicked(self) -> None:
        if self._backend is None:
            return
        folder = QFileDialog.getExistingDirectory(self, "Select folder with environment YAML files")
        if not folder:
            return
        dir_path = Path(folder)
        yaml_files = sorted([p for p in dir_path.iterdir() if p.suffix in {".yml", ".yaml"}])
        if not yaml_files:
            QMessageBox.warning(self, "No YAML files found", f"No .yml or .yaml files were found in:\n{dir_path}")
            return
        self._run_create_all_flow(yaml_files)

    def _run_create_all_flow(self, yaml_files: List[Path]) -> None:
        if self._backend is None:
            return

        def task_body() -> Optional[str]:
            try:
                for yaml_path in yaml_files:
                    name = yaml_path.stem
                    create_environment_from_file(self._backend, yaml_path, name=name)
                return None
            except EnvOperationError as exc:
                return str(exc)

        self._set_busy("Creating environments from folder…")
        task = _GenericTask(task_body)
        task.signals.finished.connect(
            lambda result, error: (
                self._set_busy(None),
                QMessageBox.critical(self, "Create from folder failed", str(error)) if error
                else (
                    self._load_envs(),
                    QMessageBox.information(
                        self,
                        "Create from folder complete",
                        f"Created environments from {len(yaml_files)} files.",
                    ),
                )
            )
        )
        self._thread_pool.start(task)

    # --- Miniforge installation ---

    def _on_install_miniforge_clicked(self) -> None:
        def progress(msg: str) -> None:
            self._set_busy(msg)

        def task_body() -> tuple[Optional[Path], Optional[str]]:
            try:
                install_dir = install_miniforge(progress=progress)
                return install_dir, None
            except EnvOperationError as exc:
                return None, str(exc)

        self._set_busy("Installing Miniforge…")
        task = _GenericTask(task_body)
        task.signals.finished.connect(self._on_miniforge_installed)
        self._thread_pool.start(task)

    def _on_miniforge_installed(self, result: tuple[Optional[Path], Optional[str]], error: Optional[BaseException]) -> None:
        self._set_busy(None)
        if error or (result and result[1]):
            msg = str(error) if error else (result[1] if result else "Unknown error")
            QMessageBox.critical(self, "Miniforge installation failed", msg)
        elif result and result[0]:
            QMessageBox.information(self, "Miniforge installed", f"Miniforge installed to {result[0]}\n\nRe-probing backend…")
            self._probe_backend()

    def _on_locate_conda_clicked(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Conda or Mamba executable", "", "Executable files (*)"
        )
        if not path:
            return

        cfg = load_config()
        cfg.conda_executable = path
        try:
            save_config(cfg)
        except Exception:  # noqa: BLE001
            pass

        self._set_busy("Checking selected Conda installation…")
        task = _BackendProbeTask()
        task.signals.finished.connect(self._on_backend_detected)
        self._thread_pool.start(task)

    def _on_about_clicked(self) -> None:
        """Open the CondaNest GitHub repository in the default browser."""
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        QDesktopServices.openUrl(QUrl("https://github.com/aradar46/condanest"))


def main(argv: Optional[list[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
