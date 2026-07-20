#!/usr/bin/env python3
"""
EQL Overlay Common
===================
Shared infrastructure for the EQL Log Reader overlay family (Friends list,
DPS/HPS meter, and anything added later). Keeping this in one module means
every overlay tails logs, persists settings, and drags/right-clicks the same
way, instead of re-implementing it per script.

Contents:
  * LogWatcher     -- tails a log file, dispatches complete new lines
  * Settings       -- tiny JSON-file-backed dict with load/save
  * make_draggable -- title-bar drag behavior for an overrideredirect() window
  * retro themes   -- shared palette/font definitions for "retro" overlay looks
"""

import json
import os
import shutil
import sys

POLL_INTERVAL_MS = 250      # how often overlays should re-check the log
SEED_BYTES = 512 * 1024     # how much of the log tail to parse on startup

# Suite-wide version stamp, used by the Launcher's update check
# (eql_update_check.py) to decide whether a newer GitHub release exists.
# Bump this alongside installer.iss's MyAppVersion on every release, or the
# update check will keep comparing against a stale number.
CURRENT_VERSION = "1.8"


# ----------------------------------------------------------------------------
# Log tailing
# ----------------------------------------------------------------------------
class LogWatcher:
    """Tails a log file; dispatches every complete line to handlers.

    Handles log growth, truncation/rotation, and partial trailing lines.
    Multiple overlays can each own their own LogWatcher instance pointed at
    the same log file -- reading is stateless/non-destructive, so there's no
    conflict running the Friends overlay and the DPS meter side by side.
    """

    def __init__(self, path):
        self.path = path
        self.handlers = []
        self._pos = 0
        self._buf = b""

    def add_handler(self, fn):
        self.handlers.append(fn)

    def seed(self, max_bytes=SEED_BYTES):
        """Parse the tail of the existing log so state is warm on startup."""
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return
        start = max(0, size - max_bytes)
        with open(self.path, "rb") as f:
            f.seek(start)
            data = f.read()
            self._pos = f.tell()
        if start > 0:                       # drop the first partial line
            nl = data.find(b"\n")
            data = data[nl + 1:] if nl >= 0 else b""
        for raw in data.splitlines():
            self._dispatch(raw)

    def poll(self):
        """Read any new bytes; dispatch complete new lines."""
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return                          # file temporarily missing
        if size < self._pos:                # truncated/rotated -> start over
            self._pos = 0
            self._buf = b""
        if size == self._pos:
            return
        try:
            with open(self.path, "rb") as f:
                f.seek(self._pos)
                chunk = f.read()
                self._pos = f.tell()
        except OSError:
            return   # transient lock (writer/AV/indexer) -- retry next tick
        self._buf += chunk
        while b"\n" in self._buf:
            raw, self._buf = self._buf.split(b"\n", 1)
            self._dispatch(raw)

    def _dispatch(self, raw):
        line = raw.decode("cp1252", errors="replace").rstrip("\r")
        for fn in self.handlers:
            fn(line)


# ----------------------------------------------------------------------------
# Per-user data files (settings, rosters, records, all-time stats)
# ----------------------------------------------------------------------------
def data_path(filename, script_dir):
    """Resolve where a per-user data file lives.

    Running from SOURCE: next to the scripts, as always (dev workflow).
    FROZEN (installed) builds: %APPDATA%\\EQL Log Reader -- the exe sits in
    Program Files, where writes fail (or get UAC-virtualized) for normal
    users and don't survive uninstall/reinstall cycles. A file found only
    next to the exe (older builds wrote there when they could) is migrated
    once, so nobody loses their settings/records on update."""
    if not getattr(sys, "frozen", False):
        return os.path.join(script_dir, filename)
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "EQL Log Reader")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        return os.path.join(script_dir, filename)
    new = os.path.join(d, filename)
    old = os.path.join(script_dir, filename)
    if not os.path.exists(new) and os.path.exists(old):
        try:
            shutil.copy2(old, new)
        except OSError:
            pass
    return new


def install_tk_error_logger(root, tool_name, log_path):
    """Make Tk callback exceptions non-fatal in windowed builds.

    A no-console (windowed) exe has sys.stderr = None, so Tk's DEFAULT
    callback-exception reporter -- which prints the traceback -- itself
    raises, and that secondary failure can abort the mainloop: one buggy
    callback kills the whole overlay with no trace. Replace it with a
    reporter that appends the traceback to a log file and keeps running."""
    import time
    import traceback

    def report(exc, val, tb):
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                        f"{tool_name}: exception in a Tk callback\n")
                traceback.print_exception(exc, val, tb, file=f)
        except OSError:
            pass

    root.report_callback_exception = report


# ----------------------------------------------------------------------------
# Settings persistence
# ----------------------------------------------------------------------------
class Settings(dict):
    """A dict that knows how to load/save itself as JSON, with defaults."""

    def __init__(self, path, defaults=None):
        super().__init__(defaults or {})
        self._path = path
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.update(json.load(f))
        except (OSError, ValueError):
            pass

    def save(self):
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(dict(self), f, indent=2)
        except OSError:
            pass


# ----------------------------------------------------------------------------
# Window dragging (title-bar drag for borderless always-on-top windows)
# ----------------------------------------------------------------------------
def make_draggable(root, handles, settings, on_moved=None):
    """Wire up drag-to-move on `handles` (widgets) for a borderless `root`.

    settings["x"]/["y"] are kept in sync and saved on release. `on_moved`
    (optional) is called with no args after each drag completes.
    """
    drag = {"x": 0, "y": 0}

    def on_press(e):
        drag["x"], drag["y"] = e.x_root - root.winfo_x(), e.y_root - root.winfo_y()

    def on_drag(e):
        settings["x"], settings["y"] = e.x_root - drag["x"], e.y_root - drag["y"]
        root.geometry(f"+{settings['x']}+{settings['y']}")

    def on_release(_):
        settings.save()
        if on_moved:
            on_moved()

    for w in handles:
        w.bind("<ButtonPress-1>", on_press)
        w.bind("<B1-Motion>", on_drag)
        w.bind("<ButtonRelease-1>", on_release)


def luma(color):
    """Perceived brightness (0-255) of an '#rrggbb' color. Shared by the
    overlays for contrast decisions (e.g. text-on-bar colors, and whether
    a dark-on-bright text should skip the Neon HUD outline)."""
    r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
    return 0.299 * r + 0.587 * g + 0.114 * b


# ----------------------------------------------------------------------------
# Retro visual themes -- shared across overlays that want a "retro" skin
# ----------------------------------------------------------------------------
# Each theme is a plain dict of colors/fonts so overlay code can do
# theme["bg"], theme["accent"], etc. font family lists are fallback chains;
# tkinter silently falls back to a default if none are installed, so these
# degrade gracefully on a machine without retro/pixel fonts.
#
# A theme with "transparent": True treats its bg color as a chroma key:
# overlays call root.attributes("-transparentcolor", theme["bg"]) so ONLY
# text/bars float over the game (Windows feature; elsewhere it degrades to
# a plain dark background). Such themes must give every text role -- dim
# included -- a bright, wallpaper-proof color, and provide "ink" (a dark
# NON-key color) for text drawn on top of bright bars, since anything drawn
# in the key color itself would punch a see-through hole.
RETRO_THEMES = {
    "crt": {
        "label": "CRT Terminal",
        "bg": "#020a04",
        "panel": "#03140a",
        "fg": "#39ff6a",
        "dim": "#0f5c2c",
        "accent": "#7dffb0",
        "warn": "#ffcf3d",
        "bad": "#ff4d4d",
        "alt": "#3dd6ff",
        "scanline": "#00220f",
        "font_mono": ("Consolas", "Courier New", "monospace"),
        "glow": True,
    },
    "arcade": {
        "label": "Arcade LED",
        "bg": "#0a0410",
        "panel": "#160a24",
        "fg": "#ff2fd0",
        "dim": "#5a2a63",
        "accent": "#2fe8ff",
        "warn": "#ffe62f",
        "bad": "#ff3b3b",
        "alt": "#8dff3d",
        "scanline": "#1a0d2c",
        "font_mono": ("Consolas", "Courier New", "monospace"),
        "glow": True,
    },
    "pixel": {
        "label": "16-bit Window",
        "bg": "#1a1030",
        "panel": "#2b1d4a",
        "fg": "#f8f4e3",
        "dim": "#8a7ab5",
        "accent": "#ffd05a",
        "warn": "#ffb84d",
        "bad": "#ff6b6b",
        "alt": "#6fd88a",
        "border_light": "#6a58a0",
        "border_dark": "#0f0a1e",
        "font_mono": ("Consolas", "Courier New", "monospace"),
        "glow": False,
    },
    "text": {
        # deliberately monochrome: no scanlines, no bevel, and overlays
        # that honor "text_only" skip decorative graphics (e.g. the DPS
        # meter renders DMG SOURCES as plain text instead of bars)
        "label": "Vintage",
        "bg": "#0e0e10",
        "panel": "#1a1a1e",
        "fg": "#e6e6e6",
        "dim": "#7d7d84",
        "accent": "#ffffff",
        "warn": "#c9c9c9",
        "bad": "#a8a8a8",
        "alt": "#bdbdbd",
        "font_mono": ("Consolas", "Courier New", "monospace"),
        "glow": False,
        "text_only": True,
    },
    "hud": {
        "label": "Neon HUD (transparent)",
        "bg": "#010101",       # chroma key -- becomes fully see-through
        "panel": "#101010",    # title bar stays opaque: the grab handle
        "fg": "#39ff14",       # neon green
        "dim": "#00c8ff",      # bright cyan (secondary text must survive
                               # any wallpaper -- no true dim on transparent)
        "accent": "#ff2fd8",   # hot pink
        "warn": "#ffe600",     # neon yellow
        "bad": "#ff6a00",      # neon orange
        "alt": "#00ffd0",      # aqua
        "ink": "#000000",      # dark text ON bright bars (NOT the key color)
        "outline": "#000000",  # stroke drawn around all canvas text so it
                               # stays readable over bright game footage
        "scanline": "#010101",
        "font_mono": ("Consolas", "Courier New", "monospace"),
        "glow": False,
        "transparent": True,
    },
}

DEFAULT_THEME = "pixel"      # "16-bit Window" -- the suite-wide default


def get_theme(key):
    """Resolve a saved theme key to its theme dict, falling back to the
    default for unknown/retired keys (e.g. a settings file written by an
    older version that still says "classic")."""
    return RETRO_THEMES.get(key, RETRO_THEMES[DEFAULT_THEME])
