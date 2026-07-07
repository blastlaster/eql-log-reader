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
import tkinter as tk
from tkinter import filedialog, messagebox

from eql_overlay_common import Settings

APP_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(APP_DIR, "eql_launcher_settings.json")
DEFAULT_INSTALL_DIR = r"C:\Users\Public\Daybreak Game Company\Installed Games"

TOOLS = [
    {"name": "Friends Overlay", "script": "eql_friend_overlay.py",
     "desc": "Shows which friends are online, with level/class/zone."},
    {"name": "DPS/HPS Meter", "script": "eql_dps_meter.py",
     "desc": "Retro live combat meter -- damage, healing, kill rate."},
    {"name": "Session Report", "script": "eql_session_report.py",
     "desc": "Detailed breakdown: damage/heal by ability, stance/invocation "
             "comparison, spells cast."},
]

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


class ToolRow:
    """One tool's row: name/desc, status, and a Start/Stop toggle button."""

    def __init__(self, parent, spec, get_log_path):
        self.spec = spec
        self.get_log_path = get_log_path
        self.proc = None

        self.frame = tk.Frame(parent, bg="#161c22", padx=10, pady=8)
        self.frame.pack(fill="x", padx=10, pady=4)

        left = tk.Frame(self.frame, bg="#161c22")
        left.pack(side="left", fill="x", expand=True)
        tk.Label(left, text=spec["name"], font=("Segoe UI", 11, "bold"),
                 bg="#161c22", fg="#d8dee6", anchor="w").pack(fill="x")
        tk.Label(left, text=spec["desc"], font=("Segoe UI", 8),
                 bg="#161c22", fg="#8a94a3", anchor="w").pack(fill="x")

        right = tk.Frame(self.frame, bg="#161c22")
        right.pack(side="right")
        self.status_lbl = tk.Label(right, text="○ stopped", font=("Segoe UI", 9),
                                    bg="#161c22", fg="#5c6773", width=12, anchor="e")
        self.status_lbl.pack(side="left", padx=(0, 10))
        self.btn = tk.Button(right, text="Start", width=8, command=self.toggle)
        self.btn.pack(side="left")

    def running(self):
        return self.proc is not None and self.proc.poll() is None

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
        script = os.path.join(APP_DIR, self.spec["script"])
        try:
            self.proc = subprocess.Popen([sys.executable, script, log_path],
                                         cwd=APP_DIR)
        except OSError as e:
            messagebox.showerror("Couldn't start", f"{self.spec['name']}: {e}")
            self.proc = None
        self.refresh()

    def stop(self):
        if self.running():
            try:
                self.proc.terminate()
            except OSError:
                pass
        self.refresh()

    def refresh(self):
        if self.running():
            self.status_lbl.config(text="● running", fg="#57d977")
            self.btn.config(text="Stop")
        else:
            self.proc = None
            self.status_lbl.config(text="○ stopped", fg="#5c6773")
            self.btn.config(text="Start")


def main():
    settings = Settings(SETTINGS_FILE, {
        "log_path": "", "active_character_id": "", "inventory_path": "",
        "hidden_characters": [],
    })

    root = tk.Tk()
    root.title("EQL Log Reader -- Launcher")
    root.configure(bg="#101418")
    root.resizable(False, False)

    BG, PANEL, FG, DIM, ACCENT = "#101418", "#161c22", "#d8dee6", "#5c6773", "#8fbf6f"
    ROW_BG, ROW_BG_ACTIVE = "#1b222a", "#20301f"

    header = tk.Frame(root, bg=BG, padx=12, pady=10)
    header.pack(fill="x")
    tk.Label(header, text="EQL LOG READER", font=("Segoe UI", 12, "bold"),
             bg=BG, fg=ACCENT).pack(anchor="w")

    # -- active character / log file summary ------------------------------------
    log_frame = tk.Frame(root, bg=PANEL, padx=10, pady=8)
    log_frame.pack(fill="x", padx=10, pady=(0, 6))
    tk.Label(log_frame, text="Active character", font=("Segoe UI", 9, "bold"),
             bg=PANEL, fg=DIM).pack(anchor="w")
    path_lbl = tk.Label(log_frame, text="(none selected)", font=("Segoe UI", 9),
                        bg=PANEL, fg=FG, anchor="w", justify="left", wraplength=380)
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
            settings["active_character_id"] = ""
            settings["inventory_path"] = ""
            settings.save()
            refresh_path_label()
            rebuild_roster()

    btn_row = tk.Frame(log_frame, bg=PANEL)
    btn_row.pack(anchor="w")
    tk.Button(btn_row, text="Browse...", command=browse_log).pack(side="left")
    refresh_path_label()

    # -- character roster ------------------------------------------------------
    chars_frame = tk.Frame(root, bg=PANEL, padx=10, pady=8)
    chars_frame.pack(fill="x", padx=10, pady=(0, 6))

    chars_header = tk.Frame(chars_frame, bg=PANEL)
    chars_header.pack(fill="x")
    tk.Label(chars_header, text="Characters", font=("Segoe UI", 9, "bold"),
             bg=PANEL, fg=DIM).pack(side="left")
    hidden_btn = tk.Button(chars_header, text="Hidden (0)", font=("Segoe UI", 8))
    hidden_btn.pack(side="right")
    auto_btn = tk.Button(chars_header, text="Auto-detect")
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
                     padx=16, pady=16).pack()
            return
        for cid in hidden:
            name, _, server = cid.partition("_")
            row = tk.Frame(win, bg=PANEL, padx=10, pady=4)
            row.pack(fill="x")
            tk.Label(row, text=f"{name}  ({server})", bg=PANEL, fg=FG,
                     anchor="w").pack(side="left", fill="x", expand=True)
            tk.Button(row, text="Unhide", command=lambda c=cid: unhide_character(c)
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
                     bg=PANEL, fg=DIM, font=("Segoe UI", 8), wraplength=380,
                     justify="left").pack(anchor="w")
            return
        for entry in visible:
            is_active = entry["id"] == active_cid
            row = tk.Frame(roster_list, bg=ROW_BG_ACTIVE if is_active else ROW_BG,
                           padx=8, pady=5)
            row.pack(fill="x", pady=2)
            label_text = f"{'● ' if is_active else ''}{entry['name']}  ({entry['server']})"
            if entry.get("inventory_path"):
                label_text += "   🎒"
            tk.Label(row, text=label_text, bg=row["bg"],
                     fg=ACCENT if is_active else FG, anchor="w",
                     font=("Segoe UI", 9, "bold" if is_active else "normal")
                     ).pack(side="left", fill="x", expand=True)
            tk.Button(row, text="Hide", font=("Segoe UI", 8),
                     command=lambda c=entry["id"]: hide_character(c)).pack(side="right")
            tk.Button(row, text="Select", font=("Segoe UI", 8), width=7,
                     command=lambda e=entry: select_character(e)
                     ).pack(side="right", padx=(0, 4))

    def auto_detect():
        rebuild_roster()

    auto_btn.config(command=auto_detect)

    rebuild_roster()

    # -- tool rows -----------------------------------------------------------
    tools_frame = tk.Frame(root, bg=BG)
    tools_frame.pack(fill="x")
    rows = [ToolRow(tools_frame, spec, get_log_path) for spec in TOOLS]

    footer = tk.Label(
        root,
        text="eql_overlay_common.py, eql_combat_tracker.py, and eql_spell_db.py are\n"
             "shared code used by the tools above -- nothing to start/stop for those.",
        font=("Segoe UI", 8), bg=BG, fg=DIM, justify="left")
    footer.pack(anchor="w", padx=12, pady=(4, 10))

    def poll_status():
        for row in rows:
            row.refresh()
        root.after(1000, poll_status)

    poll_status()
    root.mainloop()


if __name__ == "__main__":
    main()
