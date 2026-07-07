<p align="center">
  <img src="icon.png" width="340" alt="EQL Log Reader — sword-and-shield emblem over a synthwave grid">
</p>

# EQL Log Reader

Always-on-top overlay tools for **EverQuest Legends**, driven entirely by the
game's own log file (`eqlog_<Name>_<Server>.txt`). No injection, no memory
reading, no game files touched — the tools just tail the log the game already
writes, so they're safe to run alongside the game.

Requires the game to run in Windowed or Borderless-Windowed mode (true
exclusive fullscreen draws over everything, including overlays).

## The tools

**Launcher** (`eql_launcher.py`) — control panel. Auto-detects every
character in the default Daybreak install, pick one, then start/stop each
overlay with a click.

**Friends Overlay** (`eql_friend_overlay.py`) — live friends list with
level, class combo, race, zone, and AFK detection. Non-friend `/who`
searches never pollute the roster and can pop up in their own window.
Per-character rosters persist between sessions.

**DPS/HPS Meter** (`eql_dps_meter.py`) — retro live combat meter: DPS, HPS,
DTPS with melee/spell/song/damage-shield splits, damage sources split six
ways (Melee / Skill / Spell / Song / DS / Pet), your pet tracked as its own
actor, accuracy/crit/biggest-hit, kill rate, stance & invocation tracking,
and a persistent ALL TIME block (lifetime accuracy, crits, biggest hit,
kills, and share of combat time per stance/invocation). Rates divide by
*active* combat time — downtime between chained pulls is capped out, so the
numbers reflect how hard you actually hit. Right-click for options: themes,
vertical/horizontal layout, fight-average vs rolling 10s/30s windows,
DPS vs DPM units, combat timeout (5–60s), size, and opacity.

**Session Report** (`eql_session_report.py`) — deep-dive companion:
damage/healing by ability with category filter and search, bar-chart graphs
(damage by ability, DPS per fight), session-vs-session comparison with
best-session stars, persistent personal records, stance/invocation
performance, spells cast (with mana/cast/recast from `spells_us.txt`), and
an unrecognized-line calibration tab.

Shared library code (not run directly): `eql_overlay_common.py` (log
tailing, settings, themes), `eql_combat_tracker.py` (the combat parser),
`eql_spell_db.py` (spells_us.txt reader).

## Themes

CRT Terminal, Arcade LED, 16-bit Window, Vintage (text-only rows), and
**Neon HUD** — a fully transparent mode where black-outlined neon text
floats directly over the game (Windows). The Friends overlay shares all of
them, plus its original Classic Slate look.

## Running

Python 3.8+ with tkinter (included in the standard Windows Python
installer). No third-party packages.

```
python eql_launcher.py
```

or run any tool directly, e.g.
`python eql_dps_meter.py "C:\...\Logs\eqlog_Name_server.txt"`.

## Notes on accuracy

Log-line formats were calibrated against real gameplay logs; anything the
parser doesn't recognize lands in the "Unrecognized lines" tab (Session
Report) or the meter's calibration window rather than being silently
dropped — check there first if a number looks off. Stance/invocation
*effects* come from eqlwiki.com; spell magnitude estimates use classic-era
EQEmu reference math and are labeled as estimates in the UI.

Settings, rosters, personal records, and all-time stats are stored as JSON
files next to the scripts and are intentionally not part of this
repository.

## License

MIT — see [LICENSE](LICENSE).
