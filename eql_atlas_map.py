#!/usr/bin/env python3
"""
EQL Atlas -- Map Window (Phase 2)
==================================
The Atlas collector's map view: renders the current zone's map files with
the player's live position on top, plus everything the collector knows --
loot/kill/coin events where they happened, channel-command notes, and the
baseline's named-mob spawn points colored by corroboration state.

Map sources: standard EQ map text files (`L x1,y1,z1, x2,y2,z2, r,g,b`
line segments and `P x,y,z, r,g,b, size, label` points), layered as
<zone>.txt + <zone>_1/_2/_3.txt. Search order prefers maps/brewall/ over
the game's sparse default maps/ -- drop Brewall's pack in that subfolder
and every classic zone renders.

Coordinates: /loc prints world (Y, X, Z); map files store
(x, y) = (-worldX, -worldY) with z kept as true world elevation. All
collector data is stored in world coords, so the flip happens exactly
once, in _w2c().

3D: the base map files keep real z, which the in-game map can't show --
we can. The projection is a center-relative orbit camera:

    rotate (x, y) around the zone center by yaw, then tilt:
    screen_x = rx
    screen_y = ry*cos(pitch) - z*Z_EXAG*sin(pitch)

pitch = 0 IS the flat 2D map (identical to before), so 2D is just the
camera pointing straight down. The [3D] toggle tilts it; right-drag
orbits (yaw/pitch); depth-shading fades deeper geometry toward the
background in both modes, so stacked dungeon floors finally read apart.

Layers (toggleable): map lines, labels, loot drops, kills, coin, notes,
named spawns (hollow ring = baseline claim you haven't confirmed, solid =
confirmed by your kills, star = novel -- observed named the baseline
doesn't know, like EQL's custom dark elves), your movement trail, and the
live position marker with a movement-derived heading arrow.

View controls (top bar): [Fit] reframes; [3D] tilts the camera (right-drag
orbits); [ghost] chroma-keys the background away so only the drawing
floats over the game; [follow] keeps the player centered (a manual pan
switches it off); [floor] slices the map to one story's z-band around the
player's own elevation -- walking downstairs re-slices automatically, and
the ▲/▼ arrows peek at other stories. Death spots are marked with a red ✕
naming the killer; the collector clears the movement trail on death so no
line ever connects a corpse to its respawn point.

Not a standalone tool -- eql_atlas.py owns this window (right-click the
Atlas panel -> Map window).
"""

import math
import os
import time
import tkinter as tk
from tkinter import font as tkfont

from eql_overlay_common import luma

DATA_REFRESH_S = 5          # how often event/named pins re-read the DB
TRAIL_SHOW = 60             # trail points drawn (newest)
NAMED_MIN_EVENTS = 1        # placed events needed to pin a novel named
Z_EXAG = 2.0                # z is small next to x/y spans -- exaggerate it
ORBIT_REDRAW_MS = 20        # throttle for redraws while orbiting/zooming
POOL_DECIMATE_AT = 6000     # above this many segments, orbit on a subset
DEPTH_FADE = 0.55           # how much the deepest geometry fades to bg
ZBAND = 20                  # floor filter half-height: one dungeon story

LAYER_DEFAULTS = {"map": True, "labels": True, "loot": True, "kills": False,
                  "coin": False, "notes": True, "named": True,
                  "deaths": True, "trail": True, "find": True,
                  "quest": True}

FIND_COLOR = "#ff8c1a"      # 'find' hit markers: orange in every theme,
                            # distinct from the loot/warn palette roles
QUEST_COLOR = "#b18aff"     # tracked-quest item markers: violet, apart
                            # from find's orange (same value as eql_quest)

PITCH_3D = math.radians(55)          # tilt when [3D] is switched on
YAW_3D = math.radians(30)


class ZoneMap:
    """One zone's parsed map geometry, in map coords with true z:
    lines (x1,y1,z1,x2,y2,z2,rgb), points (x,y,z,rgb,label)."""

    def __init__(self, short):
        self.short = short
        self.lines = []
        self.points = []
        self.bounds = None              # (minx, miny, maxx, maxy)
        self.zbounds = (0.0, 0.0)

    @classmethod
    def load(cls, search_dirs, short):
        """Load <short>.txt (+ _1/_2/_3 layers) from the first dir that has
        the base file. Returns None when no source exists for the zone.

        Layer files are only merged when their geometry fits inside the
        base map's footprint (with margin): Brewall draws some dungeons a
        second time as a 'poster' layer -- every floor separated out
        side-by-side (z stamped 0, extents several times the real zone).
        Great on the in-game map, but it can never line up with pins at
        true world coordinates, so those layers are skipped."""
        for d in search_dirs:
            base = os.path.join(d, f"{short}.txt")
            if not os.path.isfile(base):
                continue
            zm = cls(short)
            zm._parse(base)
            if not (zm.lines or zm.points):
                continue
            zm._compute_bounds()
            bx0, by0, bx1, by1 = zm.bounds
            mx = (bx1 - bx0) * 0.3 + 50
            my = (by1 - by0) * 0.3 + 50
            for i in (1, 2, 3):
                path = os.path.join(d, f"{short}_{i}.txt")
                if not os.path.isfile(path):
                    continue
                extra = cls(short)
                extra._parse(path)
                if not (extra.lines or extra.points):
                    continue
                extra._compute_bounds()
                ex0, ey0, ex1, ey1 = extra.bounds
                if (ex0 >= bx0 - mx and ey0 >= by0 - my
                        and ex1 <= bx1 + mx and ey1 <= by1 + my):
                    zm.lines += extra.lines
                    zm.points += extra.points
            zm._compute_bounds()
            return zm
        return None

    def _parse(self, path):
        try:
            with open(path, "r", encoding="cp1252", errors="replace") as f:
                for raw in f:
                    kind = raw[:1]
                    if kind not in ("L", "P"):
                        continue
                    parts = [p.strip() for p in raw[1:].split(",")]
                    try:
                        if kind == "L" and len(parts) >= 9:
                            x1, y1, z1, x2, y2, z2 = map(float, parts[:6])
                            rgb = tuple(int(float(c)) for c in parts[6:9])
                            self.lines.append((x1, y1, z1, x2, y2, z2, rgb))
                        elif kind == "P" and len(parts) >= 8:
                            x, y, z = (float(parts[0]), float(parts[1]),
                                       float(parts[2]))
                            rgb = tuple(int(float(c)) for c in parts[3:6])
                            label = parts[7].replace("_", " ")
                            self.points.append((x, y, z, rgb, label))
                    except ValueError:
                        continue        # malformed row: skip, keep the map
        except OSError:
            pass

    def _compute_bounds(self):
        xs, ys, zs = [], [], []
        for x1, y1, z1, x2, y2, z2, _ in self.lines:
            xs += (x1, x2)
            ys += (y1, y2)
            zs += (z1, z2)
        for x, y, z, _, _ in self.points:
            xs.append(x)
            ys.append(y)
            zs.append(z)
        self.bounds = (min(xs), min(ys), max(xs), max(ys))
        self.zbounds = (min(zs), max(zs))

    def center(self):
        minx, miny, maxx, maxy = self.bounds
        z0, z1 = self.zbounds
        return ((minx + maxx) / 2, (miny + maxy) / 2, (z0 + z1) / 2)


def _hex_readable(rgb, bg_is_dark=True):
    """Map-file colors assume the game's parchment map window; on our dark
    canvas, near-black lines (the most common color) would vanish. Lift
    anything too dark into a readable gray, keep real colors as they are."""
    r, g, b = rgb
    color = f"#{r:02x}{g:02x}{b:02x}"
    if bg_is_dark and luma(color) < 70:
        return "#9a93a8"
    return color


def _mix(c1, c2, t):
    """Blend '#rrggbb' colors: t=0 -> c1, t=1 -> c2."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


class AtlasMapWindow:
    """Decorated, resizable, always-on-top map window. Left-drag pans,
    wheel zooms about the cursor, right-drag orbits (in 3D), Fit reframes,
    [3D] tilts the camera."""

    def __init__(self, parent, settings, save_settings, theme_of, map_dirs):
        self.settings = settings
        self.save_settings = save_settings
        self.theme_of = theme_of
        self.map_dirs = map_dirs
        self.zone_short = None
        self.zone_long = ""
        self.zmap = None
        self.s, self.ox, self.oy = 1.0, 0.0, 0.0
        self.yaw = 0.0
        self.pitch = 0.0
        self._c0 = (0.0, 0.0, 0.0)      # rotation center (zone center)
        self._last_data = 0.0
        self._ctx = None                # (tracker, db, baseline) from tick
        self._redraw_after = None
        # base-geometry item pool: line items are created ONCE per
        # (zone, floor band, theme) and then only MOVED via coords() each
        # frame -- delete/create per frame is what makes Tk canvases chug
        self._pre = []                  # center-relative endpoints + color
        self._pool = []                 # (dx1,dy1,dz1, dx2,dy2,dz2, item id)
        self._pool_key = None
        self._pool_step = 1
        self._orbiting = False
        self._theme_rev = 0
        self._layers = dict(LAYER_DEFAULTS)
        self._layers.update(settings.get("map_layers", {}))
        self._layer_vars = {}

        self.top = tk.Toplevel(parent)
        self.top.title("EQL Atlas -- Map")
        # pinned = always-on-top overlay floating over the game;
        # unpinned = ordinary window you alt-tab to when needed
        self.top.attributes("-topmost", bool(settings.get("map_pin", True)))
        self.top.geometry(settings.get("map_geom", "760x600+120+80"))
        self.top.protocol("WM_DELETE_WINDOW", self.hide)

        # title row: collapse chevron + zone name + close. It doubles as
        # the drag handle when ghost mode strips the OS title bar. All
        # controls live BELOW it (ctrl row + layers row) and collapse up
        # into it via the chevron.
        self.bar = tk.Frame(self.top, cursor="fleur")
        self.bar.pack(fill="x")
        # pack order = squeeze priority: the ✕ / – buttons and the chevron
        # are packed BEFORE the zone label, so a narrow window truncates
        # the zone text instead of pushing the buttons off the edge (the
        # label also falls back to the short zone name -- see
        # _fit_zone_label)
        self.col_btn = tk.Label(self.bar, text="▾", cursor="hand2")
        self.col_btn.pack(side="left", padx=(6, 0))
        self.col_btn.bind("<Button-1>", lambda e: self._toggle_collapse())
        self.close_lbl = tk.Label(self.bar, text=" ✕ ", cursor="hand2")
        self.close_lbl.pack(side="right", padx=2)
        self.close_lbl.bind("<Button-1>", lambda e: self.hide())
        self.min_btn = tk.Label(self.bar, text=" – ", cursor="hand2")
        self.min_btn.pack(side="right")
        self.min_btn.bind("<Button-1>", lambda e: self._toggle_min())
        self.zone_lbl = tk.Label(self.bar, text=" no zone", anchor="w")
        self.zone_lbl.pack(side="left", padx=4)
        self._zone_full = " no zone"     # preferred label text
        self._zone_brief = " no zone"    # short-name fallback when narrow
        self._tdrag = None
        for w in (self.bar, self.zone_lbl):
            w.bind("<ButtonPress-1>", self._title_press)
            w.bind("<B1-Motion>", self._title_move)
            w.bind("<ButtonRelease-1>", lambda e: self.save_settings())

        # controls live in flow containers: nothing is packed -- a measuring
        # reflow (see _reflow) grids them left-to-right and WRAPS overflow
        # onto extra lines whenever the window is too narrow to show a
        # widget fully, instead of clipping it off the edge
        self.ctrl = tk.Frame(self.top)
        self.ctrl.pack(fill="x")
        self.bar2 = tk.Frame(self.top)
        self.bar2.pack(fill="x")
        self.fit_btn = tk.Button(self.ctrl, text="Fit", relief="flat",
                                 command=self.fit)
        self.d3_var = tk.BooleanVar(value=bool(settings.get("map_3d")))
        self.d3_check = tk.Checkbutton(self.ctrl, text="3D",
                                       variable=self.d3_var,
                                       command=self._toggle_3d)
        self.ghost_var = tk.BooleanVar(value=bool(settings.get("map_ghost")))
        self.ghost_check = tk.Checkbutton(self.ctrl, text="ghost",
                                          variable=self.ghost_var,
                                          command=self._toggle_ghost)
        self.pin_var = tk.BooleanVar(value=bool(settings.get("map_pin", True)))
        self.pin_check = tk.Checkbutton(self.ctrl, text="pin",
                                        variable=self.pin_var,
                                        command=self._toggle_pin)
        self.follow_var = tk.BooleanVar(value=bool(settings.get("map_follow")))
        self.follow_check = tk.Checkbutton(self.ctrl, text="follow",
                                           variable=self.follow_var,
                                           command=self._toggle_follow)
        # floor filter: banded around the player's z (auto), nudge with the
        # arrows to peek one story up/down; re-checking resets the nudge
        self._floor_off = 0
        self._zref_last = None
        self._last_band = None
        self.floor_var = tk.BooleanVar(value=bool(settings.get("map_floor")))
        self.floor_check = tk.Checkbutton(self.ctrl, text="floor",
                                          variable=self.floor_var,
                                          command=self._toggle_floor)
        self.floor_dn = tk.Button(self.ctrl, text="▼", relief="flat", padx=2,
                                  command=lambda: self._floor_nudge(-1))
        self.floor_up = tk.Button(self.ctrl, text="▲", relief="flat", padx=2,
                                  command=lambda: self._floor_nudge(+1))
        self.floor_lbl = tk.Label(self.ctrl, text="")
        self.lock_var = tk.BooleanVar(value=bool(settings.get("map_lock")))
        self.lock_check = tk.Checkbutton(self.ctrl, text="lock",
                                         variable=self.lock_var,
                                         command=self._toggle_lock)
        self._ctrl_widgets = [self.floor_check, self.floor_dn, self.floor_up,
                              self.floor_lbl, self.follow_check,
                              self.pin_check, self.ghost_check,
                              self.d3_check, self.lock_check, self.fit_btn]
        self.checks = []
        self._layer_widgets = []
        for key in LAYER_DEFAULTS:
            var = tk.BooleanVar(value=self._layers[key])
            cb = tk.Checkbutton(self.bar2, text=key, variable=var,
                                command=lambda k=key: self._toggle(k))
            self._layer_vars[key] = var
            self.checks.append(cb)
            self._layer_widgets.append(cb)
        self._reflow_pending = None
        # all toggles render as CHIPS (indicatoron=False): lit accent when
        # ON, quiet panel tone when OFF. The native Windows indicator
        # paints selectcolor in BOTH states, so a bright selectcolor read
        # as "always on" -- chips make state unmistakable instead.
        self._check_pairs = ([(self.floor_check, self.floor_var),
                              (self.follow_check, self.follow_var),
                              (self.pin_check, self.pin_var),
                              (self.ghost_check, self.ghost_var),
                              (self.d3_check, self.d3_var),
                              (self.lock_check, self.lock_var)]
                             + list(zip(self.checks,
                                        (self._layer_vars[k]
                                         for k in LAYER_DEFAULTS))))
        for cb, var in self._check_pairs:
            cb.configure(indicatoron=False, relief="flat", bd=0,
                         cursor="hand2", offrelief="flat",
                         overrelief="flat", highlightthickness=0)
            var.trace_add("write", lambda *_a: self._restyle_checks())

        self.canvas = tk.Canvas(self.top, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.mono = tkfont.Font(family="Consolas", size=9)
        self._gdrag = None
        self.grip = tk.Label(self.top, text=" ◢ ", cursor="size_nw_se")
        self.grip.bind("<ButtonPress-1>", self._grip_press)
        self.grip.bind("<B1-Motion>", self._grip_move)
        self.grip.bind("<ButtonRelease-1>", lambda e: self.save_settings())

        self.canvas.bind("<ButtonPress-1>", self._pan_start)
        self.canvas.bind("<B1-Motion>", self._pan_move)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<ButtonPress-3>", self._orbit_start)
        self.canvas.bind("<B3-Motion>", self._orbit_move)
        self.canvas.bind("<ButtonRelease-3>", self._orbit_end)
        # hover tooltips: named pins and event dots are icon-only; details
        # (name, respawn, drops + rates) appear on hover in a
        # contrast-flipped box so they're readable on any skin
        self._hover = {}
        self._nav = None
        self._nav_zone = None
        self._guide_path = None
        self._guide_key = None
        for tag in ("named", "events"):
            self.canvas.tag_bind(tag, "<Enter>", self._show_tip)
            self.canvas.tag_bind(tag, "<Leave>",
                                 lambda e: self.canvas.delete("tip"))
        self.top.bind("<Configure>", self._on_configure)
        self._drag = None
        self._orbit = None
        if self.d3_var.get():
            self.pitch = math.radians(settings.get("map_pitch", 55))
            self.yaw = math.radians(settings.get("map_yaw", 30))
        self._apply_collapse()
        self.apply_theme()
        self._apply_min()

    def _show_tip(self, e):
        try:
            self.canvas.delete("tip")
            cur = self.canvas.find_withtag("current")
            lines = self._hover.get(cur[0]) if cur else None
            if not lines:
                return
            dark = luma(self._th["bg"]) < 128
            bg, fg = ("#f4f1e8", "#15121e") if dark else ("#15121e",
                                                          "#f4f1e8")
            lh = self.mono.metrics("linespace") + 2
            w = max(self.mono.measure(t) for t in lines) + 12
            h = lh * len(lines) + 8
            x = max(2, min(e.x + 16, self.canvas.winfo_width() - w - 2))
            y = max(2, min(e.y + 12, self.canvas.winfo_height() - h - 2))
            if x <= e.x <= x + w and y <= e.y <= y + h:
                y = max(2, e.y - h - 12)   # clamped under cursor: go above
            # state="disabled" is load-bearing: it makes tip items
            # invisible to event picking. Without it the tip becomes the
            # 'current' item, firing <Leave> on the pin, which deletes the
            # tip, which re-fires <Enter>... an infinite create/delete
            # storm that livelocks the whole overlay process.
            self.canvas.create_rectangle(x, y, x + w, y + h, fill=bg,
                                         outline=fg, tags="tip",
                                         state="disabled")
            for i, t in enumerate(lines):
                self.canvas.create_text(x + 6, y + 4 + i * lh, text=t,
                                        anchor="nw", font=self.mono,
                                        fill=fg, tags="tip",
                                        state="disabled")
            self.canvas.tag_raise("tip")
        except Exception:
            self.canvas.delete("tip")   # a broken tip must never take the
                                        # overlay down with it

    # -- title-row dragging (the only handle once ghost drops the OS bar) ----
    def _title_press(self, e):
        self._tdrag = (e.x_root - self.top.winfo_x(),
                       e.y_root - self.top.winfo_y())

    def _title_move(self, e):
        if self._tdrag:
            self.top.geometry(f"+{e.x_root - self._tdrag[0]}"
                              f"+{e.y_root - self._tdrag[1]}")

    # -- collapsing the control rows up into the title row -------------------
    def _apply_collapse(self):
        collapsed = bool(self.settings.get("map_collapsed"))
        self.col_btn.config(text="  ▸  " if collapsed else "  ▾  ")
        if collapsed or not self.canvas.winfo_manager():
            self.ctrl.pack_forget()
            self.bar2.pack_forget()
        else:
            self.ctrl.pack(fill="x", before=self.canvas)
            self.bar2.pack(fill="x", before=self.canvas)

    def _toggle_collapse(self):
        self.settings["map_collapsed"] = not self.settings.get("map_collapsed")
        self.save_settings()
        self._apply_collapse()

    # -- minimize: fold the whole window down to the title row ---------------
    def _apply_min(self):
        mini = bool(self.settings.get("map_min"))
        self.min_btn.config(text=" □ " if mini else " – ")
        if mini:
            self.ctrl.pack_forget()
            self.bar2.pack_forget()
            self.canvas.pack_forget()
            self.grip.place_forget()
            # shrink the WINDOW to the title bar too: the geometry was set
            # explicitly, so unpacking the children alone leaves the empty
            # body as a blank box (ghost hid it via the chroma key; plain
            # themes showed it). _on_configure skips saving while
            # minimized, so map_geom keeps the real size for restore.
            self.top.update_idletasks()
            self.top.geometry(f"{self.top.winfo_width()}"
                              f"x{self.bar.winfo_reqheight()}")
        else:
            self.canvas.pack(fill="both", expand=True)
            self._apply_collapse()
            self._apply_frame()          # re-place the grip if borderless
            # restore the pre-minimize SIZE at the current position (the
            # bar may have been dragged while minimized)
            size = self.settings.get("map_geom", "760x600").split("+")[0]
            pos = self.top.geometry().split("+", 1)
            if len(pos) == 2:
                self.top.geometry(f"{size}+{pos[1]}")

    def _toggle_min(self):
        self.settings["map_min"] = not self.settings.get("map_min")
        self.save_settings()
        self._apply_min()

    # -- window plumbing ------------------------------------------------------
    def visible(self):
        return self.top.winfo_exists() and self.top.state() == "normal"

    def show(self):
        self.top.deiconify()
        self.settings["map_open"] = True
        self.save_settings()
        # canvas size isn't real until the window is mapped -- frame the
        # zone once geometry has settled
        self.top.after(150, self.fit)

    def hide(self):
        self.top.withdraw()
        self.settings["map_open"] = False
        self.save_settings()

    def toggle(self):
        self.hide() if self.visible() else self.show()

    def _on_configure(self, e):
        if e.widget is self.top:
            # the minimized (bar-only) size must never overwrite the real
            # geometry, or restore would come back bar-sized
            if not self.settings.get("map_min"):
                self.settings["map_geom"] = self.top.geometry()
            # saved on close/drag elsewhere; don't hammer the disk here
            self._schedule_reflow()

    # -- flow layout for the control rows -------------------------------------
    def _reflow(self, frame, widgets):
        """Grid `widgets` left-to-right, wrapping to the next line whenever
        the next one wouldn't fit fully (checkbox AND its text)."""
        avail = frame.winfo_width()
        if avail <= 10:                  # frame not realized yet
            avail = max(self.top.winfo_width() - 8, 240)
        x = row = col = 0
        for w in widgets:
            need = w.winfo_reqwidth()
            if col and x + need > avail:
                row += 1
                x = col = 0
            w.grid(row=row, column=col, sticky="w")
            x += need
            col += 1

    def _reflow_all(self):
        self._reflow_pending = None
        # resizing streams Configure events; re-gridding every chip each
        # time flickers -- skip entirely when the width hasn't moved
        w = self.top.winfo_width()
        if w == getattr(self, "_reflow_w", None):
            return
        self._reflow_w = w
        ctrl = list(self._ctrl_widgets)
        if self.ghost_var.get():         # ghost implies pinned: hide "pin"
            self.pin_check.grid_forget()
            ctrl.remove(self.pin_check)
        self._reflow(self.ctrl, ctrl)
        self._reflow(self.bar2, self._layer_widgets)
        self._fit_zone_label()

    def _fit_zone_label(self):
        """Long zone titles yield to the window: when the full 'Long Name
        (short)  [N lines]' text can't fit beside the bar buttons, fall
        back to just the short zone name -- the – / ✕ buttons must stay
        visible and clickable at any width."""
        w = self.top.winfo_width()
        if w <= 10:                      # not realized yet: keep full text
            text = self._zone_full
        else:
            used = (self.col_btn.winfo_reqwidth()
                    + self.min_btn.winfo_reqwidth()
                    + self.close_lbl.winfo_reqwidth() + 22)
            text = (self._zone_full
                    if self.mono.measure(self._zone_full) <= w - used
                    else self._zone_brief)
        if self.zone_lbl.cget("text") != text:
            self.zone_lbl.config(text=text)

    def _schedule_reflow(self):
        if self._reflow_pending is None:
            self._reflow_pending = self.top.after(60, self._reflow_all)

    def apply_theme(self):
        th = self.theme_of()
        self._th = th
        self.top.configure(bg=th["bg"])
        for bar in (self.bar, self.ctrl, self.bar2):
            bar.configure(bg=th["panel"])
        self.zone_lbl.configure(bg=th["panel"], fg=th["accent"],
                                font=self.mono)
        self.col_btn.configure(bg=th["panel"], fg=th["dim"], font=self.mono)
        self.close_lbl.configure(bg=th["panel"], fg=th["dim"], font=self.mono)
        self.min_btn.configure(bg=th["panel"], fg=th["dim"], font=self.mono)
        self.floor_lbl.configure(bg=th["panel"], fg=th["dim"], font=self.mono)
        # roomy padding everywhere: these are click targets over a game,
        # not desktop widgets -- tiny hitboxes were genuinely hard to hit
        for btn in (self.fit_btn, self.floor_up, self.floor_dn):
            btn.configure(bg=th["panel"], fg=th["fg"], padx=10, pady=3,
                          activebackground=th["accent"],
                          activeforeground="#000000",
                          font=self.mono)
        # chips carry their own state colors -- see _restyle_checks. (And
        # never paint any surface the exact theme bg: in ghost mode that's
        # the chroma key, i.e. a click-through hole.)
        for cb, _v in self._check_pairs:
            cb.configure(font=self.mono, padx=8, pady=4)
        self._restyle_checks()
        self.grip.configure(bg=th["panel"], fg=th["dim"], font=self.mono)
        self.canvas.configure(bg=th["bg"])
        self._theme_rev += 1           # shade colors are baked into the pool
        self._apply_frame()
        self._reflow_w = None          # fonts/padding change widget widths
        self._schedule_reflow()
        self._render_full()

    # -- window chrome --------------------------------------------------------
    # Overlay states (pinned or ghost) strip the OS title bar entirely --
    # the zone-name row is the drag handle, the corner grip resizes, ✕
    # closes. Unpinned + non-ghost keeps the normal decorated window so
    # minimize / alt-tab / taskbar all work (borderless windows have none
    # of those). Toggling decorations on a mapped window needs a
    # withdraw/deiconify cycle to take effect.
    def _apply_frame(self):
        ghost = self.ghost_var.get()
        borderless = ghost or self.pin_var.get()
        try:
            if bool(self.top.overrideredirect()) != borderless:
                vis = self.visible()
                if vis:
                    self.top.withdraw()
                self.top.overrideredirect(borderless)
                if vis:
                    self.top.deiconify()
            # borderless windows must stay on top to be recoverable at all
            self.top.attributes("-topmost", borderless)
            self.top.attributes("-transparentcolor",
                                self._th["bg"] if ghost else "")
        except tk.TclError:
            pass                        # non-Windows: quietly stay decorated
        locked = bool(self.settings.get("map_lock"))
        try:
            self.top.resizable(not locked, not locked)
        except tk.TclError:
            pass
        if borderless and not locked:
            self.grip.place(relx=1.0, rely=1.0, anchor="se")
        else:
            self.grip.place_forget()

    def _toggle_lock(self):
        self.settings["map_lock"] = self.lock_var.get()
        self.save_settings()
        self._apply_frame()

    def _restyle_checks(self):
        """Recolor every toggle chip to match its actual state: accent
        background + dark text when ON, panel + dim text when OFF. Dark
        text means the theme's ink, NEVER its bg: on transparent themes
        bg is the chroma key, and key-colored text is a see-through hole
        -- unreadable over a bright game background."""
        th = getattr(self, "_th", None)
        if not th:
            return                      # traces fire during __init__ too
        # SOLID BLACK on the bright accent chips, in every theme: any
        # theme's bg can become the chroma key in ghost mode, so no text
        # may ever be painted in it
        for cb, var in self._check_pairs:
            on = bool(var.get())
            cb.configure(selectcolor=th["accent"], bg=th["panel"],
                         fg="#000000" if on else th["dim"],
                         activebackground=th["accent"],
                         activeforeground="#000000")

    def _toggle_ghost(self):
        self.settings["map_ghost"] = self.ghost_var.get()
        self.save_settings()
        self._apply_frame()
        self._reflow_w = None            # pin chip appears/disappears
        self._schedule_reflow()

    # -- corner resize grip (the OS frame is gone in overlay states) --------
    def _grip_press(self, e):
        self._gdrag = (e.x_root, e.y_root,
                       self.top.winfo_width(), self.top.winfo_height())

    def _grip_move(self, e):
        if not self._gdrag:
            return
        x0, y0, w0, h0 = self._gdrag
        w = max(280, w0 + e.x_root - x0)
        h = max(200, h0 + e.y_root - y0)
        self.top.geometry(f"{w}x{h}")

    def _toggle_follow(self):
        self.settings["map_follow"] = self.follow_var.get()
        self.save_settings()

    def _toggle_pin(self):
        self.settings["map_pin"] = self.pin_var.get()
        self.save_settings()
        self._apply_frame()

    # -- floor filter ---------------------------------------------------------
    def _toggle_floor(self):
        self._floor_off = 0             # re-checking resets the nudge
        self.settings["map_floor"] = self.floor_var.get()
        self.save_settings()
        self._render_full()

    def _floor_nudge(self, step):
        if not self.floor_var.get():
            self.floor_var.set(True)
            self.settings["map_floor"] = True
        old = self._floor_off
        self._floor_off += step
        zref = self._zref()
        if zref is not None and self.zmap:
            # don't step past the top/bottom story into empty air --
            # that showed a map of nothing but unfiltered dots. The band
            # center must stay inside the zone's real z-range, otherwise
            # the slice only grazes the extreme vertices.
            z0, z1 = self.zmap.zbounds
            if not (z0 <= zref <= z1):
                self._floor_off = old
                return
        self._render_full()

    def _zref(self):
        """Center of the visible z band, or None for 'show everything'.
        Auto: follows the player's own z (fresh /loc), so walking down a
        story re-slices the map by itself; the arrows peek other stories."""
        if not self.floor_var.get():
            return None
        tracker = self._ctx[0] if self._ctx else None
        if tracker and tracker.loc and time.time() - tracker.loc[3] <= 120:
            self._zref_last = tracker.loc[2]
        if self._zref_last is None:      # no position yet: nothing to slice
            return None
        return self._zref_last + self._floor_off * 2 * ZBAND

    def _zpass(self, mz, zref):
        return zref is None or abs(mz - zref) <= ZBAND

    def _toggle(self, key):
        self._layers[key] = self._layer_vars[key].get()
        self.settings["map_layers"] = dict(self._layers)
        self.save_settings()
        self._render_full()

    def _toggle_3d(self):
        on = self.d3_var.get()
        self.settings["map_3d"] = on
        if on:
            self.pitch = math.radians(self.settings.get("map_pitch", 55))
            self.yaw = math.radians(self.settings.get("map_yaw", 30))
        else:
            self.pitch = self.yaw = 0.0
        self.save_settings()
        self.fit()

    # -- projection -----------------------------------------------------------
    # Everything on the canvas goes through _project: map coords, relative
    # to the zone center, rotated by yaw, tilted by pitch, then scaled and
    # panned. pitch == 0 degenerates to the plain 2D map.
    def _project(self, mx, my, mz):
        dx = mx - self._c0[0]
        dy = my - self._c0[1]
        dz = (mz - self._c0[2]) * Z_EXAG
        if self.yaw:
            ca, sa = math.cos(self.yaw), math.sin(self.yaw)
            dx, dy = dx * ca - dy * sa, dx * sa + dy * ca
        if self.pitch:
            dy = dy * math.cos(self.pitch) - dz * math.sin(self.pitch)
        return dx * self.s + self.ox, dy * self.s + self.oy

    def _w2c(self, wy, wx, wz=0.0):
        """World (Y, X, Z) as /loc prints them -> canvas pixels. Map files
        store (x, y) = (-worldX, -worldY) -- verified against Brewall's
        Befallen, whose file y runs 62..1007 for a zone whose world Y runs
        +50..-1000. (Getting this backwards drew every pin sideways.)"""
        return self._project(-wx, -wy, wz)

    def _depth(self, mz):
        """0.0 (highest) .. 1.0 (deepest) within the zone's z range."""
        z0, z1 = self.zmap.zbounds if self.zmap else (0, 0)
        if z1 - z0 < 20:
            return 0.0
        return min(1.0, max(0.0, (z1 - mz) / (z1 - z0)))

    def _shaded(self, color, mz):
        """Deeper geometry fades toward the background -- stacked dungeon
        floors read apart, in 2D and 3D alike."""
        return _mix(color, self._th["bg"], self._depth(mz) * DEPTH_FADE)

    def fit(self):
        if not (self.zmap and self.zmap.bounds):
            self._render_full()
            return
        self._c0 = self.zmap.center()
        cw = max(self.canvas.winfo_width(), 100)
        ch = max(self.canvas.winfo_height(), 100)
        # project the 3D bounding box's corners at unit scale, no offset
        s, ox, oy = self.s, self.ox, self.oy
        self.s, self.ox, self.oy = 1.0, 0.0, 0.0
        minx, miny, maxx, maxy = self.zmap.bounds
        z0, z1 = self.zmap.zbounds
        xs, ys = [], []
        for mx in (minx, maxx):
            for my in (miny, maxy):
                for mz in (z0, z1):
                    px, py = self._project(mx, my, mz)
                    xs.append(px)
                    ys.append(py)
        self.s, self.ox, self.oy = s, ox, oy
        spanx = max(max(xs) - min(xs), 1)
        spany = max(max(ys) - min(ys), 1)
        self.s = min((cw - 40) / spanx, (ch - 40) / spany)
        self.ox = cw / 2 - (max(xs) + min(xs)) / 2 * self.s
        self.oy = ch / 2 - (max(ys) + min(ys)) / 2 * self.s
        self._render_full()

    # -- input ----------------------------------------------------------------
    def _pan_start(self, e):
        self._drag = (e.x, e.y)

    def _pan_move(self, e):
        if not self._drag:
            return
        if self.follow_var.get():        # a manual pan takes the wheel back
            self.follow_var.set(False)
            self._toggle_follow()
        dx, dy = e.x - self._drag[0], e.y - self._drag[1]
        self._drag = (e.x, e.y)
        self.ox += dx
        self.oy += dy
        self.canvas.move("all", dx, dy)

    def _wheel(self, e):
        f = 1.15 if e.delta > 0 else 1 / 1.15
        self.s *= f
        self.ox = e.x + (self.ox - e.x) * f
        self.oy = e.y + (self.oy - e.y) * f
        self._schedule_render()

    def _orbit_start(self, e):
        if self.d3_var.get():
            self._orbit = (e.x, e.y)
            self._orbiting = True
            if self._pool_step > 1:
                # huge zone: orbit on every Nth segment, full detail on release
                self.canvas.itemconfigure("bfull", state="hidden")

    def _orbit_move(self, e):
        if not self._orbit:
            return
        dx, dy = e.x - self._orbit[0], e.y - self._orbit[1]
        self._orbit = (e.x, e.y)
        self.yaw += dx * 0.008
        self.pitch = min(math.radians(85),
                         max(0.0, self.pitch + dy * 0.008))
        self.settings["map_pitch"] = round(math.degrees(self.pitch))
        self.settings["map_yaw"] = round(math.degrees(self.yaw))
        self._schedule_render()

    def _orbit_end(self, _e):
        self._orbit = None
        if self._orbiting:
            self._orbiting = False
            self.canvas.itemconfigure("bfull", state="normal")
            self._render_full()
        self.save_settings()

    def _schedule_render(self):
        """Coalesce redraws while the mouse streams motion events."""
        if self._redraw_after is None:
            self._redraw_after = self.top.after(ORBIT_REDRAW_MS,
                                                self._render_full)

    # -- zone -----------------------------------------------------------------
    def set_zone(self, short, long_name):
        self.zone_short = short
        self.zone_long = long_name or short or ""
        if not short:
            self._zone_full = " zone unknown -- /who will sync it"
            self._zone_brief = " no zone"
            self._fit_zone_label()
            self.canvas.delete("all")
            self.zmap = None
            return
        self.zmap = ZoneMap.load(self.map_dirs, short)
        got = (f"{len(self.zmap.lines)} lines" if self.zmap
               else "no map file -- install Brewall's pack to maps\\brewall")
        self._zone_full = f" {self.zone_long} ({short})  [{got}]"
        self._zone_brief = f" {short}"
        self._fit_zone_label()
        self.canvas.delete("all")
        self._pool_key = None          # pooled items died with the canvas
        self._pool = []
        if self.zmap:
            self.fit()

    # -- rendering --------------------------------------------------------------
    def tick(self, tracker, db, baseline):
        self._ctx = (tracker, db, baseline)
        if not self.visible() or self.settings.get("map_min"):
            return
        if tracker.zone_short != self.zone_short:
            self.set_zone(tracker.zone_short, tracker.zone_long)
        # auto floor filter: walking to another story re-slices the map
        if self.floor_var.get():
            zref = self._zref()
            changed = ((zref is None) != (self._last_band is None)
                       or (zref is not None and self._last_band is not None
                           and abs(zref - self._last_band) > ZBAND * 0.75))
            if changed:
                self._schedule_render()
        if time.time() - self._last_data >= DATA_REFRESH_S:
            self._draw_data()
            self._last_data = time.time()
        self._draw_live()

    def _render_full(self):
        if self._redraw_after is not None:
            try:
                self.top.after_cancel(self._redraw_after)
            except (tk.TclError, ValueError):
                pass
            self._redraw_after = None
        self._draw_base()
        self._draw_data()
        self._draw_live()
        self._last_data = time.time()

    def _ensure_pool(self, zref):
        """(Re)create the base line items only when the visible set changes
        (zone, floor band, layer toggle, theme) -- never per frame."""
        key = (self.zone_short, zref, self._layers["map"], self._theme_rev)
        if key == self._pool_key:
            return
        self._pool_key = key
        self.canvas.delete("base")
        self._pool = []
        if not (self._layers["map"] and self.zmap):
            return
        c0x, c0y, c0z = self._c0
        n_visible = sum(1 for x1, y1, z1, x2, y2, z2, _ in self.zmap.lines
                        if self._zpass(z1, zref) or self._zpass(z2, zref))
        self._pool_step = max(1, -(-n_visible // POOL_DECIMATE_AT))
        create_line = self.canvas.create_line
        i = 0
        for x1, y1, z1, x2, y2, z2, rgb in self.zmap.lines:
            # keep segments with EITHER end in band, so ramps and
            # stairwells still connect the visible story to its exits
            if not (self._zpass(z1, zref) or self._zpass(z2, zref)):
                continue
            color = self._shaded(_hex_readable(rgb), (z1 + z2) / 2)
            tags = ("base",) if i % self._pool_step == 0 else ("base", "bfull")
            iid = create_line(0, 0, 0, 0, tags=tags, fill=color)
            self._pool.append((x1 - c0x, y1 - c0y, (z1 - c0z) * Z_EXAG,
                               x2 - c0x, y2 - c0y, (z2 - c0z) * Z_EXAG, iid))
            i += 1
        self.canvas.tag_lower("base")

    def _update_base(self):
        """Per-frame path: rotate/tilt/scale the pooled items via coords().
        Pure arithmetic plus one Tk call per line -- no allocation."""
        if not self._pool:
            return
        ca, sa = math.cos(self.yaw), math.sin(self.yaw)
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        s, ox, oy = self.s, self.ox, self.oy
        coords = self.canvas.coords
        subset = self._orbiting and self._pool_step > 1
        step = self._pool_step
        for i, (dx1, dy1, dz1, dx2, dy2, dz2, iid) in enumerate(self._pool):
            if subset and i % step:
                continue
            coords(iid,
                   (dx1 * ca - dy1 * sa) * s + ox,
                   ((dx1 * sa + dy1 * ca) * cp - dz1 * sp) * s + oy,
                   (dx2 * ca - dy2 * sa) * s + ox,
                   ((dx2 * sa + dy2 * ca) * cp - dz2 * sp) * s + oy)

    def _draw_base(self):
        zref = self._zref()
        self._last_band = zref
        # label floors the way players count them -- "floor 2/3", numbered
        # from the top story down -- never raw z coordinates, which mean
        # nothing to someone who hasn't read the map file format
        if zref is not None and self.zmap:
            z0, z1 = self.zmap.zbounds
            stories = max(1, math.ceil((z1 - z0) / (2 * ZBAND)))
            floor = min(stories, max(1, 1 + round((z1 - zref) / (2 * ZBAND))))
            self.floor_lbl.config(text=f" floor {floor}/{stories} ")
        elif self.floor_var.get():
            self.floor_lbl.config(text=" floor: all ")
        else:
            self.floor_lbl.config(text="")
        self.canvas.delete("labels")
        if not self.zmap:
            self.canvas.delete("base")
            self._pool_key = None
            self._pool = []
            return
        self._ensure_pool(zref)
        self._update_base()
        if self._layers["labels"]:
            for x, y, z, rgb, label in self.zmap.points:
                if not self._zpass(z, zref):
                    continue
                cx, cy = self._project(x, y, z)
                self.canvas.create_text(cx, cy, text=label, tags="labels",
                                        fill=self._shaded(_hex_readable(rgb), z),
                                        font=self.mono, anchor="w")
        self.canvas.tag_lower("labels")
        self.canvas.tag_lower("base")

    def _dot(self, wy, wx, wz, r, tags, **kw):
        cx, cy = self._w2c(wy, wx, wz)
        return self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                                       tags=tags, **kw)

    def _draw_data(self):
        if not self._ctx:
            return
        tracker, db, baseline = self._ctx
        th = self._th
        self._hover = {}
        for tag in ("events", "notes", "named", "find", "quest", "tip"):
            self.canvas.delete(tag)
        z = db.data["zones"].get(self.zone_short)

        zref = self._zref()
        wpass = lambda wz: self._zpass(wz or 0, zref)

        if z:
            show = {"L": self._layers["loot"], "K": self._layers["kills"],
                    "C": self._layers["coin"], "D": self._layers["deaths"]}
            style = {"L": (3, th["alt"]), "K": (2, th["dim"]),
                     "C": (2, th["warn"])}
            for ev in z["events"]:
                kind, wy = ev[1], ev[4]
                if wy is None or not show.get(kind) or not wpass(ev[6]):
                    continue
                if kind == "D":
                    # death spot: an unmissable mark, labeled with the killer
                    cx, cy = self._w2c(ev[4], ev[5], ev[6] or 0)
                    self.canvas.create_text(
                        cx, cy, text="✕", tags="events", fill=th["bad"],
                        font=(self.mono.actual("family"), 12, "bold"))
                    self.canvas.create_text(cx + 9, cy, text=f"† {ev[2]}",
                                            tags="events", fill=th["bad"],
                                            font=self.mono, anchor="w")
                    continue
                r, color = style[kind]
                iid = self._dot(ev[4], ev[5], ev[6] or 0, r, "events",
                                fill=color, outline="")
                if ev[2]:               # hover: what fell here, from whom,
                    mrec = z["mobs"].get(ev[2].lower())   # and its rates
                    tip = ([ev[3]] if ev[3] else []) + [f"<- {ev[2]}"]
                    if mrec:
                        k = mrec["kills"]
                        for item, d in sorted(
                                mrec["drops"].items(),
                                key=lambda kv: -kv[1]["count"])[:5]:
                            rate = (f"{d['count']}/{k}k" if k
                                    else f"x{d['count']}")
                            tip.append(f"  {item}  {rate}")
                    self._hover[iid] = tip
            # 'find <item|npc>' hits: orange rings on every spot the item
            # was looted OR the matching mob/NPC was killed in this zone
            # ('find off' clears them)
            if self._layers["find"] and getattr(tracker, "find_query", None):
                term = tracker.find_query[0]
                excl = getattr(tracker, "find_exclude", ())
                for ev in z["events"]:
                    subj = (ev[3] if ev[1] == "L"
                            else ev[2] if ev[1] == "K" else None)
                    if (subj is None or ev[4] is None
                            or term not in subj.lower()
                            or not wpass(ev[6])
                            or any(x in subj.lower() for x in excl)):
                        continue
                    cx, cy = self._w2c(ev[4], ev[5], ev[6] or 0)
                    self.canvas.create_oval(cx - 7, cy - 7, cx + 7, cy + 7,
                                            tags="find", outline=FIND_COLOR,
                                            width=2)
            # tracked quest (Quest window): violet diamonds on every spot
            # a still-missing quest item has dropped in this zone. Exact
            # name match -- quest items are specific, not substrings.
            if self._layers["quest"] and getattr(tracker, "quest_marks",
                                                 None):
                marks = tracker.quest_marks
                for ev in z["events"]:
                    if (ev[1] != "L" or ev[4] is None or not ev[3]
                            or ev[3].lower() not in marks
                            or not wpass(ev[6])):
                        continue
                    cx, cy = self._w2c(ev[4], ev[5], ev[6] or 0)
                    iid = self.canvas.create_polygon(
                        cx, cy - 8, cx + 8, cy, cx, cy + 8, cx - 8, cy,
                        tags=("quest", "events"), outline=QUEST_COLOR,
                        fill="", width=2)
                    self._hover[iid] = [f"{ev[3]}  (quest item)",
                                        f"<- {ev[2]}"]
            if self._layers["notes"]:
                for note in z.get("notes", []):
                    if not note.get("loc"):
                        continue
                    wy, wx, wz = (note["loc"] + [0])[:3]
                    if not wpass(wz):
                        continue
                    self._dot(wy, wx, wz or 0, 4, "notes",
                              fill=th["accent"], outline=th["bg"])
                    cx, cy = self._w2c(wy, wx, wz or 0)
                    self.canvas.create_text(cx + 7, cy, text=note["text"],
                                            tags="notes", fill=th["accent"],
                                            font=self.mono, anchor="w")

        # 'find' also marks matching mobs/NPCs at their baseline spawn
        # points -- quest givers and named you haven't met yet included
        # (independent of observed data, so it works in a fresh zone)
        if (self._layers["find"] and baseline.ok and self.zone_short
                and getattr(tracker, "find_query", None)):
            term = tracker.find_query[0]
            excl = getattr(tracker, "find_exclude", ())
            for key, rec in baseline.npcs.get(self.zone_short, {}).items():
                if term not in key or any(x in key for x in excl):
                    continue
                tip = [rec["name"], "spawn point (map data)"]
                for s in rec["spawns"]:
                    if not wpass(s[2]):
                        continue
                    cx, cy = self._w2c(s[1], s[0], s[2])
                    iid = self.canvas.create_oval(
                        cx - 7, cy - 7, cx + 7, cy + 7,
                        tags=("find", "events"), outline=FIND_COLOR,
                        width=2, dash=(3, 3))
                    self._hover[iid] = tip

        # tracked quest's HAND-IN NPC: a violet labeled square at its
        # spawn point(s) whenever you're in the hand-in zone
        qnpc = getattr(tracker, "quest_npc", None)
        if (self._layers["quest"] and baseline.ok and qnpc
                and qnpc[1] == self.zone_short):
            rec = baseline.npcs.get(self.zone_short, {}).get(qnpc[0])
            for s in (rec["spawns"] if rec else ()):
                if not wpass(s[2]):
                    continue
                cx, cy = self._w2c(s[1], s[0], s[2])
                iid = self.canvas.create_rectangle(
                    cx - 6, cy - 6, cx + 6, cy + 6,
                    tags=("quest", "events"), outline=QUEST_COLOR, width=2)
                self.canvas.create_text(cx + 10, cy,
                                        text=f"hand in: {qnpc[2]}",
                                        tags="quest", fill=QUEST_COLOR,
                                        font=self.mono, anchor="w")
                self._hover[iid] = [f"hand in: {qnpc[2]}",
                                    "tracked quest turn-in"]

        if self._layers["named"] and baseline.ok and self.zone_short:
            observed = z["mobs"] if z else {}
            now = time.time()
            kill_events = {}
            if z:
                for ev in z["events"]:
                    if ev[1] == "K" and ev[2]:
                        kill_events.setdefault(ev[2].lower(), []).append(ev)

            def named_pin(wy, wx, wz, color, fill, width, tip):
                cx, cy = self._w2c(wy, wx, wz)
                iid = self.canvas.create_oval(cx - 6, cy - 6, cx + 6, cy + 6,
                                              tags="named", outline=color,
                                              fill=fill, width=width)
                self._hover[iid] = tip

            for key, rec in baseline.npcs.get(self.zone_short, {}).items():
                if not rec["named"]:
                    continue
                obs = observed.get(key)
                seen = obs and (obs["kills"] + obs["kills_group"]) > 0
                kls = kill_events.get(key, [])
                resp = min((s[4] for s in rec["spawns"]), default=0)
                last_k = max((e[0] for e in kls), default=0)
                # respawn watch: killed before + cycle elapsed = may be UP
                due = bool(seen and resp and last_k
                           and now - last_k >= resp)
                tip = [f"{rec['name']}  lvl {rec['level'][0]}-"
                       f"{rec['level'][1]}",
                       f"respawn ~{resp // 60}m"
                       + ("  ** MAY BE UP **" if due else "")]
                if obs:
                    tip.append(f"your kills: {obs['kills']}"
                               f" (+{obs['kills_group']})")
                for iid_, pct in rec["loot"][:6]:
                    nm = baseline.item_name(iid_) or f"item {iid_}"
                    tip.append(f"  {nm}  {pct:g}%")
                color = FIND_COLOR if due else (th["alt"] if seen
                                                else th["dim"])
                # refine the pin to where you ACTUALLY kill it once
                # enough placed kills exist -- DB spawn points can lie
                placed = [(e[4], e[5], e[6] or 0) for e in kls
                          if e[4] is not None]
                if len(placed) >= 3:
                    pts = [tuple(sum(c) / len(placed)
                                 for c in zip(*placed))]
                else:
                    pts = [(s[1], s[0], s[2]) for s in rec["spawns"]
                           if wpass(s[2])]
                for wy, wx, wz in pts:
                    named_pin(wy, wx, wz, color, color if seen else "",
                              3 if due else 2, tip)
            # novel named: observed, absent from baseline -- discoveries
            for key, mrec in observed.items():
                if (key.startswith(("a ", "an ", "the "))
                        or not mrec["name"][:1].isupper()
                        or baseline.mob(self.zone_short, key) is not None
                        or mrec["kills"] + mrec["kills_group"] == 0):
                    continue
                locs = [(e[4], e[5], e[6] or 0)
                        for e in kill_events.get(key, [])
                        if e[4] is not None]
                if len(locs) < NAMED_MIN_EVENTS:
                    continue
                wy, wx, wz = (sum(c) / len(locs) for c in zip(*locs))
                tip = [f"{mrec['name']}  ** NEW -- not in baseline **",
                       f"your kills: {mrec['kills']}"
                       f" (+{mrec['kills_group']})"]
                for item, d in sorted(mrec["drops"].items(),
                                      key=lambda kv: -kv[1]["count"])[:6]:
                    k = mrec["kills"]
                    r = f"  {d['count']}/{k}" if k else f"  x{d['count']}"
                    tip.append(f"  {item}{r}")
                named_pin(wy, wx, wz, th["warn"], th["warn"], 2, tip)

    def _draw_live(self):
        if not self._ctx:
            return
        tracker = self._ctx[0]
        th = self._th
        self.canvas.delete("live")
        if self._layers["trail"] and len(tracker.trail) >= 2:
            pts = list(tracker.trail)[-TRAIL_SHOW:]
            flat = []
            for wy, wx, wz, _t in pts:
                flat += self._w2c(wy, wx, wz)
            self.canvas.create_line(*flat, tags="live", fill=th["dim"],
                                    dash=(2, 3))
        # guide: ring the destination and draw the way there
        g = getattr(tracker, "guide", None)
        if g and g.get("target") and g.get("zone") == self.zone_short:
            ty, tx, tz = g["target"]
            gx, gy = self._w2c(ty, tx, tz)
            self.canvas.create_oval(gx - 9, gy - 9, gx + 9, gy + 9,
                                    tags="live", outline=FIND_COLOR, width=3)
            self.canvas.create_text(gx + 13, gy, text=g["label"], tags="live",
                                    fill=FIND_COLOR, font=self.mono,
                                    anchor="w")
            if tracker.loc and time.time() - tracker.loc[3] <= 120:
                # follow the map's own geometry to the target (A* along
                # the line graph); recompute only when we've moved
                key = (round(tracker.loc[0] / 15), round(tracker.loc[1] / 15),
                       g["target"])
                if key != self._guide_key:
                    self._guide_key = key
                    self._guide_path = self._nav_path(
                        (tracker.loc[0], tracker.loc[1], tracker.loc[2]),
                        g["target"])
                px, py = self._w2c(tracker.loc[0], tracker.loc[1],
                                   tracker.loc[2])
                flat = [px, py]
                for mx, my, mz in (self._guide_path or ()):
                    flat += self._project(mx, my, mz)
                flat += [gx, gy]
                self.canvas.create_line(*flat, tags="live",
                                        fill=FIND_COLOR, width=2,
                                        dash=(7, 4), arrow="last")
        # the player: accent dot + movement-derived heading arrow; follow
        # mode recenters the whole canvas on it every tick
        if tracker.loc and time.time() - tracker.loc[3] <= 120:
            wy, wx, wz = tracker.loc[0], tracker.loc[1], tracker.loc[2]
            cx, cy = self._w2c(wy, wx, wz)
            if self.follow_var.get():
                sx = self.canvas.winfo_width() / 2 - cx
                sy = self.canvas.winfo_height() / 2 - cy
                if abs(sx) > 1 or abs(sy) > 1:
                    self.ox += sx
                    self.oy += sy
                    self.canvas.move("all", sx, sy)
                    cx, cy = cx + sx, cy + sy
            self.canvas.create_oval(cx - 5, cy - 5, cx + 5, cy + 5,
                                    tags="live", fill=th["accent"],
                                    outline=th["bg"], width=2)
            pts = list(tracker.trail)[-8:]
            for prev in reversed(pts[:-1]):
                if (prev[0], prev[1]) != (wy, wx):
                    px, py = self._w2c(prev[0], prev[1], prev[2])
                    dx, dy = cx - px, cy - py
                    n = max((dx * dx + dy * dy) ** 0.5, 1e-6)
                    self.canvas.create_line(cx, cy, cx + dx / n * 16,
                                            cy + dy / n * 16, tags="live",
                                            fill=th["accent"], width=2,
                                            arrow="last")
                    break

    # -- guide pathfinding: A* along the map's own line graph ----------------
    def _nav_graph(self):
        if self._nav_zone == self.zone_short and self._nav:
            return self._nav
        nodes, adj = {}, {}
        if self.zmap:
            def nid(x, y, zz):
                k = (round(x / 8), round(y / 8), round(zz / 24))
                if k not in nodes:
                    nodes[k] = (x, y, zz)
                    adj[k] = set()
                return k
            for x1, y1, z1, x2, y2, z2, _ in self.zmap.lines:
                a, b = nid(x1, y1, z1), nid(x2, y2, z2)
                if a != b:
                    adj[a].add(b)
                    adj[b].add(a)
        self._nav = (nodes, adj)
        self._nav_zone = self.zone_short
        return self._nav

    def _nav_path(self, wfrom, wto):
        import heapq
        nodes, adj = self._nav_graph()
        if not nodes:
            return None

        def near(w):
            mx, my, mz = -w[1], -w[0], w[2]
            return min(nodes, key=lambda k: (nodes[k][0] - mx) ** 2
                       + (nodes[k][1] - my) ** 2
                       + ((nodes[k][2] - mz) * 2) ** 2)
        start, goal = near(wfrom), near(wto)
        gpt = nodes[goal]

        def h(k):
            n = nodes[k]
            return ((n[0] - gpt[0]) ** 2 + (n[1] - gpt[1]) ** 2) ** 0.5
        openq = [(h(start), 0.0, start, None)]
        came = {}
        best = {start: 0.0}
        while openq:
            _f, gsc, cur, par = heapq.heappop(openq)
            if cur in came:
                continue
            came[cur] = par
            if cur == goal:
                break
            a = nodes[cur]
            for nb in adj[cur]:
                b = nodes[nb]
                nd = gsc + ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2
                            + (a[2] - b[2]) ** 2) ** 0.5
                if nd < best.get(nb, 1e18):
                    best[nb] = nd
                    heapq.heappush(openq, (nd + h(nb), nd, nb, cur))
        if goal not in came:
            return None
        path, k = [], goal
        while k is not None:
            path.append(nodes[k])
            k = came[k]
        return path[::-1]
        if tracker.loc and time.time() - tracker.loc[3] <= 120:
            wy, wx, wz = tracker.loc[0], tracker.loc[1], tracker.loc[2]
            cx, cy = self._w2c(wy, wx, wz)
            # auto-follow: keep the player pinned to the canvas center
            if self.follow_var.get():
                tx = self.canvas.winfo_width() / 2 - cx
                ty = self.canvas.winfo_height() / 2 - cy
                if abs(tx) > 1 or abs(ty) > 1:
                    self.ox += tx
                    self.oy += ty
                    self.canvas.move("all", tx, ty)
                    cx, cy = cx + tx, cy + ty
            self.canvas.create_oval(cx - 5, cy - 5, cx + 5, cy + 5,
                                    tags="live", fill=th["accent"],
                                    outline=th["bg"], width=2)
            # heading from the last two distinct trail points
            pts = list(tracker.trail)[-8:]
            for prev in reversed(pts[:-1]):
                if (prev[0], prev[1]) != (wy, wx):
                    px, py = self._w2c(prev[0], prev[1], prev[2])
                    dx, dy = cx - px, cy - py
                    n = max((dx * dx + dy * dy) ** 0.5, 1e-6)
                    self.canvas.create_line(cx, cy, cx + dx / n * 16,
                                            cy + dy / n * 16, tags="live",
                                            fill=th["accent"], width=2,
                                            arrow="last")
                    break
