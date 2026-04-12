#!/usr/bin/env python3
"""
jerry_gui.py — Native GTK3 media picker for jerry.sh

IMAGE mode (default):
  Reads "cover_url<TAB>media_id<TAB>title" from stdin, shows a scrollable
  cover-art grid with live search.  Outputs the selected line to stdout.

LIST mode (--mode list):
  Reads plain-text (or tab-delimited) lines from stdin, shows a searchable
  list dialog.  Outputs the selected original line to stdout.

Exit 0 on selection, 1 on cancel / empty input.
"""

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GdkPixbuf, GLib, Gdk, Pango  # noqa: E402

import sys
import os
import threading
import urllib.request
import argparse
from concurrent.futures import ThreadPoolExecutor

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_DEFAULT_CACHE_DIR = os.path.join(
    os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")),
    "jerry", "images",
)
# Resolved after arg parsing; may be overridden by --cache-dir
CACHE_DIR = _DEFAULT_CACHE_DIR

# Limit concurrent image fetches so we don't hammer the network
_FETCH_POOL = ThreadPoolExecutor(max_workers=8)

CARD_W   = 168   # FlowBoxChild request width
CARD_H   = 268   # FlowBoxChild request height
IMG_W    = 156   # cover image width
IMG_H    = 224   # cover image height
WIN_W    = 1120
WIN_H    = 760

# ---------------------------------------------------------------------------
# CSS — GitHub-dark inspired palette
# ---------------------------------------------------------------------------
CSS = b"""
window, dialog {
    background-color: #0d1117;
}
.header-bar {
    background-color: #161b22;
    border-bottom: 1px solid #30363d;
    padding: 10px 16px;
}
.app-title {
    color: #e6edf3;
    font-size: 17px;
    font-weight: bold;
    letter-spacing: 1px;
}
.count-label {
    color: #8b949e;
    font-size: 12px;
    margin-left: 6px;
}
.search-entry {
    background-color: #21262d;
    color: #e6edf3;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 5px 12px;
    font-size: 13px;
    min-width: 260px;
    caret-color: #58a6ff;
}
.search-entry:focus {
    border-color: #58a6ff;
    box-shadow: 0 0 0 3px rgba(88,166,255,0.18);
}
/* ---- cover card ---- */
.anime-card {
    background-color: #161b22;
    border-radius: 8px;
    padding: 5px;
    margin: 5px;
    border: 2px solid transparent;
}
.anime-card:hover {
    background-color: #1c2128;
    border-color: #30363d;
}
.anime-card:selected {
    border-color: #58a6ff;
    background-color: #1c2942;
}
.card-title {
    color: #c9d1d9;
    font-size: 11px;
}
/* ---- list picker ---- */
treeview {
    background-color: #0d1117;
    color: #e6edf3;
    font-size: 13px;
}
treeview:selected {
    background-color: #1c2942;
    color: #58a6ff;
}
"""


# ---------------------------------------------------------------------------
# Image-mode: one cover-art card
# ---------------------------------------------------------------------------
class AnimeCard(Gtk.FlowBoxChild):
    def __init__(self, cover_url: str, media_id: str, title: str, raw_line: str):
        super().__init__()
        self.cover_url  = cover_url
        self.media_id   = media_id
        self.title      = title
        self.raw_line   = raw_line
        self._title_lc  = title.lower()

        self.get_style_context().add_class("anime-card")
        self.set_size_request(CARD_W, CARD_H)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.add(vbox)

        # placeholder → real image loaded async
        self.img = Gtk.Image()
        self.img.set_size_request(IMG_W, IMG_H)
        buf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, IMG_W, IMG_H)
        buf.fill(0x21262dff)
        self.img.set_from_pixbuf(buf)
        vbox.pack_start(self.img, False, False, 0)

        lbl = Gtk.Label(label=title)
        lbl.set_line_wrap(True)
        lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lbl.set_max_width_chars(18)
        lbl.set_lines(2)
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        lbl.set_justify(Gtk.Justification.CENTER)
        lbl.get_style_context().add_class("card-title")
        vbox.pack_start(lbl, False, False, 0)

        self.show_all()
        _FETCH_POOL.submit(self._fetch)

    # -- async image fetch ---------------------------------------------------
    def _fetch(self):
        path = os.path.join(CACHE_DIR, f"{self.media_id}.jpg")
        try:
            if not (os.path.exists(path) and os.path.getsize(path) > 0):
                if self.cover_url:
                    req = urllib.request.Request(
                        self.cover_url,
                        headers={"User-Agent": "Mozilla/5.0 Jerry/2.0"},
                    )
                    with urllib.request.urlopen(req, timeout=12) as r:
                        data = r.read()
                    with open(path, "wb") as f:
                        f.write(data)
            if os.path.exists(path) and os.path.getsize(path) > 0:
                GLib.idle_add(self._show, path)
        except Exception:
            pass

    def _show(self, path: str):
        try:
            buf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, IMG_W, IMG_H, False)
            self.img.set_from_pixbuf(buf)
        except Exception:
            pass
        return False   # don't repeat idle


# ---------------------------------------------------------------------------
# Image-mode main window
# ---------------------------------------------------------------------------
class CoverPickerWindow(Gtk.Window):
    def __init__(self, items: list, prompt: str):
        super().__init__(title="Jerry")
        self.result  = None
        self._items  = items
        self._query  = ""

        self.set_default_size(WIN_W, WIN_H)
        self.set_position(Gtk.WindowPosition.CENTER)

        _apply_css()

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(outer)

        # --- header ---
        hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        hdr.get_style_context().add_class("header-bar")
        outer.pack_start(hdr, False, False, 0)

        title_lbl = Gtk.Label(label="Jerry")
        title_lbl.get_style_context().add_class("app-title")
        hdr.pack_start(title_lbl, False, False, 0)

        self._count_lbl = Gtk.Label(label=f"{len(items)} titles")
        self._count_lbl.get_style_context().add_class("count-label")
        hdr.pack_start(self._count_lbl, False, False, 0)

        self._search = Gtk.SearchEntry()
        self._search.set_placeholder_text(prompt or "Search…")
        self._search.get_style_context().add_class("search-entry")
        self._search.connect("search-changed", self._on_search_changed)
        self._search.connect("activate", self._on_search_activate)
        hdr.pack_end(self._search, False, False, 0)

        # --- scrolled FlowBox ---
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        outer.pack_start(scroll, True, True, 0)

        self._flow = Gtk.FlowBox()
        self._flow.set_valign(Gtk.Align.START)
        self._flow.set_max_children_per_line(14)
        self._flow.set_min_children_per_line(2)
        self._flow.set_column_spacing(0)
        self._flow.set_row_spacing(0)
        self._flow.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._flow.set_filter_func(self._filter)
        self._flow.connect("child-activated", self._on_activated)
        scroll.add(self._flow)

        for cover_url, media_id, title, raw in items:
            self._flow.add(AnimeCard(cover_url, media_id, title, raw))

        self.connect("destroy", Gtk.main_quit)
        self.connect("key-press-event", self._on_key)
        self.show_all()
        GLib.idle_add(self._search.grab_focus)

    # -- filter / search -----------------------------------------------------
    def _filter(self, child):
        return (not self._query) or (self._query in child._title_lc)

    def _on_search_changed(self, entry):
        self._query = entry.get_text().lower()
        self._flow.invalidate_filter()
        GLib.idle_add(self._refresh_count)

    def _refresh_count(self):
        n = sum(1 for c in self._flow.get_children() if c.get_mapped())
        total = len(self._items)
        self._count_lbl.set_text(
            f"{n}/{total} titles" if self._query else f"{total} titles"
        )
        return False

    # -- selection -----------------------------------------------------------
    def _on_activated(self, _flow, child):
        self.result = child.raw_line
        Gtk.main_quit()

    def _on_search_activate(self, _entry):
        for child in self._flow.get_children():
            if child.get_mapped():
                self.result = child.raw_line
                Gtk.main_quit()
                return

    def _on_key(self, _w, event):
        if event.keyval == Gdk.KEY_Escape:
            Gtk.main_quit()


# ---------------------------------------------------------------------------
# List-mode dialog (yes/no, status choices, etc.)
# ---------------------------------------------------------------------------
class ListPickerDialog(Gtk.Dialog):
    def __init__(self, display_items: list, orig_lines: list, prompt: str):
        super().__init__(title="Jerry")
        self.result = None
        self._display = display_items
        self._orig    = orig_lines
        self._query   = ""

        _apply_css()

        self.set_default_size(480, 400)
        self.set_position(Gtk.WindowPosition.CENTER)

        box = self.get_content_area()
        box.set_spacing(0)

        search = Gtk.SearchEntry()
        search.set_placeholder_text(prompt or "Search…")
        search.get_style_context().add_class("search-entry")
        search.set_margin_top(12)
        search.set_margin_bottom(6)
        search.set_margin_start(12)
        search.set_margin_end(12)
        box.pack_start(search, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box.pack_start(scroll, True, True, 0)

        self._store = Gtk.ListStore(str)
        for item in display_items:
            self._store.append([item])

        self._fmodel = self._store.filter_new()
        self._fmodel.set_visible_func(self._filter)

        self._tree = Gtk.TreeView(model=self._fmodel)
        self._tree.set_headers_visible(False)
        col = Gtk.TreeViewColumn("", Gtk.CellRendererText(), text=0)
        self._tree.append_column(col)
        self._tree.connect("row-activated", self._on_row_activated)
        scroll.add(self._tree)

        search.connect("search-changed", lambda e: self._do_filter(e.get_text()))
        search.connect("activate", self._on_enter)
        self.connect("key-press-event", lambda _w, ev: (
            self.response(Gtk.ResponseType.CANCEL)
            if ev.keyval == Gdk.KEY_Escape else None
        ))
        self.show_all()
        GLib.idle_add(search.grab_focus)

    def _filter(self, model, it, _data):
        return (not self._query) or (self._query in model[it][0].lower())

    def _do_filter(self, text: str):
        self._query = text.lower()
        self._fmodel.refilter()

    def _on_row_activated(self, _tree, path, _col):
        it = self._fmodel.get_iter(path)
        disp = self._fmodel[it][0]
        self.result = self._resolve(disp)
        self.response(Gtk.ResponseType.OK)

    def _on_enter(self, _entry):
        _sel_model, it = self._tree.get_selection().get_selected()
        if it:
            disp = self._fmodel[it][0]
        else:
            it = self._fmodel.get_iter_first()
            if not it:
                return
            disp = self._fmodel[it][0]
        self.result = self._resolve(disp)
        self.response(Gtk.ResponseType.OK)

    def _resolve(self, display_val: str) -> str:
        """Map displayed value back to original line."""
        for orig, disp in zip(self._orig, self._display):
            if disp == display_val:
                return orig
        return display_val


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _apply_css():
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(),
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )


def _parse_args():
    p = argparse.ArgumentParser(description="Jerry native GUI picker")
    p.add_argument("--prompt",     default="Search…")
    p.add_argument("--mode",       choices=["image", "list"], default="image")
    p.add_argument("--with-nth",   dest="with_nth", default=None,
                   help="1-based column index to display in list mode")
    p.add_argument("--cache-dir",  dest="cache_dir", default=None,
                   help="Directory where jerry.sh pre-downloads cover images")
    return p.parse_args()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    args = _parse_args()

    # Point CACHE_DIR at the directory jerry.sh already populated, so we get
    # instant hits instead of re-downloading every image from scratch.
    global CACHE_DIR
    if args.cache_dir:
        CACHE_DIR = args.cache_dir
    os.makedirs(CACHE_DIR, exist_ok=True)

    raw_lines = [l for l in sys.stdin.read().splitlines() if l.strip()]

    if not raw_lines:
        sys.exit(1)

    if args.mode == "list":
        nth = int(args.with_nth) - 1 if args.with_nth else None
        display_items = []
        for line in raw_lines:
            if nth is not None:
                parts = line.split("\t")
                display_items.append(parts[nth] if nth < len(parts) else line)
            else:
                display_items.append(line)

        dlg = ListPickerDialog(display_items, raw_lines, args.prompt)
        resp = dlg.run()
        dlg.destroy()

        if resp == Gtk.ResponseType.OK and dlg.result is not None:
            print(dlg.result)
            sys.exit(0)
        sys.exit(1)

    # -- image mode ----------------------------------------------------------
    items = []
    for line in raw_lines:
        parts = line.split("\t")
        if len(parts) >= 3:
            cover_url, media_id, title = parts[0], parts[1], parts[2]
        elif len(parts) == 2:
            cover_url, media_id, title = "", parts[0], parts[1]
        else:
            cover_url, media_id, title = "", "", parts[0]
        items.append((cover_url, media_id, title, line))

    win = CoverPickerWindow(items, args.prompt)
    Gtk.main()

    if win.result is not None:
        print(win.result)
        sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    main()
