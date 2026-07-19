#!/usr/bin/env python3
"""
EQL Log Reader -- Launcher
============================
A small control panel for the overlay tools in this folder. Pick your
active EverQuest Legends character once, then start/stop each overlay with
a click instead of running scripts from a terminal.

Usage:
    python eql_launcher.py

Tools listed here are the standalone, runnable overlays:
    * Friends Overlay    (eql_friend_overlay.py)
    * DPS/HPS Meter      (eql_dps_meter.py)
    * Session Report     (eql_session_report.py)
    * Atlas Collector    (eql_atlas.py)

eql_overlay_common.py, eql_combat_tracker.py, and eql_spell_db.py are shared
library code that the tools above import -- there's nothing to start/stop
for those directly, so they aren't listed as toggleable rows here.

Closing this launcher window does NOT stop any overlays you've started --
they keep running independently so you can tuck this window away while you
play. Use the Stop button (or just close the overlay itself) to end one.

Character auto-detect
-----------------------
The game writes one log file per character+server: `eqlog_<Name>_<Server>.txt`
under the install's Logs folder, and (if you've exported one) an inventory
file `<Name>_<Server>-Inventory.txt` alongside it at the game's root folder.
"Auto-detect" scans the default Daybreak install directory for every
eqlog_*.txt it can find and lists one row per character+server -- click
"Select" to make that character active (this sets the log file the tools
above will use), or "Hide" to tuck away an old/unused character. Re-running
Auto-detect always reflects exactly what's currently in the game folder --
new characters appear, ones whose log file is gone disappear.
"""

import glob
import os
import re
import subprocess
import sys
import threading
import webbrowser
import tkinter as tk
from tkinter import filedialog, messagebox

from eql_overlay_common import (Settings, RETRO_THEMES, DEFAULT_THEME,
                                 get_theme, CURRENT_VERSION, data_path,
                                 install_tk_error_logger)
from eql_update_check import check_for_update

# The launcher is a normal decorated window, not a borderless overlay, so the
# transparent (chroma-key) Neon HUD theme -- meant to float over the game --
# isn't offered here. Same reasoning as the Session Report's theme picker.
LAUNCHER_THEMES = {k: v for k, v in RETRO_THEMES.items()
                    if not v.get("transparent")}


def _launcher_theme_key(key):
    """Clamp a saved theme key to one the launcher can actually use."""
    return key if key in LAUNCHER_THEMES else DEFAULT_THEME


if getattr(sys, "frozen", False):
    # Packaged (PyInstaller) exe: __file__ points into the temp extraction
    # dir, not the real install location. Use the exe's own directory so
    # settings/rosters persist next to where the user put the program.
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = data_path("eql_launcher_settings.json", APP_DIR)
ERROR_LOG = data_path("eql_errors.log", APP_DIR)
DEFAULT_INSTALL_DIR = r"C:\Users\Public\Daybreak Game Company\Installed Games"

TOOLS = [
    {"name": "Friends Overlay", "script": "eql_friend_overlay.py",
     "exe": "eql_friend_overlay.exe",
     "desc": "Shows which friends are online, with level/class/zone."},
    {"name": "DPS/HPS Meter", "script": "eql_dps_meter.py",
     "exe": "eql_dps_meter.exe",
     "desc": "Retro live combat meter -- damage, healing, kill rate."},
    {"name": "Session Report", "script": "eql_session_report.py",
     "exe": "eql_session_report.exe",
     "desc": "Detailed breakdown: damage/heal by ability, stance/invocation "
             "comparison, spells cast."},
    {"name": "Atlas Collector", "script": "eql_atlas.py",
     "exe": "eql_atlas.exe",
     "desc": "Records what died where and what it dropped -- builds your "
             "loot/spawn database as you play."},
]

# These two are always-on, live overlays meant to track whichever character
# is currently active -- each is launched with the log path fixed as a
# command-line argument, so a running instance keeps watching the OLD
# character's log until it's restarted. Auto-restart them (with the new log
# path) whenever the active character changes, so switching characters "just
# works" instead of requiring a manual close/reopen. Session Report is a
# read-once deep-dive the player opens per session (with its own "Change
# log..." button and several tabs of state) -- restarting it out from under
# them would lose their place, so it's deliberately left alone here.
AUTO_RESTART_TOOLS = {"Friends Overlay", "DPS/HPS Meter", "Atlas Collector"}

_EQLOG_NAME_RE = re.compile(r"^eqlog_([A-Za-z0-9]+)_([A-Za-z0-9]+)\.txt$", re.IGNORECASE)


def discover_characters():
    """Scan the default Daybreak install dir for every eqlog_<Name>_<Server>.txt
    file, a few levels deep (games live at base/<GameName>/Logs/eqlog_*.txt).
    Returns {char_id: {"id", "name", "server", "log_path", "inventory_path"}},
    one entry per character+server -- freshly built every call, so it always
    reflects exactly what's on disk right now."""
    found = {}
    base = DEFAULT_INSTALL_DIR
    if not os.path.isdir(base):
        return found
    for depth in ("", "*", os.path.join("*", "*"), os.path.join("*", "*", "*")):
        for path in glob.glob(os.path.join(base, depth, "eqlog_*.txt")):
            m = _EQLOG_NAME_RE.match(os.path.basename(path))
            if not m:
                continue
            name, server = m.group(1), m.group(2)
            cid = f"{name}_{server}"
            # inventory export (if any) lives at the game root, one level up
            # from the Logs folder the eqlog file itself is in.
            game_root = os.path.dirname(os.path.dirname(path))
            inv_path = os.path.join(game_root, f"{name}_{server}-Inventory.txt")
            if not os.path.isfile(inv_path):
                inv_path = None
            entry = {"id": cid, "name": name, "server": server,
                     "log_path": path, "inventory_path": inv_path}
            existing = found.get(cid)
            if existing is None or os.path.getmtime(path) > os.path.getmtime(existing["log_path"]):
                found[cid] = entry
    return found


def _match_discovered_character(path):
    """If `path` is exactly one of the auto-detected characters' log files,
    return its roster entry so Browse can mark it active too -- otherwise
    Select and Browse would highlight the roster differently even when they
    land on the very same character's log."""
    target = os.path.normcase(os.path.abspath(path))
    for entry in discover_characters().values():
        if os.path.normcase(os.path.abspath(entry["log_path"])) == target:
            return entry
    return None


def _mix(c1, c2, t):
    """Blend two '#rrggbb' colors: t=0 -> c1, t=1 -> c2. Derives the
    launcher-only tones (row backgrounds) the compact themes don't define."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


class ToolRow:
    """One tool's row: name/desc, status, and a Start/Stop toggle button.

    The subprocess handle lives in the shared `procs` dict (keyed by tool
    name) rather than on the row itself, so picking a new theme can rebuild
    the whole UI without the launcher losing track of overlays that are
    already running."""

    def __init__(self, parent, spec, get_log_path, procs, pal):
        self.spec = spec
        self.get_log_path = get_log_path
        self.procs = procs
        self.pal = pal

        P = pal
        self.frame = tk.Frame(parent, bg=P["panel"], padx=10, pady=8)
        self.frame.pack(fill="x", padx=10, pady=4)

        left = tk.Frame(self.frame, bg=P["panel"])
        left.pack(side="left", fill="x", expand=True)
        tk.Label(left, text=spec["name"], font=(P["font"], 11, "bold"),
                 bg=P["panel"], fg=P["fg"], anchor="w").pack(fill="x")
        tk.Label(left, text=spec["desc"], font=(P["font"], 8),
                 bg=P["panel"], fg=P["dim"], anchor="w").pack(fill="x")

        right = tk.Frame(self.frame, bg=P["panel"])
        right.pack(side="right")
        self.status_lbl = tk.Label(right, text="○ stopped", font=(P["font"], 9),
                                    bg=P["panel"], fg=P["dim"], width=12, anchor="e")
        self.status_lbl.pack(side="left", padx=(0, 10))
        self.btn = P["button"](right, text="Start", width=8, command=self.toggle)
        self.btn.pack(side="left")
        self.refresh()

    def _proc(self):
        return self.procs.get(self.spec["name"])

    def running(self):
        p = self._proc()
        return p is not None and p.poll() is None

    def toggle(self):
        if self.running():
            self.stop()
        else:
            self.start()

    def start(self):
        log_path = self.get_log_path()
        if not log_path:
            messagebox.showinfo(
                "No character selected",
                "Pick a character first (Auto-detect or Browse, top of the launcher).")
            return
        try:
            if getattr(sys, "frozen", False):
                # Packaged build: sys.executable is this exe, not a python
                # interpreter, so launch the sibling tool exe directly.
                target = os.path.join(APP_DIR, self.spec["exe"])
                self.procs[self.spec["name"]] = subprocess.Popen(
                    [target, log_path], cwd=APP_DIR)
            else:
                script = os.path.join(APP_DIR, self.spec["script"])
                self.procs[self.spec["name"]] = subprocess.Popen(
                    [sys.executable, script, log_path], cwd=APP_DIR)
        except OSError as e:
            messagebox.showerror("Couldn't start", f"{self.spec['name']}: {e}")
            self.procs.pop(self.spec["name"], None)
        self.refresh()

    def stop(self):
        if self.running():
            try:
                self._proc().terminate()
            except OSError:
                pass
        self.refresh()

    def refresh(self):
        if self.running():
            self.status_lbl.config(text="● running", fg=self.pal["good"])
            self.btn.config(text="Stop")
        else:
            self.procs.pop(self.spec["name"], None)
            self.status_lbl.config(text="○ stopped", fg=self.pal["dim"])
            self.btn.config(text="Start")


def main():
    settings = Settings(SETTINGS_FILE, {
        "log_path": "", "active_character_id": "", "inventory_path": "",
        "hidden_characters": [],
        "theme": DEFAULT_THEME,   # shared suite theme set (16-bit Window)
    })

    root = tk.Tk()
    install_tk_error_logger(root, "eql_launcher", ERROR_LOG)
    root.title("EQL Log Reader -- Launcher")
    root.resizable(False, False)

    procs = {}                       # tool name -> Popen; survives re-themes
    ui = {"after": None}             # pending poll callback, cancelled on rebuild

    # -- update check ------------------------------------------------------
    # update_info["result"] is None until a background check finds something
    # newer than CURRENT_VERSION, at which point it becomes
    # {"version": "v1.2", "url": "https://github.com/.../releases/tag/v1.2"}
    # and build_ui() (called again here) renders a small banner for it.
    # Deliberately does nothing more automatic than that -- see
    # eql_update_check.py for why: no download, no auto-run, nothing that
    # looks like updater-malware behavior to antivirus.
    update_info = {"result": None}

    def check_updates_async(manual):
        """Run the GitHub check off-thread (it's a blocking network call),
        then hop back onto the Tk main thread via root.after to touch any
        widgets. `manual` controls whether a result is reported either way
        (button click) or only when something's actually found (silent
        startup check -- most runs it'll find nothing, and that shouldn't
        pop a dialog)."""
        def worker():
            result = check_for_update(CURRENT_VERSION)

            def apply():
                if result:
                    version, url = result
                    update_info["result"] = {"version": version, "url": url}
                    build_ui()
                elif manual:
                    messagebox.showinfo(
                        "Up to date",
                        f"You're running the latest version (v{CURRENT_VERSION}).")
            root.after(0, apply)

        threading.Thread(target=worker, daemon=True).start()

    def build_ui():
        """(Re)build the whole launcher UI in the current theme. Called once
        at startup and again whenever a new theme is picked -- widgets bake
        their colors in at creation, so a clean rebuild is simplest. Running
        overlays are unaffected (their handles live in `procs`)."""
        if ui["after"] is not None:
            root.after_cancel(ui["after"])
            ui["after"] = None
        for w in list(root.children.values()):
            w.destroy()

        th = get_theme(_launcher_theme_key(settings.get("theme", DEFAULT_THEME)))
        BG, PANEL, FG, DIM, ACCENT = (th["bg"], th["panel"], th["fg"],
                                      th["dim"], th["accent"])
        GOOD = th["alt"]
        ROW_BG = _mix(PANEL, FG, 0.06)
        ROW_BG_ACTIVE = _mix(PANEL, ACCENT, 0.22)
        FAM = th["font_mono"][0]

        def button(parent, **kw):
            kw.setdefault("font", (FAM, 8))
            return tk.Button(parent, bg=PANEL, fg=FG, activebackground=ACCENT,
                             activeforeground=BG, relief="flat", **kw)

        pal = {"bg": BG, "panel": PANEL, "fg": FG, "dim": DIM,
               "accent": ACCENT, "good": GOOD, "font": FAM, "button": button}

        root.configure(bg=BG)

        header = tk.Frame(root, bg=BG, padx=12, pady=10)
        header.pack(fill="x")
        tk.Label(header, text="EQL LOG READER", font=(FAM, 12, "bold"),
                 bg=BG, fg=ACCENT).pack(side="left")

        # theme picker: same theme set as every other applet in the suite
        def pick_theme(k):
            settings["theme"] = k
            settings.save()
            build_ui()

        theme_btn = tk.Menubutton(header, text="Theme ▾", font=(FAM, 8),
                                  bg=PANEL, fg=FG, activebackground=ACCENT,
                                  activeforeground=BG, relief="flat", padx=8)
        theme_menu = tk.Menu(theme_btn, tearoff=0)
        cur = _launcher_theme_key(settings.get("theme", DEFAULT_THEME))
        for key, spec in LAUNCHER_THEMES.items():
            mark = "● " if key == cur else "   "
            theme_menu.add_command(label=mark + spec["label"],
                                   command=lambda k=key: pick_theme(k))
        theme_btn.configure(menu=theme_menu)
        theme_btn.pack(side="right")

        check_updates_btn = button(header, text="Check for Updates", font=(FAM, 8))
        check_updates_btn.config(command=lambda: check_updates_async(manual=True))
        check_updates_btn.pack(side="right", padx=(0, 6))

        # -- update banner (only shown once a background/manual check has
        # actually found something newer than CURRENT_VERSION) -----------------
        if update_info["result"]:
            info = update_info["result"]

            def copy_update_link():
                root.clipboard_clear()
                root.clipboard_append(info["url"])
                root.update()  # push the clipboard write through immediately
                messagebox.showinfo("Link copied",
                                     "Download link copied to clipboard.")

            def open_update_link():
                webbrowser.open(info["url"])

            upd = tk.Frame(root, bg=ACCENT, padx=10, pady=6)
            upd.pack(fill="x")
            tk.Label(upd, text=f"⬆ {info['version']} available",
                     font=(FAM, 9, "bold"), bg=ACCENT, fg=BG).pack(side="left")
            tk.Button(upd, text="Copy Link", font=(FAM, 8), bg=BG, fg=ACCENT,
                      relief="flat", command=copy_update_link
                      ).pack(side="right", padx=(4, 0))
            tk.Button(upd, text="Open in Browser", font=(FAM, 8), bg=BG, fg=ACCENT,
                      relief="flat", command=open_update_link
                      ).pack(side="right")

        # -- active character / log file summary --------------------------------
        log_frame = tk.Frame(root, bg=PANEL, padx=10, pady=8)
        log_frame.pack(fill="x", padx=10, pady=(0, 6))
        tk.Label(log_frame, text="Active character", font=(FAM, 9, "bold"),
                 bg=PANEL, fg=DIM).pack(anchor="w")
        path_lbl = tk.Label(log_frame, text="(none selected)", font=(FAM, 9),
                            bg=PANEL, fg=FG, anchor="w", justify="left",
                            wraplength=380)
        path_lbl.pack(anchor="w", fill="x", pady=(2, 6))

        def active_label_text():
            cid = settings.get("active_character_id", "")
            p = settings.get("log_path", "")
            if not p:
                return "(none selected)"
            if cid and "_" in cid:
                name, server = cid.split("_", 1)
                inv = " -- inventory found" if settings.get("inventory_path") else ""
                return f"{name}  ({server}){inv}\n{p}"
            return p

        def refresh_path_label():
            path_lbl.config(text=active_label_text())

        def get_log_path():
            return settings.get("log_path", "")

        def browse_log():
            initialdir = os.path.dirname(settings.get("log_path", "")) or DEFAULT_INSTALL_DIR
            if not os.path.isdir(initialdir):
                initialdir = None
            chosen = filedialog.askopenfilename(
                title="Select your EverQuest log file (eqlog_*.txt)",
                initialdir=initialdir,
                filetypes=[("EQ log files", "eqlog_*.txt"),
                           ("Text files", "*.txt"), ("All files", "*.*")])
            if chosen and os.path.isfile(chosen):
                settings["log_path"] = chosen
                # If the browsed file matches one of the auto-detected
                # characters, mark it active too -- so Browse highlights the
                # roster the same way Select does, instead of only updating
                # the summary line above and leaving the picker unhighlighted.
                matched = _match_discovered_character(chosen)
                if matched:
                    settings["active_character_id"] = matched["id"]
                    settings["inventory_path"] = matched.get("inventory_path") or ""
                else:
                    settings["active_character_id"] = ""
                    settings["inventory_path"] = ""
                settings.save()
                refresh_path_label()
                rebuild_roster()
                restart_running_tools()

        btn_row = tk.Frame(log_frame, bg=PANEL)
        btn_row.pack(anchor="w")
        button(btn_row, text="Browse...", command=browse_log,
               font=(FAM, 9)).pack(side="left")
        refresh_path_label()

        # -- character roster ----------------------------------------------------
        chars_frame = tk.Frame(root, bg=PANEL, padx=10, pady=8)
        chars_frame.pack(fill="x", padx=10, pady=(0, 6))

        chars_header = tk.Frame(chars_frame, bg=PANEL)
        chars_header.pack(fill="x")
        tk.Label(chars_header, text="Characters", font=(FAM, 9, "bold"),
                 bg=PANEL, fg=DIM).pack(side="left")
        hidden_btn = button(chars_header, text="Hidden (0)")
        hidden_btn.pack(side="right")
        auto_btn = button(chars_header, text="Auto-detect", font=(FAM, 9))
        auto_btn.pack(side="right", padx=(0, 6))

        roster_list = tk.Frame(chars_frame, bg=PANEL)
        roster_list.pack(fill="x", pady=(6, 0))

        def select_character(entry):
            settings["log_path"] = entry["log_path"]
            settings["active_character_id"] = entry["id"]
            settings["inventory_path"] = entry.get("inventory_path") or ""
            settings.save()
            refresh_path_label()
            rebuild_roster()
            restart_running_tools()

        def restart_running_tools():
            """Seamlessly bounce any live overlay so it picks up the log
            path just set above, instead of continuing to watch the
            character you switched away from. `rows` is defined further
            down in build_ui(); by the time a button click can actually
            reach this closure the whole UI (rows included) already
            exists, so the late-bound lookup is safe."""
            for row in rows:
                if row.spec["name"] in AUTO_RESTART_TOOLS and row.running():
                    row.stop()
                    row.start()

        def hide_character(cid):
            hidden = set(settings.get("hidden_characters", []))
            hidden.add(cid)
            settings["hidden_characters"] = sorted(hidden)
            settings.save()
            rebuild_roster()

        def unhide_character(cid):
            hidden = set(settings.get("hidden_characters", []))
            hidden.discard(cid)
            settings["hidden_characters"] = sorted(hidden)
            settings.save()
            rebuild_roster()
            open_hidden_manager()  # refresh the dialog in place

        def open_hidden_manager():
            for w in list(root.children.values()):
                if getattr(w, "_is_hidden_manager", False):
                    w.destroy()
            hidden = sorted(settings.get("hidden_characters", []))
            win = tk.Toplevel(root)
            win._is_hidden_manager = True
            win.title("Hidden characters")
            win.configure(bg=PANEL)
            win.resizable(False, False)
            if not hidden:
                tk.Label(win, text="No hidden characters.", bg=PANEL, fg=DIM,
                         font=(FAM, 9), padx=16, pady=16).pack()
                return
            for cid in hidden:
                name, _, server = cid.partition("_")
                row = tk.Frame(win, bg=PANEL, padx=10, pady=4)
                row.pack(fill="x")
                tk.Label(row, text=f"{name}  ({server})", bg=PANEL, fg=FG,
                         font=(FAM, 9), anchor="w").pack(side="left", fill="x",
                                                         expand=True)
                button(row, text="Unhide",
                       command=lambda c=cid: unhide_character(c)
                       ).pack(side="right")

        hidden_btn.config(command=open_hidden_manager)

        def rebuild_roster():
            for w in roster_list.winfo_children():
                w.destroy()
            found = discover_characters()
            hidden = set(settings.get("hidden_characters", []))
            hidden_btn.config(text=f"Hidden ({len(hidden)})")
            active_cid = settings.get("active_character_id", "")
            visible = sorted((c for c in found.values() if c["id"] not in hidden),
                             key=lambda c: (c["name"].lower(), c["server"].lower()))
            if not visible:
                tk.Label(roster_list, text="No characters found. Click Auto-detect, "
                                           "or use Browse... to pick a log file manually.",
                         bg=PANEL, fg=DIM, font=(FAM, 8), wraplength=380,
                         justify="left").pack(anchor="w")
                return
            for entry in visible:
                is_active = entry["id"] == active_cid
                row = tk.Frame(roster_list,
                               bg=ROW_BG_ACTIVE if is_active else ROW_BG,
                               padx=8, pady=5)
                row.pack(fill="x", pady=2)
                label_text = f"{'● ' if is_active else ''}{entry['name']}  ({entry['server']})"
                if entry.get("inventory_path"):
                    label_text += "   🎒"
                tk.Label(row, text=label_text, bg=row["bg"],
                         fg=ACCENT if is_active else FG, anchor="w",
                         font=(FAM, 9, "bold" if is_active else "normal")
                         ).pack(side="left", fill="x", expand=True)
                button(row, text="Hide",
                       command=lambda c=entry["id"]: hide_character(c)
                       ).pack(side="right")
                button(row, text="Select", width=7,
                       command=lambda e=entry: select_character(e)
                       ).pack(side="right", padx=(0, 4))

        def auto_detect():
            rebuild_roster()

        auto_btn.config(command=auto_detect)

        rebuild_roster()

        # -- tool rows ---------------------------------------------------------
        tools_frame = tk.Frame(root, bg=BG)
        tools_frame.pack(fill="x")
        rows = [ToolRow(tools_frame, spec, get_log_path, procs, pal)
                for spec in TOOLS]

        footer = tk.Label(
            root,
            text="eql_overlay_common.py, eql_combat_tracker.py, and eql_spell_db.py are\n"
                 "shared code used by the tools above -- nothing to start/stop for those.",
            font=(FAM, 8), bg=BG, fg=DIM, justify="left")
        footer.pack(anchor="w", padx=12, pady=(4, 10))

        def poll_status():
            for row in rows:
                row.refresh()
            ui["after"] = root.after(1000, poll_status)

        poll_status()

    build_ui()
    check_updates_async(manual=False)   # quiet startup check; no dialog if nothing's newer
    root.mainloop()


if __name__ == "__main__":
    main()
