"""GTK 4 / libadwaita window for Simple Git Push."""
from __future__ import annotations

import os
import shutil
import sys
import threading
from pathlib import Path
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import (APP_ID, APP_NAME, COPYRIGHT, COPYRIGHT_HOLDER, COPYRIGHT_YEAR,  # noqa: E402
               REPO_URL, __version__, core)

CSS = """
.dropzone { border: 2px dashed alpha(currentColor, 0.35); border-radius: 12px; padding: 18px; }
.dropzone.drag { border-color: @accent_bg_color; background: alpha(@accent_bg_color, 0.12); }
textview.terminal, textview.terminal text { background-color: #1e1e1e; color: #d4d4d4; }
.terminal-frame { border-radius: 8px; }
"""


class AutoRow:
    """An entry row that is filled from the zip but can be edited or reset."""

    def __init__(self, title: str, browse: Optional[Callable[[], None]] = None):
        self.auto = ""
        self.row = Adw.EntryRow(title=title)
        self.badge = Gtk.Label(valign=Gtk.Align.CENTER)
        self.badge.add_css_class("caption")
        reset = Gtk.Button(icon_name="view-refresh-symbolic", valign=Gtk.Align.CENTER,
                           tooltip_text="Reset to the value from the zip")
        reset.add_css_class("flat")
        reset.connect("clicked", lambda *_: self.row.set_text(self.auto))
        if browse:
            b = Gtk.Button(icon_name="document-open-symbolic", valign=Gtk.Align.CENTER,
                           tooltip_text="Choose a file")
            b.add_css_class("flat")
            b.connect("clicked", lambda *_: browse())
            self.row.add_suffix(b)
        self.row.add_suffix(self.badge)
        self.row.add_suffix(reset)
        self.row.connect("changed", lambda *_: self._update_badge())
        self._update_badge()

    def _update_badge(self) -> None:
        edited = self.row.get_text() != self.auto
        self.badge.set_text("edited" if edited else "auto")
        if edited:
            self.badge.add_css_class("warning")
            self.badge.remove_css_class("dim-label")
        else:
            self.badge.add_css_class("dim-label")
            self.badge.remove_css_class("warning")

    def set_auto(self, value: str, force: bool = False) -> None:
        untouched = self.row.get_text() == self.auto
        self.auto = value
        if untouched or force:
            self.row.set_text(value)
        self._update_badge()

    def text(self) -> str:
        return self.row.get_text().strip()


class AboutDialog(Adw.Dialog):
    """About and License tabs, same layout as the author's File Library app."""

    def __init__(self) -> None:
        super().__init__(title=f"About {APP_NAME}", content_width=440)
        stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        stack.add_titled(self._about_page(), "about", "About")
        stack.add_titled(self._license_page(), "license", "License")
        switcher = Gtk.StackSwitcher(stack=stack, halign=Gtk.Align.CENTER,
                                     margin_top=12, margin_start=20, margin_end=20)
        close = Gtk.Button(label="Close", halign=Gtk.Align.END,
                           margin_top=6, margin_bottom=16, margin_end=16)
        close.connect("clicked", lambda *_: self.close())
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(switcher)
        body.append(stack)
        body.append(close)
        stack.set_vexpand(True)

        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title=f"About {APP_NAME}", subtitle=""))
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(body)
        self.set_child(view)

    @staticmethod
    def _about_page() -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, halign=Gtk.Align.CENTER,
                      margin_top=18, margin_bottom=12, margin_start=24, margin_end=24)
        icon = Gtk.Image(icon_name="simple-git-push", pixel_size=96, margin_bottom=8)
        name = Gtk.Label(label=APP_NAME)
        name.add_css_class("title-1")
        version = Gtk.Label(label=f"Version {__version__}")
        version.add_css_class("dim-label")
        desc = Gtk.Label(label="Unpack a zip or folder, commit, push and create a GitHub release from one window.",
                         wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=40, margin_top=10)
        link = Gtk.LinkButton(uri=REPO_URL, label=REPO_URL.replace("https://", ""), margin_top=6)
        copyright_ = Gtk.Label(label=f"Copyright {COPYRIGHT}", margin_top=14)
        lic = Gtk.Label(label="Released under the MIT License.\nThis program comes with absolutely no warranty.",
                        justify=Gtk.Justification.CENTER, margin_top=2)
        lic.add_css_class("dim-label")
        for w in (icon, name, version, desc, link, copyright_, lic):
            box.append(w)
        return box

    @staticmethod
    def _license_page() -> Gtk.Widget:
        text = Gtk.Label(label=core.license_text(COPYRIGHT_HOLDER, COPYRIGHT_YEAR), wrap=True, xalign=0,
                         selectable=True, margin_top=14, margin_bottom=8, margin_start=24, margin_end=24)
        text.add_css_class("caption")
        sw = Gtk.ScrolledWindow(vexpand=True, min_content_height=300, child=text)
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        return sw


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, preload: Optional[str] = None):
        super().__init__(application=app, title=APP_NAME)
        self.set_default_size(780, 940)
        self.set_icon_name("simple-git-push")
        self.info: Optional[core.ZipInfo] = None
        self.busy = False
        self._loading = True
        self.projects: list = []
        self.current = 0

        css = Gtk.CssProvider()
        if hasattr(css, "load_from_string"):
            css.load_from_string(CSS)
        else:
            css.load_from_data(CSS.encode())
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self._build_ui()
        self._load_settings()
        self.connect("close-request", self._on_close)

        for tool in ("git", "gh"):
            path = shutil.which(tool)
            self.log("info", f"{tool}: {path or 'not found'}")
        if preload:
            GLib.idle_add(self.load_source, preload)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        about = Gtk.Button(icon_name="help-about-symbolic", tooltip_text="About Simple Git Push")
        about.add_css_class("flat")
        about.connect("clicked", lambda *_: self.show_about())
        header.pack_end(about)
        self.spinner = Gtk.Spinner()
        header.pack_end(self.spinner)
        view.add_top_bar(header)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                       margin_top=12, margin_bottom=12, margin_start=16, margin_end=16)
        page.append(self._build_drop_zone())
        page.append(self._build_project())
        page.append(self._build_commit())
        page.append(self._build_release())

        clamp = Adw.Clamp(maximum_size=720, child=page)
        scrolled = Gtk.ScrolledWindow(vexpand=True, child=clamp)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        paned.set_start_child(scrolled)
        paned.set_end_child(self._build_terminal())
        paned.set_resize_start_child(True)
        paned.set_shrink_start_child(False)
        paned.set_resize_end_child(True)
        paned.set_shrink_end_child(False)
        paned.set_position(640)

        view.set_content(paned)
        self.toasts.set_child(view)
        self.set_content(self.toasts)

    def _build_drop_zone(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.add_css_class("dropzone")
        icon = Gtk.Image(icon_name="folder-download-symbolic", pixel_size=40)
        icon.add_css_class("dim-label")
        title = Gtk.Label(label="Drop a .zip or a folder here")
        title.add_css_class("title-4")
        self.zip_status = Gtk.Label(label="Nothing loaded yet", wrap=True, justify=Gtk.Justification.CENTER)
        self.zip_status.add_css_class("dim-label")
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER, margin_top=6)
        open_btn = Gtk.Button(label="Open file…", icon_name="document-open-symbolic",
                              tooltip_text="Open a zip file")
        open_btn.connect("clicked", lambda *_: self.choose_zip())
        folder_btn = Gtk.Button(label="Import from folder…", icon_name="folder-open-symbolic",
                                tooltip_text="Copy the files of a folder into the development folder")
        folder_btn.connect("clicked", lambda *_: self.choose_source_folder())
        self.unpack_btn = Gtk.Button(label="Unpack again")
        self.unpack_btn.connect("clicked", lambda *_: self.unpack_current())
        self.clear_btn = Gtk.Button(label="Clear", icon_name="edit-clear-all-symbolic",
                                    tooltip_text="Remove the loaded zip or folder and clear the commit and release fields")
        self.clear_btn.connect("clicked", lambda *_: self.clear_zip())
        buttons.append(open_btn)
        buttons.append(folder_btn)
        buttons.append(self.unpack_btn)
        buttons.append(self.clear_btn)
        for w in (icon, title, self.zip_status, buttons):
            box.append(w)

        target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        target.connect("drop", self._on_drop)
        target.connect("enter", self._on_drag_enter, box)
        target.connect("leave", lambda *_: box.remove_css_class("drag"))
        box.add_controller(target)
        return box

    def _build_project(self) -> Gtk.Widget:
        g = Adw.PreferencesGroup(title="Project")
        self.project_group = g
        self.project_row = Adw.ComboRow(title="Project", model=Gtk.StringList.new([]))
        add = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER,
                         tooltip_text="Add a new project")
        add.add_css_class("flat")
        add.connect("clicked", lambda *_: self.add_project())
        self.edit_btn = Gtk.Button(icon_name="document-edit-symbolic", valign=Gtk.Align.CENTER)
        self.edit_btn.add_css_class("flat")
        self.edit_btn.connect("clicked", lambda *_: self.toggle_edit())
        self.del_btn = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                                  tooltip_text="Delete this project")
        self.del_btn.add_css_class("flat")
        self.del_btn.connect("clicked", lambda *_: self.delete_project())
        self.project_row.add_suffix(add)
        self.project_row.add_suffix(self.edit_btn)
        self.project_row.add_suffix(self.del_btn)
        self.name_row = Adw.EntryRow(title="Project name")
        self.name_row.connect("changed", lambda *_: self._on_name_changed())
        self.dev_row = Adw.EntryRow(title="Development folder")
        pick = Gtk.Button(icon_name="folder-symbolic", valign=Gtk.Align.CENTER,
                          tooltip_text="Choose a folder")
        pick.add_css_class("flat")
        pick.connect("clicked", lambda *_: self.choose_folder())
        self.pick_btn = pick
        self.dev_row.add_suffix(pick)
        self.repo_row = Adw.EntryRow(title="Repository URL")
        g.add(self.project_row)
        g.add(self.name_row)
        g.add(self.dev_row)
        g.add(self.repo_row)
        self.set_editing(False)
        return g

    def set_editing(self, on: bool) -> None:
        """Project name, folder and repository are locked unless the pencil was pressed."""
        self.editing = on
        for row in (self.name_row, self.dev_row, self.repo_row):
            row.set_editable(on)
        self.pick_btn.set_sensitive(on)
        if on:
            self.edit_btn.set_icon_name("object-select-symbolic")
            self.edit_btn.set_tooltip_text("Save and lock this project")
            self.edit_btn.add_css_class("suggested-action")
            self.edit_btn.remove_css_class("flat")
            self.project_group.set_description("Editing. Press the check mark to save and lock the project.")
        else:
            self.edit_btn.set_icon_name("document-edit-symbolic")
            self.edit_btn.set_tooltip_text("Edit this project")
            self.edit_btn.add_css_class("flat")
            self.edit_btn.remove_css_class("suggested-action")
            self.project_group.set_description("Locked. Press the pencil to change the project.")

    def toggle_edit(self) -> None:
        if self.editing:
            self._capture()
            self.set_editing(False)
            self.toast("Project saved")
        else:
            self.set_editing(True)
            self.name_row.grab_focus()

    def _build_commit(self) -> Gtk.Widget:
        g = Adw.PreferencesGroup(title="Commit and push")
        self.type_row = Adw.ComboRow(title="Commit type",
                                     model=Gtk.StringList.new(list(core.COMMIT_TYPES)))
        self.msg_row = Adw.EntryRow(title="Commit message")
        self.push_row = Adw.SwitchRow(title="Push to origin after commit", active=True)
        self.nodeb_row = Adw.SwitchRow(
            title="Keep .deb files out of the commit",
            subtitle="Release assets are uploaded with the release instead",
            active=True)
        for w in (self.type_row, self.msg_row, self.push_row, self.nodeb_row):
            g.add(w)
        self.type_row.connect("notify::selected", self._on_type_changed)

        self.commit_btn = Gtk.Button(label="Commit and push", halign=Gtk.Align.END)
        self.commit_btn.add_css_class("suggested-action")
        self.commit_btn.connect("clicked", lambda *_: self.do_commit())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.append(g)
        box.append(self.commit_btn)
        return box

    def _build_release(self) -> Gtk.Widget:
        g = Adw.PreferencesGroup(title="GitHub release",
                                 description="Filled from the zip. Edit any field to override it.")
        self.tag_row = AutoRow("Tag")
        self.title_row = AutoRow("Title")
        self.notes_row = AutoRow("Notes file", browse=lambda: self.choose_file(self.notes_row.row))
        self.asset_row = AutoRow("Asset (.deb)", browse=lambda: self.choose_file(self.asset_row.row))
        for r in (self.tag_row, self.title_row, self.notes_row, self.asset_row):
            g.add(r.row)
        self.pre_row = Adw.SwitchRow(title="Mark as pre-release")
        self.over_row = Adw.SwitchRow(title="Overwrite if the release already exists",
                                      subtitle="Deletes the old release and its tag first")
        g.add(self.pre_row)
        g.add(self.over_row)
        self.release_btn = Gtk.Button(label="Create release", halign=Gtk.Align.END)
        self.release_btn.add_css_class("suggested-action")
        self.release_btn.connect("clicked", lambda *_: self.do_release())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.append(g)
        box.append(self.release_btn)
        return box

    def _build_terminal(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      margin_top=6, margin_bottom=12, margin_start=16, margin_end=16)
        bar = Gtk.Box(spacing=6)
        label = Gtk.Label(label="Output", xalign=0, hexpand=True)
        label.add_css_class("heading")
        copy = Gtk.Button(label="Copy all", icon_name="edit-copy-symbolic")
        copy.connect("clicked", lambda *_: self.copy_all())
        clear = Gtk.Button(label="Clear")
        clear.connect("clicked", lambda *_: self.buffer.set_text(""))
        for w in (label, copy, clear):
            bar.append(w)

        self.buffer = Gtk.TextBuffer()
        self.buffer.create_tag("cmd", foreground="#7ec699")
        self.buffer.create_tag("err", foreground="#f07178")
        self.buffer.create_tag("info", foreground="#8a8a8a")
        self.buffer.create_tag("out")
        self.endmark = self.buffer.create_mark("end", self.buffer.get_end_iter(), False)
        self.view = Gtk.TextView(buffer=self.buffer, editable=False, cursor_visible=True,
                                 monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                 left_margin=10, right_margin=10, top_margin=8, bottom_margin=8)
        self.view.add_css_class("terminal")
        sw = Gtk.ScrolledWindow(vexpand=True, child=self.view, min_content_height=140)
        sw.add_css_class("terminal-frame")
        box.append(bar)
        box.append(sw)
        return box

    # ------------------------------------------------------------ settings
    def _load_settings(self) -> None:
        self.projects, self.current = core.load_projects()
        self._loading = True
        self.project_row.set_model(Gtk.StringList.new([p["name"] for p in self.projects]))
        self.project_row.set_selected(self.current)
        self._loading = False
        self.project_row.connect("notify::selected", self._on_project_selected)
        self._show_project()

    def _show_project(self) -> None:
        """Copy the current project into the form."""
        p = self.projects[self.current]
        self._loading = True
        self.name_row.set_text(p["name"])
        self.dev_row.set_text(p["dev_folder"])
        self.repo_row.set_text(p["repo"])
        ctype = p["commit_type"]
        self.type_row.set_selected(core.COMMIT_TYPES.index(ctype) if ctype in core.COMMIT_TYPES else 0)
        self.push_row.set_active(p["push"])
        self.nodeb_row.set_active(p["exclude_deb"])
        self.del_btn.set_sensitive(len(self.projects) > 1)
        self._loading = False
        self._refresh_message()

    def _capture(self) -> None:
        """Copy the form into the current project and write it to disk."""
        if getattr(self, "_loading", True) or not self.projects:
            return
        p = self.projects[self.current]
        p.update(
            dev_folder=self.dev_row.get_text().strip(),
            repo=self.repo_row.get_text().strip(),
            commit_type=self._ctype(),
            push=self.push_row.get_active(),
            exclude_deb=self.nodeb_row.get_active(),
        )
        core.save_projects(self.projects, self.current)

    def _on_project_selected(self, *_args) -> None:
        if self._loading:
            return
        new = self.project_row.get_selected()
        if new == Gtk.INVALID_LIST_POSITION or new == self.current:
            return
        self._capture()
        self.set_editing(False)
        self.current = new
        core.save_projects(self.projects, self.current)
        self._show_project()
        self.log("info", f"Project: {self.projects[self.current]['name']}")

    def _on_name_changed(self) -> None:
        if self._loading or not self.projects:
            return
        name = self.name_row.get_text().strip()
        if not name:
            return
        name = core.unique_name(name, self.projects, ignore=self.current)
        self.projects[self.current]["name"] = name
        self._loading = True
        self.project_row.get_model().splice(self.current, 1, [name])
        self.project_row.set_selected(self.current)
        self._loading = False
        core.save_projects(self.projects, self.current)

    def add_project(self) -> None:
        self._capture()
        p = core.new_project(dev_folder="~/Devel/new-project", repo="https://github.com/owner/repo",
                             name=core.unique_name("new-project", self.projects))
        self.projects.append(p)
        self.current = len(self.projects) - 1
        self._loading = True
        self.project_row.get_model().append(p["name"])
        self.project_row.set_selected(self.current)
        self._loading = False
        core.save_projects(self.projects, self.current)
        self._show_project()
        self.set_editing(True)
        self.name_row.grab_focus()

    def delete_project(self) -> None:
        if len(self.projects) < 2:
            self.toast("Keep at least one project")
            return
        name = self.projects[self.current]["name"]
        dialog = Adw.AlertDialog(
            heading=f"Delete “{name}”?",
            body="This only removes the saved settings. Your files and the repository stay untouched.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def answered(_d, response):
            if response != "delete":
                return
            del self.projects[self.current]
            self.current = max(0, self.current - 1)
            self._loading = True
            self.project_row.set_model(Gtk.StringList.new([p["name"] for p in self.projects]))
            self.project_row.set_selected(self.current)
            self._loading = False
            core.save_projects(self.projects, self.current)
            self.set_editing(False)
            self._show_project()
            self.log("info", f"Deleted project {name}")
        dialog.connect("response", answered)
        dialog.present(self)

    def _on_close(self, *_args) -> bool:
        self._capture()
        return False

    # ----------------------------------------------------------- messages
    def _ctype(self) -> str:
        return core.COMMIT_TYPES[self.type_row.get_selected()]

    def _on_type_changed(self, *_args) -> None:
        if self._loading:
            self._refresh_message()
            return
        custom = self._ctype() == "custom"
        self.msg_row.set_editable(custom)
        if custom:
            self.msg_row.set_text("")
            self.msg_row.grab_focus()
        else:
            self._refresh_message()

    def _refresh_message(self) -> None:
        ctype = self._ctype()
        self.msg_row.set_editable(ctype == "custom")
        if ctype == "custom":
            return
        auto = self.info.auto_message if self.info else ""
        self.msg_row.set_text(core.build_message(ctype, auto_text=auto))

    # --------------------------------------------------------------- log
    def log(self, kind: str, text: str) -> None:
        GLib.idle_add(self._append, kind, text)

    def _append(self, kind: str, text: str) -> bool:
        self.buffer.insert_with_tags_by_name(self.buffer.get_end_iter(), text + "\n", kind)
        self.view.scroll_to_mark(self.endmark, 0.0, True, 0.0, 1.0)
        return False

    def copy_all(self) -> None:
        start, end = self.buffer.get_bounds()
        self.get_clipboard().set(self.buffer.get_text(start, end, False))
        self.toast("Output copied")

    def toast(self, text: str) -> None:
        self.toasts.add_toast(Adw.Toast.new(text))

    # ------------------------------------------------------ file choosers
    def choose_zip(self) -> None:
        dialog = Gtk.FileDialog(title="Open zip file")
        flt = Gtk.FileFilter(name="ZIP archives")
        flt.add_pattern("*.zip")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(flt)
        dialog.set_filters(filters)

        def done(dlg, res):
            try:
                f = dlg.open_finish(res)
            except GLib.Error:
                return
            self.load_source(f.get_path())
        dialog.open(self, None, done)

    def choose_source_folder(self) -> None:
        dialog = Gtk.FileDialog(title="Import from folder")

        def done(dlg, res):
            try:
                f = dlg.select_folder_finish(res)
            except GLib.Error:
                return
            self.load_source(f.get_path())
        dialog.select_folder(self, None, done)

    def choose_folder(self) -> None:
        dialog = Gtk.FileDialog(title="Choose the development folder")

        def done(dlg, res):
            try:
                f = dlg.select_folder_finish(res)
            except GLib.Error:
                return
            self.dev_row.set_text(self._tilde(f.get_path()))
        dialog.select_folder(self, None, done)

    def choose_file(self, row: Adw.EntryRow) -> None:
        dialog = Gtk.FileDialog(title="Choose a file")
        try:
            dialog.set_initial_folder(Gio.File.new_for_path(str(core.expand(self.dev_row.get_text()))))
        except Exception:
            pass

        def done(dlg, res):
            try:
                f = dlg.open_finish(res)
            except GLib.Error:
                return
            p = Path(f.get_path())
            try:
                p = p.relative_to(core.expand(self.dev_row.get_text()))
            except ValueError:
                pass
            row.set_text(str(p))
        dialog.open(self, None, done)

    @staticmethod
    def _tilde(path: str) -> str:
        home = str(Path.home())
        return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path

    # ------------------------------------------------------------ drag/drop
    def _on_drag_enter(self, _t, _x, _y, box) -> Gdk.DragAction:
        box.add_css_class("drag")
        return Gdk.DragAction.COPY

    def _on_drop(self, _t, value, _x, _y) -> bool:
        for f in value.get_files():
            path = f.get_path()
            if path and (os.path.isdir(path) or path.lower().endswith(".zip")):
                self.load_source(path)
                return True
        self.toast("Drop a .zip file or a folder")
        return False

    # ----------------------------------------------------- zip or folder
    def load_source(self, path: str) -> bool:
        try:
            info = core.analyze_source(path)
        except core.ZipError as exc:
            self.log("err", str(exc))
            self.toast("Couldn't read that zip or folder")
            return False
        kind = info.source_label
        name = Path(path).name or path
        self.info = info
        self._refresh_message()
        self.log("info", f"Loaded {kind} {name}: {info.file_count} files, "
                         f"version {info.version or 'unknown'}")
        if info.stripped_prefix and kind == "folder":
            self.log("info", f"Using the folder inside: {info.stripped_prefix}")
        self.tag_row.set_auto(info.tag, force=True)
        self.title_row.set_auto(info.title, force=True)
        self.notes_row.set_auto(info.notes_file or "", force=True)
        self.asset_row.set_auto(info.asset or "", force=True)
        if len(info.deb_files) > 1:
            self.log("info", "Several .deb files found: " + ", ".join(info.deb_files))
        if not info.version:
            self.log("err", f"No version found in the {kind}. Enter the tag in the release section.")
        if self._ctype() == "auto-generated":
            self.log("info", f"Auto-generated commit message from {info.message_source}: {info.auto_message}")
        first = self._tilde(str(Path(path))) if kind == "folder" else name
        summary = f"{first}\n{info.file_count} files"
        if info.version:
            summary += f" · version {info.version}"
        if kind == "folder" and info.root is not None and (info.root / ".git").exists():
            summary += " · .git skipped"
        self.zip_status.set_text(summary)
        self.unpack_btn.set_label("Copy again" if kind == "folder" else "Unpack again")
        self.unpack_current()
        return False

    def clear_zip(self) -> None:
        """Forget the loaded zip or folder and empty the commit and release fields. Files on disk stay as they are."""
        self.info = None
        self.zip_status.set_text("Nothing loaded yet")
        self.unpack_btn.set_label("Unpack again")
        if self._ctype() == "custom":
            self.msg_row.set_text("")
        else:
            self._refresh_message()
        for row in (self.tag_row, self.title_row, self.notes_row, self.asset_row):
            row.set_auto("", force=True)
        self.pre_row.set_active(False)
        self.over_row.set_active(False)
        self.log("info", "Cleared the loaded zip or folder and the commit and release fields.")

    def show_about(self) -> None:
        AboutDialog().present(self)

    def unpack_current(self) -> None:
        self._capture()
        if self.info is None:
            self.toast("Load a zip or folder first")
            return
        dev_text = self.dev_row.get_text().strip()
        if not dev_text:
            self.toast("Set the development folder first")
            return
        dev = core.expand(dev_text)
        if core.same_location(self.info, dev):
            self.log("info", "That's already your development folder. Nothing to copy.")
            self.toast("That's already your development folder")
            return
        if core.inside_source(self.info, dev):
            self.log("err", "The development folder is inside the folder you are importing. "
                            "Choose a different folder.")
            self.toast("The development folder is inside the import folder")
            return
        try:
            conflicts, same = core.plan_unpack(self.info, dev)
        except OSError as exc:
            self.log("err", f"Couldn't check {dev}: {exc}")
            return
        if not conflicts:
            self._start_unpack(dev, True)
            return
        shown = "\n".join(f"• {c}" for c in conflicts[:12])
        more = f"\n…and {len(conflicts) - 12} more" if len(conflicts) > 12 else ""
        extra = f"\n\n{same} identical file(s) will be left alone." if same else ""
        dialog = Adw.AlertDialog(
            heading="Replace existing files?",
            body=f"{len(conflicts)} file(s) in {dev} already exist and differ:\n\n{shown}{more}{extra}")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("skip", "Keep existing")
        dialog.add_response("overwrite", "Replace")
        dialog.set_response_appearance("overwrite", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def answered(_d, response):
            if response in ("overwrite", "skip"):
                self._start_unpack(dev, response == "overwrite")
            else:
                self.log("info", "Import cancelled.")
        dialog.connect("response", answered)
        dialog.present(self)

    def _start_unpack(self, dev: Path, overwrite: bool) -> None:
        info = self.info

        def work(runner: core.Runner) -> bool:
            copying = info.kind == "folder"
            self.log("info", f"{'Copying' if copying else 'Unpacking'} {info.file_count} files into {dev}")
            written, skipped = core.unpack(info, dev, overwrite, self.log)
            self.log("info", f"{'Copied' if copying else 'Unpacked'}: {written} written, "
                             f"{skipped} left as they were.")
            return True
        self._run_bg(work)

    # ----------------------------------------------------------- actions
    def do_commit(self) -> None:
        self._capture()
        dev_text, repo = self.dev_row.get_text().strip(), self.repo_row.get_text().strip()
        if not dev_text or not repo:
            self.toast("Enter the development folder and repository first")
            return
        if not self.msg_row.get_text().strip():
            self.toast("Load a zip or folder first, or enter a commit message"
                       if self._ctype() == "auto-generated" else "Enter a commit message first")
            return
        message = self.msg_row.get_text().strip()
        push, exclude = self.push_row.get_active(), self.nodeb_row.get_active()
        dev = core.expand(dev_text)
        self._run_bg(lambda r: core.commit_and_push(r, dev, repo, message, push, exclude))

    def do_release(self) -> None:
        self._capture()
        dev_text = self.dev_row.get_text().strip()
        if not dev_text:
            self.toast("Enter the development folder first")
            return
        args = dict(
            dev=core.expand(dev_text), repo=self.repo_row.get_text().strip(),
            tag=self.tag_row.text(), title=self.title_row.text(),
            notes_file=self.notes_row.text(), asset=self.asset_row.text(),
            prerelease=self.pre_row.get_active(), overwrite=self.over_row.get_active())
        self._run_bg(lambda r: core.create_release(r, **args))

    # ------------------------------------------------------- background
    def _run_bg(self, work: Callable[[core.Runner], bool]) -> None:
        if self.busy:
            self.toast("Another task is still running")
            return
        self._set_busy(True)

        def target() -> None:
            ok = False
            try:
                ok = bool(work(core.Runner(self.log)))
            except Exception as exc:  # shown in the output pane
                self.log("err", f"{type(exc).__name__}: {exc}")
            GLib.idle_add(self._finished, ok)
        threading.Thread(target=target, daemon=True).start()

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        for b in (self.commit_btn, self.release_btn, self.unpack_btn, self.clear_btn):
            b.set_sensitive(not busy)
        self.spinner.set_spinning(busy)

    def _finished(self, ok: bool) -> bool:
        self._set_busy(False)
        self.toast("Done" if ok else "Something went wrong. See the output below")
        return False


class App(Adw.Application):
    def __init__(self, preload: Optional[str] = None):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.preload = preload

    def do_activate(self) -> None:
        win = self.props.active_window or MainWindow(self, self.preload)
        win.present()


def main() -> int:
    preload = next((a for a in sys.argv[1:]
                    if (a.lower().endswith(".zip") and os.path.isfile(a)) or os.path.isdir(a)), None)
    GLib.set_application_name(APP_NAME)
    return App(preload).run([sys.argv[0]])
