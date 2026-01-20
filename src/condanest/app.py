from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional
import threading

import gi  # type: ignore[import]

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gio, GLib, Gtk  # type: ignore[import]

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
    update_packages,
)
from .models import BackendInfo, DiskUsageReport, Environment, Package
from .config import load_config, save_config


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app)
        self.set_title("CondaNest")
        self.set_default_size(900, 600)

        self._backend: Optional[BackendInfo] = None
        self._envs: list[Environment] = []
        self._packages: list[Package] = []
        self._current_env: Optional[Environment] = None
        self._disk_usage: Optional[DiskUsageReport] = None
        self._channels: list[str] = []
        self._package_query: str = ""

        # Global operation status (simple footer indicator).
        self._status_label: Optional[Gtk.Label] = None
        self._status_spinner: Optional[Gtk.Spinner] = None
        self._no_backend_install_button: Optional[Gtk.Button] = None
        self._no_backend_locate_button: Optional[Gtk.Button] = None
        self._no_backend_status_label: Optional[Gtk.Label] = None

        self._init_ui()
        self._probe_backend_async()

    def _init_ui(self) -> None:
        # Libadwaita expects content inside a ToolbarView; set_titlebar()
        # is not supported on Adw.ApplicationWindow.
        self._toolbar_view = Adw.ToolbarView()
        self.set_content(self._toolbar_view)

        header_bar = Adw.HeaderBar()
        header_bar.set_title_widget(Gtk.Label(label="CondaNest"))

        self._backend_label = Gtk.Label(xalign=1.0)
        self._backend_label.add_css_class("dim-label")
        header_bar.pack_end(self._backend_label)

        # Top header now only shows the app title and backend label;
        # environment actions live in the sidebar header.
        self._toolbar_view.add_top_bar(header_bar)

        # Footer: disk usage label.
        footer_bar = Adw.ToolbarView()
        self._footer_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self._footer_box.set_margin_start(12)
        self._footer_box.set_margin_end(12)
        self._footer_box.set_margin_bottom(6)

        self._disk_label = Gtk.Label(xalign=0.0)
        self._disk_label.add_css_class("dim-label")
        self._footer_box.append(self._disk_label)

        # Spacer between disk usage and operation status.
        footer_spacer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        footer_spacer.set_hexpand(True)
        self._footer_box.append(footer_spacer)

        # Simple operation status indicator: spinner + text.
        self._status_spinner = Gtk.Spinner()
        self._status_spinner.set_visible(False)
        self._footer_box.append(self._status_spinner)

        self._status_label = Gtk.Label(xalign=1.0)
        # Make the operation status much more prominent than the disk label.
        # Use accent color and slightly larger title styling.
        self._status_label.add_css_class("title-4")
        self._status_label.add_css_class("accent")
        self._status_label.set_visible(False)
        self._footer_box.append(self._status_label)

        footer_bar.set_content(self._footer_box)
        self._toolbar_view.add_bottom_bar(footer_bar)

        self._stack = Adw.ViewStack()
        self._toolbar_view.set_content(self._stack)

        # Simple status pages without subclassing (AdwStatusPage is final on some versions).
        self._loading_page = Adw.StatusPage(
            title="Probing Conda/Mamba backend…",
            description="Detecting installed backend and base environment.",
            icon_name="system-search-symbolic",
        )
        self._error_page = Adw.StatusPage(
            title="No Conda installation found",
            description="",
            icon_name="dialog-warning-symbolic",
        )

        # Custom content for the "no backend" view: explain options and provide
        # one-click Miniforge install and a way to locate an existing install.
        no_backend_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        no_backend_box.set_halign(Gtk.Align.CENTER)
        no_backend_box.set_valign(Gtk.Align.CENTER)

        no_backend_title = Gtk.Label(label="No Conda installation found")
        no_backend_title.add_css_class("title-4")
        no_backend_title.set_xalign(0.5)
        no_backend_box.append(no_backend_title)

        no_backend_desc = Gtk.Label(
            label=(
                "CondaNest can install Miniforge for you (recommended), or you can "
                "point it at an existing Conda/Miniconda/Anaconda installation.\n\n"
                "If you choose an existing Miniconda/Anaconda that uses the "
                "Anaconda defaults channels, you may need to accept the "
                "Anaconda Terms of Service once in a terminal; CondaNest will "
                "show you the exact commands if that is required."
            )
        )
        no_backend_desc.add_css_class("dim-label")
        no_backend_desc.set_wrap(True)
        no_backend_desc.set_max_width_chars(64)
        no_backend_desc.set_xalign(0.5)
        no_backend_box.append(no_backend_desc)

        buttons_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._no_backend_install_button = Gtk.Button.new_with_label("Install Miniforge (recommended)")
        self._no_backend_install_button.add_css_class("suggested-action")
        self._no_backend_install_button.connect("clicked", self._on_install_miniforge_clicked)
        buttons_row.append(self._no_backend_install_button)

        self._no_backend_locate_button = Gtk.Button.new_with_label("Locate existing installation…")
        self._no_backend_locate_button.add_css_class("flat")
        self._no_backend_locate_button.connect("clicked", self._on_locate_conda_clicked)
        buttons_row.append(self._no_backend_locate_button)

        no_backend_box.append(buttons_row)

        self._no_backend_status_label = Gtk.Label(xalign=0.5)
        self._no_backend_status_label.add_css_class("dim-label")
        no_backend_box.append(self._no_backend_status_label)

        self._error_page.set_child(no_backend_box)

        # Main app view: sidebar with env list + main content stack.
        self._main_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)

        # Sidebar
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        sidebar.set_size_request(260, -1)

        # Environments header row: title + actions (clean, refresh, create, bulk menu).
        sidebar_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        sidebar_header.set_margin_start(12)
        sidebar_header.set_margin_top(12)

        sidebar_label = Gtk.Label(label="Environments", xalign=0.0)
        sidebar_label.add_css_class("title-4")
        sidebar_header.append(sidebar_label)

        # Simple spacer to push actions to the right.
        spacer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        spacer.set_hexpand(True)
        sidebar_header.append(spacer)

        # Clean button (uses global clean flow based on disk usage).
        # Use a compact text button instead of an ambiguous icon.
        self._clean_button = Gtk.Button.new_with_label("Clean")
        self._clean_button.add_css_class("flat")
        self._clean_button.set_tooltip_text("Clean Conda caches and package data")
        self._clean_button.set_sensitive(False)
        self._clean_button.connect("clicked", self._on_clean_global_clicked)
        sidebar_header.append(self._clean_button)

        # Sidebar refresh and create map to the same handlers as header buttons.
        sidebar_refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        sidebar_refresh.add_css_class("flat")
        sidebar_refresh.set_tooltip_text("Reload environments")
        sidebar_refresh.connect("clicked", self._on_refresh_clicked)
        sidebar_header.append(sidebar_refresh)

        sidebar_create = Gtk.Button.new_from_icon_name("list-add-symbolic")
        sidebar_create.add_css_class("flat")
        sidebar_create.set_tooltip_text("Create new environment")
        sidebar_create.connect("clicked", self._on_create_env_clicked)
        sidebar_header.append(sidebar_create)

        # Bulk actions button (three dots) for export all / create from folder.
        bulk_button = Gtk.Button.new_from_icon_name("open-menu-symbolic")
        bulk_button.add_css_class("flat")
        bulk_button.set_tooltip_text("Bulk actions")
        bulk_button.connect("clicked", self._on_bulk_actions_clicked)
        sidebar_header.append(bulk_button)

        sidebar.append(sidebar_header)

        self._env_list = Gtk.ListBox()
        self._env_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._env_list.add_css_class("navigation-sidebar")
        self._env_list.connect("row-selected", self._on_env_row_selected)
        sidebar.append(self._env_list)

        # Main content area
        self._main_content_stack = Adw.ViewStack()

        # Placeholder content when nothing is selected (smaller, less noisy text).
        self._env_placeholder_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._env_placeholder_box.set_halign(Gtk.Align.CENTER)
        self._env_placeholder_box.set_valign(Gtk.Align.CENTER)

        placeholder_icon = Gtk.Image.new_from_icon_name("applications-system-symbolic")
        placeholder_icon.set_pixel_size(72)
        self._env_placeholder_box.append(placeholder_icon)

        placeholder_title = Gtk.Label(label="No environment selected")
        placeholder_title.add_css_class("title-4")
        placeholder_title.set_xalign(0.5)
        self._env_placeholder_box.append(placeholder_title)

        placeholder_desc = Gtk.Label(
            label="Choose an environment from the sidebar to view its details."
        )
        placeholder_desc.add_css_class("dim-label")
        placeholder_desc.set_wrap(True)
        placeholder_desc.set_max_width_chars(32)
        placeholder_desc.set_xalign(0.5)
        self._env_placeholder_box.append(placeholder_desc)

        # Packages view for the selected environment.
        self._packages_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        # Title / subtitle for selected env.
        self._env_title_label = Gtk.Label(xalign=0.0)
        self._env_title_label.add_css_class("title-3")
        self._env_path_label = Gtk.Label(xalign=0.0)
        self._env_path_label.add_css_class("dim-label")
        self._env_title_label.set_margin_top(18)
        self._env_title_label.set_margin_start(18)
        self._env_path_label.set_margin_start(18)
        self._packages_box.append(self._env_title_label)
        self._packages_box.append(self._env_path_label)

        # Packages header.
        packages_header = Gtk.Label(label="Installed packages", xalign=0.0)
        packages_header.add_css_class("title-4")
        packages_header.set_margin_start(18)
        packages_header.set_margin_top(18)
        self._packages_box.append(packages_header)

        # Channel priority label.
        self._channel_label = Gtk.Label(xalign=0.0)
        self._channel_label.add_css_class("dim-label")
        self._channel_label.set_margin_start(18)
        self._channel_label.set_margin_top(4)
        self._packages_box.append(self._channel_label)

        # Search + actions row.
        actions_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        actions_row.set_margin_start(18)
        actions_row.set_margin_end(18)
        actions_row.set_margin_top(6)

        # Package search entry (expands to take remaining space).
        self._package_search_entry = Gtk.SearchEntry()
        self._package_search_entry.set_placeholder_text("Search packages…")
        self._package_search_entry.set_hexpand(True)
        self._package_search_entry.connect("search-changed", self._on_package_search_changed)
        actions_row.append(self._package_search_entry)

        # Compact text buttons for install / update-all on the right.
        install_button = Gtk.Button.new_with_label("Install…")
        install_button.add_css_class("flat")
        install_button.set_tooltip_text("Install a package into this environment")
        install_button.connect("clicked", self._on_install_package_clicked)
        actions_row.append(install_button)

        update_all_button = Gtk.Button.new_with_label("Update all…")
        update_all_button.add_css_class("flat")
        update_all_button.set_tooltip_text("Update all packages in this environment")
        update_all_button.connect("clicked", self._on_update_all_clicked)
        actions_row.append(update_all_button)

        self._packages_box.append(actions_row)

        # Packages list inside its own scrolled window, so only the list
        # area scrolls and the header stays fixed.
        self._package_list = Gtk.ListBox()
        self._package_list.add_css_class("boxed-list")

        package_scroller = Gtk.ScrolledWindow()
        package_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        package_scroller.set_child(self._package_list)
        package_scroller.set_margin_start(18)
        package_scroller.set_margin_end(18)
        package_scroller.set_margin_bottom(18)
        package_scroller.set_vexpand(True)

        self._packages_box.append(package_scroller)

        self._main_content_stack.add_named(self._env_placeholder_box, "placeholder")
        self._main_content_stack.add_named(self._packages_box, "packages")
        self._main_content_stack.set_visible_child(self._env_placeholder_box)

        self._main_box.append(sidebar)

        # Visual separator between sidebar and main content.
        separator = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        separator.set_vexpand(True)
        self._main_box.append(separator)

        self._main_box.append(self._main_content_stack)

        self._stack.add_named(self._loading_page, "loading")
        self._stack.add_named(self._error_page, "error")
        self._stack.add_named(self._main_box, "main")
        self._stack.set_visible_child(self._loading_page)

    def _on_refresh_clicked(self, _button: Gtk.Button) -> None:
        if self._backend is not None:
            self._load_envs_async()

    def _on_backend_detected(self, backend: Optional[BackendInfo]) -> bool:
        if backend is None:
            self._stack.set_visible_child(self._error_page)
            self._backend_label.set_label("No backend")
        else:
            self._backend = backend
            backend_str = f"{backend.kind} ({backend.version})"
            self._backend_label.set_label(backend_str)
            # Switch to the main app view and load environments.
            self._stack.set_visible_child(self._main_box)
            self._load_envs_async()
            self._load_disk_usage_async()
            self._load_channels_async()
        # Returning False removes this idle callback.
        return False

    def _populate_env_list(self, envs: list[Environment]) -> None:
        # Clear existing rows (GTK4: iterate via get_first_child / get_next_sibling).
        child = self._env_list.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self._env_list.remove(child)
            child = next_child

        self._envs = sorted(envs, key=lambda e: e.name)

        for env in self._envs:
            row = Adw.ActionRow(
                title=env.name,
                subtitle=str(env.path),
            )
            if env.is_active:
                row.add_css_class("accent")

            # Attach the Environment object to the row for later lookup.
            row.environment = env  # type: ignore[attr-defined]

            # Row suffix actions: export, rename, clone, delete.
            export_button = Gtk.Button.new_with_label("YML")
            export_button.add_css_class("flat")
            export_button.set_tooltip_text("Export environment YAML")
            export_button.connect("clicked", self._on_env_export_clicked, env)
            row.add_suffix(export_button)

            rename_button = Gtk.Button.new_from_icon_name("document-edit-symbolic")
            rename_button.add_css_class("flat")
            rename_button.set_tooltip_text("Rename environment")
            rename_button.connect("clicked", self._on_env_rename_clicked, env)
            row.add_suffix(rename_button)

            clone_button = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
            clone_button.add_css_class("flat")
            clone_button.set_tooltip_text("Clone environment")
            clone_button.connect("clicked", self._on_env_clone_clicked, env)
            row.add_suffix(clone_button)

            delete_button = Gtk.Button.new_from_icon_name("user-trash-symbolic")
            delete_button.add_css_class("flat")
            delete_button.set_tooltip_text("Delete environment")
            delete_button.connect("clicked", self._on_env_delete_clicked, env)
            row.add_suffix(delete_button)

            self._env_list.append(row)

        # Reset main content to placeholder until user selects.
        self._main_content_stack.set_visible_child(self._env_placeholder_box)
        # Kick off async size calculations for each environment.
        self._load_env_sizes_async()

    def _on_env_row_selected(self, _listbox: Gtk.ListBox, row: Optional[Gtk.ListBoxRow]) -> None:
        if row is None:
            self._main_content_stack.set_visible_child(self._env_placeholder_box)
            return

        env: Environment = getattr(row, "environment")
        self._current_env = env
        # Update header.
        self._env_title_label.set_label(env.name)
        self._env_path_label.set_label(str(env.path))
        # Show packages view and load package list.
        self._main_content_stack.set_visible_child(self._packages_box)
        self._load_packages_async(env)

    def _load_envs_async(self) -> None:
        if self._backend is None:
            return

        backend = self._backend

        def worker() -> None:
            try:
                envs = list_envs(backend)
            except Exception:
                envs = []
            GLib.idle_add(lambda: self._populate_env_list(envs))

        threading.Thread(target=worker, daemon=True).start()

    def _load_env_sizes_async(self) -> None:
        if not self._envs:
            return

        envs = list(self._envs)

        def worker() -> None:
            for env in envs:
                size_bytes = env_disk_usage_bytes(env)
                size_str = self._format_bytes(size_bytes)

                def update_row(e: Environment = env, size: str = size_str) -> None:
                    child = self._env_list.get_first_child()
                    while child is not None:
                        row = child
                        row_env = getattr(row, "environment", None)
                        if isinstance(row_env, Environment) and row_env.name == e.name:
                            # Show "path • size" in subtitle.
                            row.set_subtitle(f"{row_env.path} • {size}")
                            break
                        child = child.get_next_sibling()

                GLib.idle_add(update_row)

        threading.Thread(target=worker, daemon=True).start()

    def _on_env_export_clicked(self, _button: Gtk.Button, env: Environment) -> None:
        if self._backend is None:
            return

        dialog = Gtk.FileChooserNative.new(
            title="Export environment YAML",
            parent=self,
            action=Gtk.FileChooserAction.SAVE,
            accept_label="Export",
            cancel_label="Cancel",
        )
        dialog.set_current_name(f"{env.name}.yml")

        def on_response(dlg: Gtk.FileChooserNative, response: int) -> None:
            if response != Gtk.ResponseType.ACCEPT:
                dlg.destroy()
                return
            path = dlg.get_file()
            dlg.destroy()
            if path is None:
                return
            dest = Path(path.get_path())
            self._run_export_flow(env, dest)

        dialog.connect("response", on_response)
        dialog.show()

    def _on_env_rename_clicked(self, _button: Gtk.Button, env: Environment) -> None:
        self._show_rename_dialog(env)

    def _on_env_clone_clicked(self, _button: Gtk.Button, env: Environment) -> None:
        if self._backend is None:
            return

        dialog = Adw.MessageDialog.new(
            self,
            "Clone environment",
            "Enter a name for the cloned environment.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("clone", "Clone")
        dialog.set_response_appearance("clone", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("clone")
        dialog.set_close_response("cancel")

        entry = Gtk.Entry()
        entry.set_text(f"{env.name}-copy")
        entry.set_activates_default(True)
        dialog.set_extra_child(entry)

        def on_response(_dlg: Adw.MessageDialog, response: str) -> None:
            if response != "clone":
                return
            new_name = entry.get_text().strip()
            if not new_name or new_name == env.name:
                return
            self._run_clone_flow(env, new_name)

        dialog.connect("response", on_response)
        dialog.present()

    def _run_clone_flow(self, env: Environment, new_name: str) -> None:
        if self._backend is None:
            return

        backend = self._backend
        self.set_sensitive(False)
        self._set_operation_status(f"Cloning environment '{env.name}' → '{new_name}'…")

        def worker() -> None:
            error_msg: Optional[str] = None
            tos_error: Optional[str] = None
            try:
                clone_environment(backend, env, new_name)
            except TermsOfServiceError as exc:
                tos_error = str(exc)
            except EnvOperationError as exc:
                error_msg = str(exc)

            def finish() -> None:
                self.set_sensitive(True)
                self._set_operation_status(None)
                if tos_error:
                    self._show_tos_dialog("clone this environment", tos_error)
                elif error_msg:
                    dialog = Adw.MessageDialog.new(
                        self,
                        "Clone failed",
                        error_msg,
                    )
                    dialog.add_response("close", "Close")
                    dialog.set_close_response("close")
                    dialog.present()
                else:
                    self._load_envs_async()
                    self._select_env_by_name(new_name)

            GLib.idle_add(finish)

        threading.Thread(target=worker, daemon=True).start()

    def _on_env_delete_clicked(self, _button: Gtk.Button, env: Environment) -> None:
        if self._backend is None:
            return

        dialog = Adw.MessageDialog.new(
            self,
            "Delete environment",
            f"Are you sure you want to permanently delete '{env.name}'?\nThis cannot be undone.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")

        def on_response(_dlg: Adw.MessageDialog, response: str) -> None:
            if response != "delete":
                return
            self._run_delete_flow(env)

        dialog.connect("response", on_response)
        dialog.present()

    def _run_delete_flow(self, env: Environment) -> None:
        if self._backend is None:
            return

        backend = self._backend
        self.set_sensitive(False)
        self._set_operation_status(f"Deleting environment '{env.name}'…")

        def worker() -> None:
            error_msg: Optional[str] = None
            tos_error: Optional[str] = None
            try:
                remove_environment(backend, env)
            except TermsOfServiceError as exc:
                tos_error = str(exc)
            except EnvOperationError as exc:
                error_msg = str(exc)

            def finish() -> None:
                self.set_sensitive(True)
                self._set_operation_status(None)
                if tos_error:
                    self._show_tos_dialog("delete this environment", tos_error)
                elif error_msg:
                    dialog = Adw.MessageDialog.new(
                        self,
                        "Delete failed",
                        error_msg,
                    )
                    dialog.add_response("close", "Close")
                    dialog.set_close_response("close")
                    dialog.present()
                else:
                    self._load_envs_async()
                    self._current_env = None

            GLib.idle_add(finish)

        threading.Thread(target=worker, daemon=True).start()

    def _on_env_clean_clicked(self, _button: Gtk.Button, env: Environment) -> None:
        # For now, per-env clean is just a shortcut to the global janitor dialog.
        self._show_clean_dialog()

    def _run_export_flow(self, env: Environment, dest: Path) -> None:
        if self._backend is None:
            return

        backend = self._backend
        self.set_sensitive(False)
        self._set_operation_status(f"Exporting environment '{env.name}' to YAML…")

        def worker() -> None:
            error_msg: Optional[str] = None
            try:
                export_environment_yaml(backend, env, dest)
            except EnvOperationError as exc:
                error_msg = str(exc)

            def finish() -> None:
                self.set_sensitive(True)
                self._set_operation_status(None)
                if error_msg:
                    dialog = Adw.MessageDialog.new(
                        self,
                        "Export failed",
                        error_msg,
                    )
                    dialog.add_response("close", "Close")
                    dialog.set_close_response("close")
                    dialog.present()
                else:
                    dialog = Adw.MessageDialog.new(
                        self,
                        "Environment exported",
                        f"Environment '{env.name}' was exported to:\n{dest}",
                    )
                    dialog.add_response("close", "Close")
                    dialog.set_close_response("close")
                    dialog.present()

            GLib.idle_add(finish)

        threading.Thread(target=worker, daemon=True).start()
    def _populate_package_list(self, packages: list[Package]) -> None:
        # Store full package list and reset search query before applying filter.
        self._packages = packages
        self._package_query = ""
        if hasattr(self, "_package_search_entry") and self._package_search_entry is not None:
            self._package_search_entry.set_text("")
        self._apply_package_filter()

    def _load_packages_async(self, env: Environment) -> None:
        if self._backend is None:
            return

        backend = self._backend

        def worker() -> None:
            try:
                packages = list_installed_packages(backend, env)
            except Exception:
                packages = []
            GLib.idle_add(lambda: self._populate_package_list(packages))

        threading.Thread(target=worker, daemon=True).start()

    def _on_package_search_changed(self, entry: Gtk.SearchEntry) -> None:
        self._package_query = entry.get_text().strip().lower()
        self._apply_package_filter()

    def _apply_package_filter(self) -> None:
        # Clear existing rows.
        child = self._package_list.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self._package_list.remove(child)
            child = next_child

        query = self._package_query

        for pkg in self._packages:
            if query:
                name = pkg.name.lower()
                channel = (pkg.channel or "").lower()
                if query not in name and query not in channel:
                    continue
            subtitle_parts = [pkg.version]
            if pkg.channel:
                subtitle_parts.append(pkg.channel)
            subtitle = " • ".join(subtitle_parts)
            row = Adw.ActionRow(
                title=pkg.name,
                subtitle=subtitle,
            )
            self._package_list.append(row)

    def _probe_backend_async(self) -> None:
        def worker() -> None:
            try:
                backend = detect_backend()
            except BackendNotFoundError:
                backend = None

            # Marshal UI update back onto the main thread.
            GLib.idle_add(self._on_backend_detected, backend)

        threading.Thread(target=worker, daemon=True).start()

    # --- Backend bootstrap / Miniforge install ---

    def _set_no_backend_status(self, message: str) -> None:
        if self._no_backend_status_label is not None:
            self._no_backend_status_label.set_label(message)

    def _set_no_backend_buttons_sensitive(self, value: bool) -> None:
        if self._no_backend_install_button is not None:
            self._no_backend_install_button.set_sensitive(value)
        if self._no_backend_locate_button is not None:
            self._no_backend_locate_button.set_sensitive(value)

    def _on_install_miniforge_clicked(self, _button: Gtk.Button) -> None:
        # Start background Miniforge installation and re-probe the backend.
        if self._backend is not None:
            return

        self._set_no_backend_buttons_sensitive(False)
        self._set_no_backend_status("Downloading and installing Miniforge…")

        def progress(msg: str) -> None:
            GLib.idle_add(self._set_no_backend_status, msg)

        def worker() -> None:
            error_msg: Optional[str] = None
            new_backend: Optional[BackendInfo] = None
            try:
                install_miniforge(progress=progress)
                new_backend = detect_backend()
            except (EnvOperationError, BackendNotFoundError) as exc:
                error_msg = str(exc)
            except Exception as exc:  # noqa: BLE001
                error_msg = str(exc)

            def finish() -> None:
                if error_msg:
                    self._set_no_backend_buttons_sensitive(True)
                    self._set_no_backend_status("")
                    dialog = Adw.MessageDialog.new(
                        self,
                        "Miniforge installation failed",
                        error_msg,
                    )
                    dialog.add_response("close", "Close")
                    dialog.set_close_response("close")
                    dialog.present()
                elif new_backend is not None:
                    # Hand over to the normal backend-detected flow.
                    self._on_backend_detected(new_backend)

            GLib.idle_add(finish)

        threading.Thread(target=worker, daemon=True).start()

    def _on_locate_conda_clicked(self, _button: Gtk.Button) -> None:
        # Let the user pick an existing conda/mamba executable and re-probe.
        chooser = Gtk.FileChooserNative.new(
            title="Select Conda or Mamba executable",
            parent=self,
            action=Gtk.FileChooserAction.OPEN,
            accept_label="Select",
            cancel_label="Cancel",
        )

        def on_response(dlg: Gtk.FileChooserNative, response: int) -> None:
            if response != Gtk.ResponseType.ACCEPT:
                dlg.destroy()
                return
            file_obj = dlg.get_file()
            dlg.destroy()
            if file_obj is None:
                return
            path_str = file_obj.get_path()
            if not path_str:
                return

            cfg = load_config()
            cfg.conda_executable = path_str
            try:
                save_config(cfg)
            except Exception:  # noqa: BLE001
                pass

            # Update status and re-probe backend with the new configuration.
            self._set_no_backend_status("Checking selected Conda installation…")

            def worker() -> None:
                backend: Optional[BackendInfo]
                try:
                    backend = detect_backend()
                except BackendNotFoundError:
                    backend = None

                def finish() -> None:
                    if backend is None:
                        self._set_no_backend_status(
                            "The selected executable did not look like a working Conda/Mamba."
                        )
                    else:
                        self._on_backend_detected(backend)

                GLib.idle_add(finish)

            threading.Thread(target=worker, daemon=True).start()

        chooser.connect("response", on_response)
        chooser.show()

    def _on_rename_clicked(self, _button: Gtk.Button) -> None:
        if self._current_env is None or self._backend is None:
            return

        env = self._current_env
        self._show_rename_dialog(env)

    def _show_rename_dialog(self, env: Environment) -> None:
        dialog = Adw.MessageDialog.new(
            self,
            "Rename environment",
            "Enter a new name for this environment. The environment will be cloned and the old one can be removed.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("rename", "Rename")
        dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("rename")
        dialog.set_close_response("cancel")

        entry = Gtk.Entry()
        entry.set_text(env.name)
        entry.set_activates_default(True)
        dialog.set_extra_child(entry)

        def on_response(_dlg: Adw.MessageDialog, response: str) -> None:
            if response != "rename":
                return
            new_name = entry.get_text().strip()
            if not new_name or new_name == env.name:
                return
            self._run_rename_flow(env, new_name)

        dialog.connect("response", on_response)
        dialog.present()

    def _run_rename_flow(self, env: Environment, new_name: str) -> None:
        if self._backend is None:
            return

        backend = self._backend
        self.set_sensitive(False)

        def worker() -> None:
            error_msg: Optional[str] = None
            try:
                new_env = clone_environment(backend, env, new_name)
                # Only attempt removal if clone succeeded.
                remove_environment(backend, env)
            except EnvOperationError as exc:
                error_msg = str(exc)
                new_env = None  # type: ignore[assignment]

            def finish() -> None:
                self.set_sensitive(True)
                if error_msg:
                    dialog = Adw.MessageDialog.new(
                        self,
                        "Rename failed",
                        error_msg,
                    )
                    dialog.add_response("close", "Close")
                    dialog.set_close_response("close")
                    dialog.present()
                else:
                    # Reload envs and try to select the new one.
                    self._load_envs_async()
                    self._select_env_by_name(new_name)

            GLib.idle_add(finish)

        threading.Thread(target=worker, daemon=True).start()

    def _select_env_by_name(self, name: str) -> None:
        # Called after env list reload; best-effort selection.
        child = self._env_list.get_first_child()
        while child is not None:
            row = child  # Gtk.ListBoxRow / Adw.ActionRow
            env = getattr(row, "environment", None)
            if isinstance(env, Environment) and env.name == name:
                self._env_list.select_row(row)
                break
            child = child.get_next_sibling()

    # --- Disk usage / Janitor ---

    @staticmethod
    def _format_bytes(num_bytes: int) -> str:
        if num_bytes <= 0:
            return "0 B"
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if num_bytes < 1024 or unit == "TB":
                return f"{num_bytes:.1f} {unit}"
            num_bytes /= 1024.0
        return f"{num_bytes:.1f} TB"

    def _load_disk_usage_async(self) -> None:
        if self._backend is None:
            return

        backend = self._backend

        def worker() -> None:
            try:
                report = get_disk_usage_report(backend)
            except Exception:
                report = DiskUsageReport(pkgs_cache=0, envs=0, total=0)

            def finish() -> None:
                self._disk_usage = report
                total_str = self._format_bytes(report.total)
                self._disk_label.set_label(f"Total Conda disk usage: {total_str}")
                self._clean_button.set_sensitive(report.total > 0)

            GLib.idle_add(finish)

        threading.Thread(target=worker, daemon=True).start()

    def _load_channels_async(self) -> None:
        if self._backend is None:
            return

        backend = self._backend

        def worker() -> None:
            try:
                channels = get_channel_priorities(backend)
            except Exception:
                channels = []

            def finish() -> None:
                self._channels = channels
                if self._channels:
                    self._channel_label.set_label(
                        "Channel priority: " + ", ".join(self._channels)
                    )
                else:
                    self._channel_label.set_label("")

            GLib.idle_add(finish)

        threading.Thread(target=worker, daemon=True).start()

    # --- Global operation status helper ---

    def _set_operation_status(self, message: Optional[str]) -> None:
        """Show or hide the footer spinner + status text."""
        if self._status_spinner is None or self._status_label is None:
            return

        if message:
            self._status_label.set_label(message)
            self._status_label.set_visible(True)
            self._status_spinner.start()
            self._status_spinner.set_visible(True)
        else:
            self._status_label.set_visible(False)
            self._status_label.set_text("")
            self._status_spinner.stop()
            self._status_spinner.set_visible(False)

    # --- Terms of Service guidance ---

    def _show_tos_dialog(self, action: str, raw_error: str) -> None:
        """Show a friendly explanation for CondaToSNonInteractiveError."""
        # Prefer to show the exact backend executable path so the user runs
        # the commands against the same Conda that CondaNest is using.
        exe = None
        if self._backend is not None:
            exe = self._backend.executable
        exe_display = exe or "conda"

        msg = (
            "Conda reported that it cannot {} because the Anaconda defaults "
            "channels require accepting their Terms of Service first.\n\n"
            "To accept these terms for the defaults channels, run the following "
            "commands once in a terminal (they use the same Conda executable "
            "that CondaNest is using), then retry this action:"
        ).format(action)

        dialog = Adw.MessageDialog.new(
            self,
            "Accept Anaconda Terms of Service in a terminal",
            msg,
        )
        dialog.add_response("close", "Close")
        dialog.set_close_response("close")

        # Create a selectable text view for the exact commands
        commands_text = (
            f"{exe_display} tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main\n"
            f"{exe_display} tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r"
        )
        
        commands_view = Gtk.TextView()
        commands_view.set_editable(False)
        # Keep cursor visible so selection feels natural.
        commands_view.set_cursor_visible(True)
        commands_view.get_buffer().set_text(commands_text)
        commands_view.set_monospace(True)
        commands_view.set_wrap_mode(Gtk.WrapMode.NONE)
        
        # Add a label above the commands (make it visually prominent).
        commands_label = Gtk.Label(
            label=(
                "Commands to copy:\n"
                "1) Open a new terminal\n"
                "2) Copy the following commands and run them"
            )
        )
        commands_label.set_xalign(0.0)
        commands_label.add_css_class("title-4")
        
        # Wrap in scrolled window
        commands_scrolled = Gtk.ScrolledWindow()
        commands_scrolled.set_child(commands_view)
        # Make the commands area larger so it is easier to see and select.
        commands_scrolled.set_min_content_height(120)
        commands_scrolled.set_max_content_height(200)
        
        # Create a box to hold label and commands
        commands_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        commands_box.append(commands_label)
        commands_box.append(commands_scrolled)
        commands_box.set_margin_top(12)
        commands_box.set_margin_bottom(12)
        commands_box.set_margin_start(12)
        commands_box.set_margin_end(12)

        # Create a preferences group to hold everything
        main_group = Adw.PreferencesGroup()
        main_group.add(commands_box)

        # Show the raw Conda error in a collapsible expander for advanced users.
        if raw_error:
            expander = Adw.ExpanderRow()
            expander.set_title("Show Conda error details")
            error_view = Gtk.TextView()
            error_view.set_editable(False)
            error_view.set_cursor_visible(True)
            error_view.get_buffer().set_text(raw_error)
            error_view.set_monospace(True)
            error_scrolled = Gtk.ScrolledWindow()
            error_scrolled.set_child(error_view)
            # Make the error details box smaller so it takes less space.
            error_scrolled.set_min_content_height(60)
            expander.set_child(error_scrolled)
            main_group.add(expander)

        dialog.set_extra_child(main_group)
        dialog.present()

    def _on_clean_global_clicked(self, _button: Gtk.Button) -> None:
        self._show_clean_dialog()

    def _show_clean_dialog(self) -> None:
        if self._backend is None:
            return

        est = self._format_bytes(self._disk_usage.total) if self._disk_usage else "unknown"
        dialog = Adw.MessageDialog.new(
            self,
            "Clean Conda caches and packages?",
            f"This will run 'conda clean --all' using the detected backend.\n"
            f"Estimated total Conda usage: {est}.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("clean", "Clean up")
        dialog.set_response_appearance("clean", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")

        def on_response(_dlg: Adw.MessageDialog, response: str) -> None:
            if response != "clean":
                return
            self._run_clean_flow()

        dialog.connect("response", on_response)
        dialog.present()

    def _run_clean_flow(self) -> None:
        if self._backend is None:
            return

        backend = self._backend
        self.set_sensitive(False)
        self._set_operation_status("Cleaning Conda caches and package data…")

        def worker() -> None:
            error_msg: Optional[str] = None
            try:
                run_global_clean(backend)
            except EnvOperationError as exc:
                error_msg = str(exc)

            def finish() -> None:
                self.set_sensitive(True)
                self._set_operation_status(None)
                if error_msg:
                    dialog = Adw.MessageDialog.new(
                        self,
                        "Clean failed",
                        error_msg,
                    )
                    dialog.add_response("close", "Close")
                    dialog.set_close_response("close")
                    dialog.present()
                else:
                    # Refresh disk usage after successful clean.
                    self._load_disk_usage_async()

            GLib.idle_add(finish)

        threading.Thread(target=worker, daemon=True).start()

    # --- Create environment ---

    def _on_create_env_clicked(self, _button: Gtk.Button) -> None:
        if self._backend is None:
            return

        dialog = Adw.MessageDialog.new(
            self,
            "Create new environment",
            "Create a new environment by specifying a name and optional Python version,\n"
            "or by pointing to an existing environment.yml file.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("create", "Create")
        dialog.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("create")
        dialog.set_close_response("cancel")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        name_entry = Gtk.Entry()
        name_entry.set_placeholder_text("env name (required unless using file name)")

        version_entry = Gtk.Entry()
        version_entry.set_placeholder_text("python version (optional, e.g. 3.11)")

        # Horizontal box: environment.yml path entry + file chooser button.
        file_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        file_entry = Gtk.Entry()
        file_entry.set_hexpand(True)
        file_entry.set_placeholder_text("environment.yml path (optional)")

        file_button = Gtk.Button.new_from_icon_name("document-open-symbolic")
        file_button.add_css_class("flat")
        file_button.set_tooltip_text("Browse for environment.yml")

        def on_file_button_clicked(_btn: Gtk.Button) -> None:
            chooser = Gtk.FileChooserNative.new(
                title="Select environment.yml",
                parent=self,
                action=Gtk.FileChooserAction.OPEN,
                accept_label="Select",
                cancel_label="Cancel",
            )

            # Optional filter for YAML files.
            yaml_filter = Gtk.FileFilter()
            yaml_filter.set_name("YAML files")
            yaml_filter.add_pattern("*.yml")
            yaml_filter.add_pattern("*.yaml")
            chooser.add_filter(yaml_filter)

            def on_chooser_response(dlg: Gtk.FileChooserNative, response: int) -> None:
                if response != Gtk.ResponseType.ACCEPT:
                    dlg.destroy()
                    return
                file_obj = dlg.get_file()
                dlg.destroy()
                if file_obj is None:
                    return
                path_str = file_obj.get_path()
                if path_str:
                    file_entry.set_text(path_str)

            chooser.connect("response", on_chooser_response)
            chooser.show()

        file_button.connect("clicked", on_file_button_clicked)

        file_row.append(file_entry)
        file_row.append(file_button)

        box.append(name_entry)
        box.append(version_entry)
        box.append(file_row)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(6)
        box.set_margin_end(6)
        dialog.set_extra_child(box)

        def on_response(_dlg: Adw.MessageDialog, response: str) -> None:
            if response != "create":
                return
            name = name_entry.get_text().strip()
            python_version = version_entry.get_text().strip() or None
            yaml_path = file_entry.get_text().strip() or None

            if yaml_path:
                # Allow name to be empty here; Conda will use the name from file.
                self._run_create_from_file_flow(name or None, Path(yaml_path))
            else:
                if not name:
                    return
                self._run_create_flow(name, python_version)

        dialog.connect("response", on_response)
        dialog.present()

    def _run_create_flow(self, name: str, python_version: Optional[str]) -> None:
        if self._backend is None:
            return

        backend = self._backend
        self.set_sensitive(False)
        self._set_operation_status(f"Creating environment '{name}'…")

        def worker() -> None:
            error_msg: Optional[str] = None
            tos_error: Optional[str] = None
            try:
                create_environment(backend, name, python_version)
            except TermsOfServiceError as exc:
                tos_error = str(exc)
            except EnvOperationError as exc:
                error_msg = str(exc)

            def finish() -> None:
                self.set_sensitive(True)
                self._set_operation_status(None)
                if tos_error:
                    self._show_tos_dialog("create this environment", tos_error)
                elif error_msg:
                    dialog = Adw.MessageDialog.new(
                        self,
                        "Create failed",
                        error_msg,
                    )
                    dialog.add_response("close", "Close")
                    dialog.set_close_response("close")
                    dialog.present()
                else:
                    self._load_envs_async()
                    self._select_env_by_name(name)

            GLib.idle_add(finish)

        threading.Thread(target=worker, daemon=True).start()

    def _run_create_from_file_flow(self, name: Optional[str], yaml_path: Path) -> None:
        if self._backend is None:
            return

        backend = self._backend
        self.set_sensitive(False)
        label_name = name or yaml_path.stem
        self._set_operation_status(f"Creating environment '{label_name}' from file…")

        def worker() -> None:
            error_msg: Optional[str] = None
            tos_error: Optional[str] = None
            try:
                create_environment_from_file(backend, yaml_path, name=name)
            except TermsOfServiceError as exc:
                tos_error = str(exc)
            except EnvOperationError as exc:
                error_msg = str(exc)

            def finish() -> None:
                self.set_sensitive(True)
                self._set_operation_status(None)
                if tos_error:
                    self._show_tos_dialog("create this environment from file", tos_error)
                elif error_msg:
                    dialog = Adw.MessageDialog.new(
                        self,
                        "Create from file failed",
                        error_msg,
                    )
                    dialog.add_response("close", "Close")
                    dialog.set_close_response("close")
                    dialog.present()
                else:
                    self._load_envs_async()
                    if name:
                        self._select_env_by_name(name)

            GLib.idle_add(finish)

        threading.Thread(target=worker, daemon=True).start()

    # --- Package install / update ---

    def _on_install_package_clicked(self, _button: Gtk.Button) -> None:
        if self._backend is None or self._current_env is None:
            return

        dialog = Adw.MessageDialog.new(
            self,
            "Install package",
            "Enter a Conda package spec to install into this environment\n"
            "(for example: numpy or numpy=1.26).",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("install", "Install")
        dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("install")
        dialog.set_close_response("cancel")

        entry = Gtk.Entry()
        entry.set_placeholder_text("package name[=version]")
        entry.set_activates_default(True)
        entry.set_margin_top(6)
        entry.set_margin_bottom(6)
        entry.set_margin_start(6)
        entry.set_margin_end(6)
        dialog.set_extra_child(entry)

        def on_response(_dlg: Adw.MessageDialog, response: str) -> None:
            if response != "install":
                return
            spec = entry.get_text().strip()
            if not spec:
                return
            self._run_install_packages_flow([spec])

        dialog.connect("response", on_response)
        dialog.present()

    def _run_install_packages_flow(self, specs: list[str]) -> None:
        if self._backend is None or self._current_env is None or not specs:
            return

        backend = self._backend
        env = self._current_env
        self.set_sensitive(False)
        pretty = ", ".join(specs)
        self._set_operation_status(f"Installing {pretty} into '{env.name}'…")

        def worker() -> None:
            error_msg: Optional[str] = None
            tos_error: Optional[str] = None
            try:
                install_packages(backend, env, specs)
            except TermsOfServiceError as exc:
                tos_error = str(exc)
            except EnvOperationError as exc:
                error_msg = str(exc)

            def finish() -> None:
                self.set_sensitive(True)
                self._set_operation_status(None)
                if tos_error:
                    self._show_tos_dialog("install packages into this environment", tos_error)
                elif error_msg:
                    dialog = Adw.MessageDialog.new(
                        self,
                        "Install failed",
                        error_msg,
                    )
                    dialog.add_response("close", "Close")
                    dialog.set_close_response("close")
                    dialog.present()
                else:
                    self._load_packages_async(env)

            GLib.idle_add(finish)

        threading.Thread(target=worker, daemon=True).start()

    def _on_update_all_clicked(self, _button: Gtk.Button) -> None:
        if self._backend is None or self._current_env is None:
            return

        env = self._current_env
        dialog = Adw.MessageDialog.new(
            self,
            "Update all packages?",
            f"This will run 'conda update --all' on environment '{env.name}'.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("update", "Update all")
        dialog.set_response_appearance("update", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")

        def on_response(_dlg: Adw.MessageDialog, response: str) -> None:
            if response != "update":
                return
            self._run_update_all_flow()

        dialog.connect("response", on_response)
        dialog.present()

    def _run_update_all_flow(self) -> None:
        if self._backend is None or self._current_env is None:
            return

        backend = self._backend
        env = self._current_env
        self.set_sensitive(False)
        self._set_operation_status(f"Updating all packages in '{env.name}'…")

        def worker() -> None:
            error_msg: Optional[str] = None
            tos_error: Optional[str] = None
            try:
                update_all_packages(backend, env)
            except TermsOfServiceError as exc:
                tos_error = str(exc)
            except EnvOperationError as exc:
                error_msg = str(exc)

            def finish() -> None:
                self.set_sensitive(True)
                self._set_operation_status(None)
                if tos_error:
                    self._show_tos_dialog("update all packages in this environment", tos_error)
                elif error_msg:
                    dialog = Adw.MessageDialog.new(
                        self,
                        "Update all failed",
                        error_msg,
                    )
                    dialog.add_response("close", "Close")
                    dialog.set_close_response("close")
                    dialog.present()
                else:
                    self._load_packages_async(env)

            GLib.idle_add(finish)

        threading.Thread(target=worker, daemon=True).start()

    # --- Bulk export / create from folder ---

    def _on_bulk_actions_clicked(self, _button: Gtk.Button) -> None:
        # Compact dialog offering bulk tools and About link.
        dialog = Adw.MessageDialog.new(
            self,
            "Bulk environment tools",
            "What would you like to do?",
        )
        dialog.add_response("channels", "Manage channels…")
        dialog.add_response("export", "Export all envs to YAML…")
        dialog.add_response("create", "Create envs from folder…")
        dialog.add_response("about", "About CondaNest…")
        dialog.add_response("cancel", "Cancel")
        dialog.set_close_response("cancel")

        def on_response(_dlg: Adw.MessageDialog, response: str) -> None:
            if response == "create":
                self._on_create_all_clicked(Gtk.Button())
            elif response == "export":
                self._on_export_all_clicked(Gtk.Button())
            elif response == "channels":
                self._on_manage_channels_clicked()
            elif response == "about":
                Gio.AppInfo.launch_default_for_uri(
                    "https://github.com/aradar46/condanest", None
                )

        dialog.connect("response", on_response)
        dialog.present()

    def _on_manage_channels_clicked(self) -> None:
        if self._backend is None:
            return

        backend = self._backend

        # Start from the channels we already loaded (if any). If we don't have
        # them yet, fall back to a fresh backend query.
        channels = list(getattr(self, "_channels", []))
        if not channels:
            try:
                channels = get_channel_priorities(backend)
            except Exception:  # noqa: BLE001
                channels = []

        # Also fetch current channel_priority mode.
        mode = get_channel_priority_mode(backend) or ""
        strict_enabled = mode.strip().lower() == "strict"

        dialog = Adw.MessageDialog.new(
            self,
            "Manage Conda channels",
            "Adjust global Conda channel order and priority.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("apply", "Apply")
        dialog.set_close_response("cancel")

        channels_order: list[str] = list(channels)

        group = Adw.PreferencesGroup()
        group.set_margin_top(12)
        group.set_margin_bottom(12)
        group.set_margin_start(12)
        group.set_margin_end(12)

        # Strict mode toggle at the top, with shorter copy and more breathing room.
        strict_row = Adw.ActionRow()
        strict_row.set_title("Strict channel priority")
        strict_check = Gtk.CheckButton()
        strict_check.set_active(strict_enabled)
        strict_row.add_suffix(strict_check)
        strict_row.set_activatable_widget(strict_check)
        strict_row.set_subtitle("Prefer packages from the highest priority channel first.")

        # Label + list of channels (top = highest priority).
        channels_label = Gtk.Label(label="Channels (top = highest priority)")
        channels_label.set_xalign(0.0)
        channels_label.add_css_class("title-4")
        channels_label.set_margin_top(12)

        # Add channel entry row.
        add_channel_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        add_channel_entry = Gtk.Entry()
        add_channel_entry.set_placeholder_text("Enter channel name or URL (e.g., conda-forge)")
        add_channel_entry.set_hexpand(True)

        add_channel_btn = Gtk.Button.new_with_label("Add")
        add_channel_btn.add_css_class("suggested-action")

        def on_add_channel(_btn: Gtk.Button) -> None:
            text = add_channel_entry.get_text().strip()
            if not text:
                return
            if text not in channels_order:
                channels_order.append(text)
                add_channel_entry.set_text("")
                rebuild_rows()

        add_channel_btn.connect("clicked", on_add_channel)
        add_channel_entry.connect("activate", on_add_channel)

        add_channel_row.append(add_channel_entry)
        add_channel_row.append(add_channel_btn)

        list_box = Gtk.ListBox()
        list_box.add_css_class("boxed-list")
        list_box.set_margin_top(6)

        warning_label = Gtk.Label()
        warning_label.add_css_class("dim-label")
        warning_label.set_xalign(0.0)
        warning_label.set_wrap(True)
        warning_label.set_margin_top(6)

        def rebuild_rows() -> None:
            # Clear existing rows.
            child = list_box.get_first_child()
            while child is not None:
                next_child = child.get_next_sibling()
                list_box.remove(child)
                child = next_child

            for idx, ch in enumerate(channels_order):
                row = Adw.ActionRow(title=ch)

                # Delete button for each channel.
                delete_btn = Gtk.Button.new_from_icon_name("user-trash-symbolic")
                delete_btn.add_css_class("flat")
                delete_btn.set_tooltip_text("Remove this channel")

                def on_delete(_btn: Gtk.Button, i: int = idx) -> None:
                    channels_order.pop(i)
                    rebuild_rows()

                delete_btn.connect("clicked", on_delete)
                row.add_suffix(delete_btn)

                # Only show move buttons if there is more than one channel.
                if len(channels_order) > 1:
                    up_btn = Gtk.Button.new_from_icon_name("go-up-symbolic")
                    up_btn.add_css_class("flat")
                    up_btn.set_tooltip_text("Move up")

                    def on_up(_btn: Gtk.Button, i: int = idx) -> None:
                        if i <= 0:
                            return
                        channels_order[i - 1], channels_order[i] = (
                            channels_order[i],
                            channels_order[i - 1],
                        )
                        rebuild_rows()

                    up_btn.connect("clicked", on_up)
                    row.add_suffix(up_btn)

                    down_btn = Gtk.Button.new_from_icon_name("go-down-symbolic")
                    down_btn.add_css_class("flat")
                    down_btn.set_tooltip_text("Move down")

                    def on_down(_btn: Gtk.Button, i: int = idx) -> None:
                        if i >= len(channels_order) - 1:
                            return
                        channels_order[i + 1], channels_order[i] = (
                            channels_order[i],
                            channels_order[i + 1],
                        )
                        rebuild_rows()

                    down_btn.connect("clicked", on_down)
                    row.add_suffix(down_btn)

                list_box.append(row)

            # Update warning about defaults being at the top.
            if channels_order:
                top = channels_order[0].lower()
                if "defaults" in top or "repo.anaconda.com/pkgs" in top:
                    warning_label.set_label(
                        "Using 'defaults' as the highest priority channel may "
                        "require a commercial license for some use-cases."
                    )
                    warning_label.set_visible(True)
                else:
                    warning_label.set_visible(False)
            else:
                warning_label.set_visible(False)

        rebuild_rows()

        group.add(strict_row)
        group.add(channels_label)
        group.add(add_channel_row)
        group.add(list_box)
        group.add(warning_label)

        dialog.set_extra_child(group)

        def on_response(_dlg: Adw.MessageDialog, response: str) -> None:
            if response != "apply":
                return
            # Apply new order and strict mode selection.
            try:
                set_channel_priorities(backend, channels_order)
                new_mode = "strict" if strict_check.get_active() else "flexible"
                set_channel_priority_mode(backend, new_mode)
            except EnvOperationError as exc:
                err_dialog = Adw.MessageDialog.new(
                    self,
                    "Failed to update channels",
                    str(exc),
                )
                err_dialog.add_response("close", "Close")
                err_dialog.set_close_response("close")
                err_dialog.present()
                return

            # Refresh cached channels and label in the main UI.
            self._load_channels_async()

        dialog.connect("response", on_response)
        dialog.present()

    def _on_export_all_clicked(self, _button: Gtk.Button) -> None:
        if not self._envs or self._backend is None:
            return

        chooser = Gtk.FileChooserNative.new(
            title="Select folder to export all environments",
            parent=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
            accept_label="Export",
            cancel_label="Cancel",
        )

        def on_response(dlg: Gtk.FileChooserNative, response: int) -> None:
            if response != Gtk.ResponseType.ACCEPT:
                dlg.destroy()
                return
            folder = dlg.get_file()
            dlg.destroy()
            if folder is None:
                return
            dest_dir = folder.get_path()
            if dest_dir:
                self._run_export_all_flow(Path(dest_dir))

        chooser.connect("response", on_response)
        chooser.show()

    def _run_export_all_flow(self, dest_dir: Path) -> None:
        if self._backend is None:
            return

        backend = self._backend
        envs = list(self._envs)
        self.set_sensitive(False)
        self._set_operation_status("Exporting all environments to YAML…")

        def worker() -> None:
            error_msg: Optional[str] = None
            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
                for env in envs:
                    dest = dest_dir / f"{env.name}.yml"
                    export_environment_yaml(backend, env, dest)
            except EnvOperationError as exc:
                error_msg = str(exc)

            def finish() -> None:
                self.set_sensitive(True)
                self._set_operation_status(None)
                if error_msg:
                    dialog = Adw.MessageDialog.new(
                        self,
                        "Export all failed",
                        error_msg,
                    )
                    dialog.add_response("close", "Close")
                    dialog.set_close_response("close")
                    dialog.present()
                else:
                    dialog = Adw.MessageDialog.new(
                        self,
                        "Export complete",
                        f"Exported {len(envs)} environments to:\n{dest_dir}",
                    )
                    dialog.add_response("close", "Close")
                    dialog.set_close_response("close")
                    dialog.present()

            GLib.idle_add(finish)

        threading.Thread(target=worker, daemon=True).start()

    def _on_create_all_clicked(self, _button: Gtk.Button) -> None:
        if self._backend is None:
            return

        chooser = Gtk.FileChooserNative.new(
            title="Select folder with environment YAML files",
            parent=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
            accept_label="Create",
            cancel_label="Cancel",
        )

        def on_response(dlg: Gtk.FileChooserNative, response: int) -> None:
            if response != Gtk.ResponseType.ACCEPT:
                dlg.destroy()
                return
            folder = dlg.get_file()
            dlg.destroy()
            if folder is None:
                return
            dir_path = folder.get_path()
            if dir_path:
                self._run_create_all_flow(Path(dir_path))

        chooser.connect("response", on_response)
        chooser.show()

    def _run_create_all_flow(self, dir_path: Path) -> None:
        if self._backend is None:
            return

        backend = self._backend
        # Collect all *.yml / *.yaml files.
        yaml_files = sorted(
            [p for p in dir_path.iterdir() if p.suffix in {".yml", ".yaml"}],
            key=lambda p: p.name,
        )
        if not yaml_files:
            dialog = Adw.MessageDialog.new(
                self,
                "No YAML files found",
                f"No .yml or .yaml files were found in:\n{dir_path}",
            )
            dialog.add_response("close", "Close")
            dialog.set_close_response("close")
            dialog.present()
            return

        self.set_sensitive(False)
        self._set_operation_status("Creating environments from folder…")

        def worker() -> None:
            error_msg: Optional[str] = None
            try:
                for yaml_path in yaml_files:
                    # Use file stem as default env name.
                    name = yaml_path.stem
                    create_environment_from_file(backend, yaml_path, name=name)
            except EnvOperationError as exc:
                error_msg = str(exc)

            def finish() -> None:
                self.set_sensitive(True)
                self._set_operation_status(None)
                if error_msg:
                    dialog = Adw.MessageDialog.new(
                        self,
                        "Create from folder failed",
                        error_msg,
                    )
                    dialog.add_response("close", "Close")
                    dialog.set_close_response("close")
                    dialog.present()
                else:
                    self._load_envs_async()
                    dialog = Adw.MessageDialog.new(
                        self,
                        "Create from folder complete",
                        f"Created environments from {len(yaml_files)} files in:\n{dir_path}",
                    )
                    dialog.add_response("close", "Close")
                    dialog.set_close_response("close")
                    dialog.present()

            GLib.idle_add(finish)

        threading.Thread(target=worker, daemon=True).start()


class CondaControlApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id="io.github.condanest")
        self.connect("activate", self.on_activate)

    def do_startup(self) -> None:  # type: ignore[override]
        Adw.Application.do_startup(self)

    def on_activate(self, _app: Gio.Application) -> None:
        win = self.props.active_window
        if not win:
            win = MainWindow(self)
        win.present()


def main(argv: Optional[list[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    app = CondaControlApplication()
    return app.run(argv)


if __name__ == "__main__":
    raise SystemExit(main())

