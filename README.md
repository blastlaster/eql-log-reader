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

> **Windows SmartScreen note:** the installer is not yet code-signed, so
> Windows may warn that it "isn't commonly downloaded" (browser) and show
> "Publisher: Unknown" (installer). That's the standard warning for any
> unsigned download, not a detection of anything harmful — the tools only
> read the game's log file, and the full source is this repository, so you
> can audit or build it yourself (see BUILDING.md). To proceed: choose
> **Keep** on the download, then **More info → Run anyway** at install.
> Code signing to remove the warning entirely is in progress.

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
kills, and share of combat time per stance/invocation). A BUFFS block
lists the buffs/debuffs currently on you with estimated countdowns
(durations from the spell file; the log's buff lines carry no spell name,
so they're attributed via `spells_us_str.txt` messages). Rates divide by
*active* combat time — downtime between chained pulls is capped out, so the
numbers reflect how hard you actually hit. Right-click for options: themes,
vertical/horizontal layout, fight-average vs rolling 10s/30s windows,
DPS vs DPM units, combat timeout (5–60s), size, opacity, and the buff
block on/off.

**Session Report** (`eql_session_report.py`) — deep-dive companion:
damage/healing by ability with category filter and search, bar-chart graphs
(damage by ability, DPS per fight), session-vs-session comparison with
best-session stars, persistent personal records, stance/invocation
performance, spells cast (mana/cast/recast/duration from `spells_us.txt`,
plus per-spell resist counts and fizzle/interrupt totals), buff/debuff
uptime on you (log lines like "You feel armored." carry no spell name — the
report attributes them via the game's own `spells_us_str.txt` message
table), and an unrecognized-line calibration tab.

Shared library code (not run directly): `eql_overlay_common.py` (log
tailing, settings, themes), `eql_combat_tracker.py` (the combat parser),
`eql_spell_db.py` (spells_us.txt reader).

## Themes

One shared theme set across all four applets: **16-bit Window** (the
default), CRT Terminal, Arcade LED, Vintage (text-only rows), and
**Neon HUD** — a fully transparent mode where black-outlined neon text
floats directly over the game (Windows; the report and launcher render it
as a plain dark palette). Pick a theme from each applet's right-click menu
(overlays), the Theme dropdown (Session Report), or the Theme button
(Launcher).

## In-game setup (Friends Overlay)

**Before anything else, turn on logging:** type `/log on` in any in-game
chat window. This is what makes the game write `eqlog_<Name>_<Server>.txt`
in the first place — every tool in this suite reads that file, so nothing
here works until logging is on. Logs are written to your EverQuest Legends
install's `Logs` folder.

The Friends Overlay reads `/who` and friend-list output from the game's log,
so it needs a dedicated chat tab plus a macro/hotkey that refreshes that data
for you automatically.

1. Open any chat window and create a new tab.
2. Route all `/who` messages and "Other" messages to that tab.
3. Turn off highlighting on new messages for that tab, so it doesn't flash/alert.
4. Press `L` to open Socials.
5. Create a new macro: `/friend | /who friend all | /pet who leader | /pause 60 | /who`.
   (The trailing `/pause 60 | /who` runs a plain `/who` six seconds later —
   that's what reveals your own level to the log, which the duration
   estimates scale by.)
6. Place the macro in the last slot of your main hotbar (slot 12) — any slot
   works, this is just what the rest of these steps assume.
7. Press `Alt+O` to open Settings, then go to Controls > Hotbar 1 > Button 12
   (or whichever slot you used).
8. Rebind that button to one of your movement keys (e.g. Right / D).
9. Pressing that movement key now also fires the macro into the hidden chat
   tab, refreshing friend/pet data every time you move that direction.
10. Press that direction any time you want to update the friends list.
    `/who` results also pop up in their own window — right-click the main
    overlay element and give it a try.

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
*effects* come from eqlwiki.com; spell magnitude and buff-duration
estimates use classic-era EQEmu reference math and are labeled as
estimates in the UI. Spell-file mechanics (song/lifetap/discipline flags,
target and resist types, the full SPA effect table, buff message strings,
wiki-verified spell lists) follow the reverse-engineered format documented
by the EQL Spell Explorer project (github.com/Amerzel/eql-info).

The spell-data features read `spells_us.txt` / `spells_us_str.txt` from
your EQL install (found automatically); without them those features
quietly degrade while combat parsing works as normal. Duration estimates
scale by your character level once the log reveals it via a plain `/who`
(`/who friend all` alone does not include yourself — the recommended
macro above ends with `/pause 60 | /who` precisely so firing it pins
your level automatically); until then estimates assume L50.

Settings, rosters, personal records, and all-time stats are stored as JSON
files next to the scripts and are intentionally not part of this
repository.

## License

MIT — see [LICENSE](LICENSE).
