# EQL Log Reader — v1.4 (in progress)

## What's new in v1.4

**Buff list no longer keeps ghosts after you die.** Death strips every buff and debuff in game, but the log prints no fade messages for them — the meter's BUFFS block used to carry the whole pre-death list (with `+elapsed` / `?` timers) until the next recast. "You have been slain..." now clears the tracked list; uptime accounting banks the stretch as usual.

**Instant spells no longer land on the buff list.** Direct heals, lifetaps, and nukes log a "cast on you" message just like buffs do (e.g. Light Healing's "You feel a little better."), but nothing lands that could ever fade — they used to sit on the BUFFS list forever as `+elapsed` entries. Messages whose spell candidates are all instant are now skipped.

**Resists, fizzles, and interrupts now use EQL's real phrasings — and attribute per spell.** v1.3 shipped classic-EQ guesses; calibration lines from a real log confirmed EQL's actual forms, which all carry the spell name:
- `A dry bone skeleton resisted your Fingers of Fire!`
- `Your Cascade of Hail spell fizzles!`
- `Your Force Snap spell is interrupted.`

The Session Report's Spells cast table gains **Fizzled** and **Interrupted** columns next to Resisted (the Casts column keeps counting attempts — a fizzled or interrupted cast still logged "You begin casting..."). Third parties' fizzles/interrupts (`Henelope's Convoke Shadow spell fizzles!`) are recognized and ignored. A failed cast also cancels its attribution window, so damage or an ambiguous buff-landed line right after a fizzle can no longer be pinned on the dead cast. The classic nameless phrasings remain as fallbacks.

Two more confirmed from a later log: **incoming resists** use `You resist a lesser mummy's Rabies!` (counted in "Resisted by you"), and **bard song interruptions** log `Your melody has been interrupted!` (counted with interrupts).

**Misc.** "... is healed from within." (the Budding Heal line's delayed-heal trigger firing on someone else) is recognized and ignored instead of landing in the calibration tab; first-person chat ("You tell General:1, ...") no longer leaks into the calibration tab when it quotes combat words.

**Buff tracking survives auto-played bard songs (Symphonic Aura).** The aura logs no "You begin singing..." lines — only the pulse and fade messages — which exposed three tracking bugs, confirmed against a real log:

- *A message can be one spell's landing AND another's fade.* "You slow down." is the cast-on-you text of 28 snare/slow spells **and** the fade text of the Selo's run-speed line. It was always read as a debuff landing, so Selo's fading created a phantom up-counting debuff row and left the real Selo's entry open forever. Dual-meaning messages now read as the fade when something active matches the fade candidates, and as the landing otherwise. (Tradeoff: a snare landing *while* Selo's runs is misread as the Selo's fade — the next pulse re-opens Selo's 6s later.)
- *A fade message shared by two active buffs closed neither.* "Your surge of strength fades." means Anthem de Arms or Yaulp; with both up, the tracker refused to guess and Anthem ghosted at `?` forever. Ties now resolve to the active buff nearest its estimated natural end (permanent/unknown-duration buffs sort last; still no guess if nothing has a usable estimate). Also fixes DoTs: "You feel better." now closes Infectious Cloud instead of being misread as a Light Healing landing.
- *"Your wounds begin to heal." is not passive regen.* An old pattern ate it as an amountless regen tick; it's actually the Hymn of Restoration / Elixir / Pact HoT landing message, and now reaches the buff tracker (the old pattern remains as a fallback when the client's string file is missing).

**Zoning cleans up the buff list.** Songs are silently stripped when you zone — no fade lines — so Anthem/Hymn/Selo's ghosted through every zone change. "LOADING, PLEASE WAIT..." now closes any tracked entry that can't still be up on the other side: everything it could be is a song, instant, or already past its own duration estimate. Buffs that might legitimately persist (permanent, unknown duration, still inside their estimate) ride through untouched, exactly like the game does it.

**Spell-message tables respect EQL's level 50 cap.** Player spells gated above 50 exist in the Live-era data files but can never occur on EQL — they only added false ambiguity to shared messages (Live's L77 Selo's Accelerating Canto shadowing the real L5 Accelerando). They're now dropped at load; mob-only spells are kept, since their debuff messages on you are real. On top of that, an ambiguous (quoted) buff whose remaining candidates all share one duration estimate now shows a real countdown instead of `+elapsed` — whichever spell it is, it ends at the same time.

---

# EQL Log Reader — v1.3

**Release date:** July 11, 2026

Mechanics release: the suite now reads far more of the game's own spell data — sourced from the EQL Spell Explorer project's reverse-engineered client-data format (github.com/Amerzel/eql-info) and verified against the installed game files. Headliners: a live BUFFS block on the meter with level-accurate countdown estimates, buff/debuff uptime and cast-outcome tracking in the Session Report, wiki-verified spell filtering, proc tagging, and a fixed recast-time misread.

## What's new in v1.3

**Spell database (`eql_spell_db.py`) — much fuller read of `spells_us.txt`.**
- Tracks the current 173-column file revision (the 2026-06-29 patch inserted a placeholder column at index 103, shifting later columns; everything this suite reads sits below the shift and the effects blob is located by content, so both file revisions parse identically).
- **Fixed: recovery/recast columns are milliseconds, not seconds** — a 1.5s recast previously displayed as "1500s" in the Session Report.
- Full 528-entry SPA (spell affect) name table, target-type and resist-type name maps, and the 77-skill enum.
- New per-spell fields: casting skill, endurance cost, shared reuse timer, discipline flag, recourse link.
- Song recognition now uses the game's own classification (casting skill = Singing/instruments) alongside bard-exclusivity.
- Lifetap recognition from the spell file's own target-type flag (13/20) — covers all ~474 taps with no name list to maintain.
- Buff duration estimates (EQEmu classic-era formula table, labeled as estimates; EQL marks e.g. the Shielding line permanent-until-removed).
- Heal candidates now include SPA 79 (instant HP) and SPA 100 (heal-over-time) effects, not just SPA 0.
- Loads `spells_us_str.txt` to map the log's nameless buff lines ("You feel armored." / "Your shielding fades.") back to the spell(s) that produce them.
- New `eql_verified_spells.py`: wiki-verified per-class spell lists (1,503 spells across 13 classes, from eql-info's hand-checked `verified/*.txt`). Heal-candidate estimates filter to spells actually obtainable on EQL's L1-50 server by default — e.g. the Bard list collapses to exactly the four real regen songs — with a "Verified spells only" checkbox in the Session Report's Diagnostics tab to see the raw file instead. Classes with no verified data pass through unfiltered.

**Combat tracker — new mechanics tracked.**
- Buffs/debuffs on you: gained/faded counts and uptime, attributed via the spell-message tables; ambiguous shared messages resolve through your recent casts or gain/fade candidate pairing, and otherwise display as the quoted message text rather than a guess.
- Lifetap self-heal synthesis is now data-driven (catches every tap spell, e.g. Drain Soul, not just the five hand-confirmed names).
- Spell resists (per spell, both directions), fizzles, and interrupts — classic-EQ phrasings, flagged as unconfirmed for EQL; if EQL words them differently the real lines now land in the calibration tab (its keyword filter also widened: resist/fizzle/interrupt/discipline/dispel).
- Proc attribution: damage from a spell that (a) the spell file lists as proc-granted (SPA 85/323/339/419/427 and friends — 3,793 candidate spells) and (b) you were never seen casting shows as "Spell (proc)" in the report's damage-by-ability Type column. Later cast evidence removes the tag. Item-granted weapon procs aren't in client spell data and can't be recognized this way — they still count normally, just untagged.

**DPS/HPS Meter — live BUFFS block.** Active buffs/debuffs on you with estimated countdowns (soonest-to-expire first; `perm` for permanent buffs, `+m:ss` elapsed for unknown durations, `?` when past the estimate with no fade line seen). Both layouts; toggle via right-click.

**Buff durations scale with your real level.** The tracker learns your level from `/who` entries naming your character (and the classic level-up line, if EQL uses it); buff and spell duration estimates scale by it instead of assuming L50. Confirmed against the in-game buff window:
Shield of Barbs (duration formula 10 = 3×level+10 ticks) reads 7:18 at L21, where the L50 assumption said 15:00. Until a `/who` naming you appears in the log, estimates still assume L50 — run a plain `/who` once per session to pin it (the Session Report says which level its durations use). Buffs cast on you by OTHERS scale by the caster's level, which the log never reveals; those estimates stay approximate.

**Session Report.** Spells-cast table gains Duration and Resisted columns plus a fizzles/interrupts/resists summary line; new "Buffs/debuffs on you" table (gained/faded/uptime, `*` = still active at the last log line). The Diagnostics tab's heal-candidate estimates now also consider instant-HP and heal-over-time effects, not just the classic HP slot.

## What's included

**Launcher** — control panel. Auto-detects every character in the default Daybreak install, pick one, then start/stop each overlay with a click. Friends Overlay and DPS/HPS Meter follow you automatically when you switch characters. Checks GitHub for newer releases.

**Friends Overlay** — live friends list with level, class combo, race, zone, and AFK detection. Per-character rosters persist between sessions. Non-friend `/who` searches never pollute the roster.

**DPS/HPS Meter** — retro live combat meter: DPS, HPS, DTPS with melee/spell/song/damage-shield splits, damage sources split six ways (Melee / Skill / Spell / Song / DS / Pet), pet tracked as its own actor, accuracy/crit/biggest-hit, kill rate, stance & invocation tracking, a persistent ALL TIME block, and (new) a BUFFS block showing what's on you with estimated countdowns. Right-click for themes, layout, fight-average vs rolling windows, DPS vs DPM, combat timeout, size, opacity, and the buff block on/off.

**Session Report** — a one-page dashboard (headline stats, damage-split and damage-taken charts, top abilities, DPS-per-fight, Stance/Invocation performance) plus Abilities (full sortable damage/healing tables with proc tagging, spells cast with duration/resist data, buff/debuff uptime), Sessions (session-vs-session comparison, personal records), and Diagnostics (verified-filtered passive-healing estimates, unrecognized-line calibration) tabs.

## Requirements

- Windows, with EverQuest Legends running.
- `/log on` in-game — every tool reads `eqlog_<Name>_<Server>.txt`.
- **New in v1.3:** the spell-data features (buff names/countdowns, duration/resist columns, heal estimates, proc tags) read `spells_us.txt` and `spells_us_str.txt` from your EQL install. They're found automatically in the default Daybreak install location (or next to the log file's game directory); if the files aren't found, those features quietly degrade — combat parsing itself never depends on them.
- For level-accurate buff/spell duration estimates, run a plain `/who`  once per session so the log reveals your level (the Friends Overlay's
  `/who friend all` macro does not include yourself). Until then, estimates assume L50.

## Themes

One shared theme set across all four applets, with **16-bit Window** as the default: 16-bit Window, CRT Terminal, Arcade LED, Vintage (text-only rows), and Neon HUD — a fully transparent mode where black-outlined neon text floats directly over the game. Friends Overlay and DPS/HPS Meter offer the full set including Neon HUD; Session Report and the Launcher render a plain dark palette instead (they aren't floating overlays).

## Distribution

v1.3 ships as four standalone Windows executables (Launcher, Friends Overlay, DPS/HPS Meter, Session Report) sharing one runtime folder — no Python install required. Keep the whole installed folder together; the Launcher starts the others as sibling processes, and each tool keeps its settings/rosters/personal-records JSON files next to itself. Installing over an existing copy upgrades it in place; your settings and rosters are preserved.

## Installation

**Option 1: Download (recommended)**
Download `EQL-Log-Reader-Setup.exe` from this release and run it. It installs the Launcher and all three overlay tools together in one folder, with a Start Menu entry and optional desktop icon — no Python required. Launch the app via the Launcher shortcut it creates.

**Option 2: Build from source**
Requires Python 3.8+ and PyInstaller (`pip install pyinstaller`), and Inno Setup if you want to produce a Setup.exe of your own. From the
project folder:

```
build_exe.bat        REM builds all four tools into dist\EQL Log Reader\
make_installer.bat   REM packages that into Output\EQL-Log-Reader-Setup.exe
```

See `BUILDING.md` for details and troubleshooting.

## In-game setup (Friends Overlay)

**Before anything else, turn on logging:** type `/log on` in any in-game chat window. Logs are written to your EverQuest Legends install's `Logs` folder.

The Friends Overlay reads `/who` and friend-list output from the game's log, so it needs a dedicated chat tab plus a macro/hotkey that refreshes that data automatically.

1. Open any chat window and create a new tab.
2. Route all `/who` messages and "Other" messages to that tab.
3. Turn off highlighting on new messages for that tab, so it doesn't flash/alert.
4. Press `L` to open Socials.
5. Create a new macro: `/friend | /who friend all | /pet who leader | /pause 60 | /who`.

<img width="523" height="320" alt="macro + special ex" src="https://github.com/user-attachments/assets/2f24e0b7-5ea7-49ba-9e12-004fb33e49b5" />

6. Place the macro in the last slot of your main hotbar (slot 12) — any slot works, this is just what the rest of these steps assume.
7. Press `Alt+O` to open Settings, then go to Controls > Hotbar 1 > Button 12 (or whichever slot you used).
8. Rebind that button to one of your movement keys (e.g. Right / D).
9. Pressing that movement key now also fires the macro into the hidden chat tab, refreshing friend/pet data every time you move that direction.
10. Press that direction any time you want to update the friends list. `/who` results also pop up in their own window — right-click the main overlay element and give it a try.

## Notes on accuracy

Log-line formats were calibrated against real gameplay logs; anything the parser doesn't recognize lands in the "Unrecognized lines" tab (Session Report) or the meter's calibration window rather than being silently dropped. Stance/invocation effects are sourced from eqlwiki.com. Spell magnitude and buff-duration estimates use classic-era EQEmu reference math, are labeled as estimates in the UI, and scale by your level once a `/who` line reveals it (Shield of Barbs' in-game 7:18 at L21 matches the formula exactly). Buffs cast on you by other players scale by the caster's level, which the log never shows — those countdowns stay approximate. Resist/fizzle/interrupt lines use classic-EQ phrasings not yet confirmed for EQL; if EQL words them differently they'll appear in the calibration tab instead of being counted. Spell-file mechanics (song/lifetap/discipline flags, target and resist types, the SPA effect table, buff message strings, verified spell lists) follow the reverse-engineered format documented by the EQL Spell Explorer project (github.com/Amerzel/eql-info).

## License

MIT — see LICENSE.

---

# EQL Log Reader — v1.1

**Release date:** July 8, 2026

Follow-up release: a redesigned Session Report dashboard, a Launcher quality-of-life fix, a combat-tracking accuracy fix, and packaging scripts for cutting future installers.

## What's new in v1.1

**Session Report — Overview tab redesigned into a real dashboard.** Previously the report spread its data across nine tabs (Overview, Graphs, Damage by Ability, Healing by Ability, Sessions, Stance/Invocation, Spells Cast, Passive Healing (est.), Unrecognized lines), and the top bar's Session/Theme controls and buttons could get squeezed off the window at smaller sizes. Now:
- The **Overview** tab is a single scrollable dashboard: headline stat cards (session length, avg DPS/DTPS, damage, healing, kills, deaths, biggest hit, stance), a color-coded damage-split bar, a damage-taken breakdown, top damage/healing abilities as bar charts, a DPS-per-fight chart, and the Stance/Invocation performance tables — most of a session at a glance.
- Everything else is consolidated into three further tabs: **Abilities** (full sortable damage/healing-by-ability tables with category filter + search, and spells cast), **Sessions** (session-vs-session comparison, personal records), and **Diagnostics** (passive healing estimates, unrecognized-line calibration).
- The top bar is now two rows specifically so the Session/Theme dropdowns and Refresh/Change log buttons can never be squeezed off-window.

**Launcher — auto-restart on character switch.** Friends Overlay and DPS/HPS Meter previously kept watching whichever log file they were started with, so switching your active character in the Launcher required manually closing and reopening both tools. They now auto-restart with the newly selected log the moment you pick a different character (via Select or Browse). Session Report is deliberately left alone, since it's a multi-tab report you're actively reading rather than a live tracker.

**Launcher — Neon HUD removed from the Launcher's own theme picker.** Neon HUD is a fully transparent, chroma-keyed theme meant to float over the game; it never made sense on the Launcher's normal decorated window (Session Report already excluded it for the same reason). It's unaffected everywhere it's actually useful — Friends Overlay and DPS/HPS Meter still offer it.

**Combat tracker — Lifetap-family spells now count as healing.** Lifetap, Lifespike, Lifedraw, Siphon Life, and Spirit Tap deal damage and heal the caster for the same amount, but the game only ever logs the damage half of *your own* casts — confirmed against real gameplay logs, where other players' self-casts of the same spells log both halves, always healing for exactly the damage dealt. The tracker now records a matching self-heal (1:1 with the logged damage) whenever one of these spells lands, so they show up correctly in HPS and the healing-by-ability table instead of vanishing.

**Packaging.** Added `build_exe.bat` (PyInstaller, onedir build of all four tools via `eql_suite.spec`), `make_installer.bat`, and `installer.iss` (Inno Setup) for producing `EQL-Log-Reader-Setup.exe`. `make_installer.bat` locates `ISCC.exe` across Inno Setup 5/6/7, including Inno 7's per-user `%LocalAppData%\Programs` install location. See `BUILDING.md` for the full build workflow.

## What's included

**Launcher** — control panel. Auto-detects every character in the default Daybreak install, pick one, then start/stop each overlay with a click. Friends Overlay and DPS/HPS Meter now follow you automatically when you switch characters.

**Friends Overlay** — live friends list with level, class combo, race, zone, and AFK detection. Per-character rosters persist between sessions. Non-friend `/who` searches never pollute the roster.

**DPS/HPS Meter** — retro live combat meter: DPS, HPS, DTPS with melee/spell/song/damage-shield splits, damage sources split six ways (Melee / Skill / Spell / Song / DS / Pet), pet tracked as its own actor, accuracy/crit/biggest-hit, kill rate, stance & invocation tracking, and a persistent ALL TIME block. Right-click for themes, layout, fight-average vs rolling windows, DPS vs DPM, combat timeout, size, and opacity.

**Session Report** — a one-page dashboard (headline stats, damage-split and damage-taken charts, top abilities, DPS-per-fight, Stance/Invocation performance) plus Abilities (full sortable damage/healing tables, spells cast), Sessions (session-vs-session comparison, personal records), and Diagnostics (passive healing estimates, unrecognized-line calibration) tabs.

## Themes

One shared theme set across all four applets, with **16-bit Window** as the default: 16-bit Window, CRT Terminal, Arcade LED, Vintage (text-only rows), and Neon HUD — a fully transparent mode where black-outlined neon text floats directly over the game. Session Report and the Launcher render/offer it as a plain dark palette or not at all (Neon HUD is excluded from the Launcher's theme picker as of v1.1, and was already excluded from Session Report's), since neither is a floating overlay. Friends Overlay and DPS/HPS Meter offer the full set including Neon HUD.

## Distribution

v1.1 ships as four standalone Windows executables (Launcher, Friends Overlay, DPS/HPS Meter, Session Report) sharing one runtime folder — no Python install required. Keep the whole installed folder together; the Launcher starts the others as sibling processes, and each tool keeps its settings/rosters/personal-records JSON files next to itself.

## Installation

**Option 1: Download (recommended)**
Download `EQL-Log-Reader-Setup.exe` from this release and run it. It installs the Launcher and all three overlay tools together in one folder, with a Start Menu entry and optional desktop icon — no Python required. Launch the app via the Launcher shortcut it creates.

**Option 2: Build from source**
Requires Python 3.8+ and PyInstaller (`pip install pyinstaller`), and Inno Setup if you want to produce a Setup.exe of your own. From the project folder:

```
build_exe.bat        REM builds all four tools into dist\EQL Log Reader\
make_installer.bat   REM packages that into Output\EQL-Log-Reader-Setup.exe
```

See `BUILDING.md` for details and troubleshooting. `dist\EQL Log Reader\eql_launcher.exe` also runs standalone without building the installer.

## In-game setup (Friends Overlay)

**Before anything else, turn on logging:** type `/log on` in any in-game chat window. This is what makes the game write `eqlog_<Name>_<Server>.txt` in the first place — every tool in this suite reads that file, so nothing here works until logging is on. Logs are written to your EverQuest Legends install's `Logs` folder.

The Friends Overlay reads `/who` and friend-list output from the game's log, so it needs a dedicated chat tab plus a macro/hotkey that refreshes that data automatically.

1. Open any chat window and create a new tab.
2. Route all `/who` messages and "Other" messages to that tab.
3. Turn off highlighting on new messages for that tab, so it doesn't flash/alert.
4. Press `L` to open Socials.
5. Create a new macro: `/friend | /who friend all | /pet who leader`.
6. Place the macro in the last slot of your main hotbar (slot 12) — any slot works, this is just what the rest of these steps assume.
7. Press `Alt+O` to open Settings, then go to Controls > Hotbar 1 > Button 12 (or whichever slot you used).
8. Rebind that button to one of your movement keys (e.g. Right / D).
9. Pressing that movement key now also fires the macro into the hidden chat tab, refreshing friend/pet data every time you move that direction.
10. Press that direction any time you want to update the friends list. `/who` results also pop up in their own window — right-click the main overlay element and give it a try.

## Notes on accuracy

Log-line formats were calibrated against real gameplay logs; anything the parser doesn't recognize lands in the "Unrecognized lines" tab (Session Report) or the meter's calibration window rather than being silently dropped. Stance/invocation effects are sourced from eqlwiki.com; spell magnitude estimates use classic-era EQEmu reference math and are labeled as estimates in the UI. Lifetap-family self-heals (new in v1.1) are estimated 1:1 from the spell's own logged damage, since the game never logs your own self-heal from these directly. There is no client-side gear/item-stat data source, so stance/invocation switch detection is regex-based against log text and not independently verified against item data.

## License

MIT — see LICENSE.

---

# EQL Log Reader — v1.0

**Release date:** July 7, 2026

First public release. EQL Log Reader is a family of always-on-top overlay tools for **EverQuest Legends**, driven entirely by the game's own log file. No injection, no memory reading, no game files touched — the tools just tail the log the game already writes, so they're safe to run alongside the game.

Requires the game in Windowed or Borderless-Windowed mode (true exclusive fullscreen draws over everything, including overlays).

## What's included

**Launcher** — control panel. Auto-detects every character in the default Daybreak install, pick one, then start/stop each overlay with a click. Re-running auto-detect always reflects exactly what's in the game folder.

**Friends Overlay** — live friends list with level, class combo, race, zone, and AFK detection. Per-character rosters persist between sessions. Non-friend `/who` searches never pollute the roster.

**DPS/HPS Meter** — retro live combat meter: DPS, HPS, DTPS with melee/spell/song/damage-shield splits, damage sources split six ways (Melee / Skill / Spell / Song / DS / Pet), pet tracked as its own actor, accuracy/crit/biggest-hit, kill rate, stance & invocation tracking, and a persistent ALL TIME block. Right-click for themes, layout, fight-average vs rolling windows, DPS vs DPM, combat timeout, size, and opacity.

**Session Report** — damage/healing by ability with category filter and search, bar-chart graphs, session-vs-session comparison with best-session stars, persistent personal records, stance/invocation performance, spells cast (with mana/cast/recast from `spells_us.txt`), and an unrecognized-line calibration tab.

## Themes

One shared theme set across all four applets, with **16-bit Window** as the default: 16-bit Window, CRT Terminal, Arcade LED, Vintage (text-only rows), and Neon HUD — a fully transparent mode where black-outlined neon text floats directly over the game (the report and launcher render it as a plain dark palette). The Friends overlay's old Classic Slate look was retired; saved "classic" settings fall back to the default.

## Distribution

v1.0 ships as four standalone Windows executables (Launcher, Friends Overlay, DPS/HPS Meter, Session Report) — no Python install required. Keep all four `.exe` files together in one folder; the Launcher starts the others as sibling processes, and each tool keeps its settings/rosters/personal-records JSON files next to itself.

## Installation

**Option 1: Download**
Download `EQL Log Reader Setup.exe` and run it. It installs the Launcher and all three overlay tools together in one folder — no Python required. Launch the app via the Launcher shortcut it creates.

**Option 2: Build from source**
Requires Python 3.8+ and PyInstaller (`pip install pyinstaller`). From the project folder:

```
pyinstaller --onefile --windowed --icon=icon.ico eql_friend_overlay.py
pyinstaller --onefile --windowed --icon=icon.ico eql_dps_meter.py
pyinstaller --onefile --windowed --icon=icon.ico eql_session_report.py
pyinstaller --onefile --windowed --icon=icon.ico eql_launcher.py
```

All four `.exe` files land in `dist/`. Keep them together in that one folder and run `eql_launcher.exe` to start.

## In-game setup (Friends Overlay)

The Friends Overlay reads `/who` and friend-list output from the game's log, so it needs a dedicated chat tab plus a macro/hotkey that refreshes that data automatically.

1. Open any chat window and create a new tab.
2. Route all `/who` messages and "Other" messages to that tab.
3. Turn off highlighting on new messages for that tab, so it doesn't flash/alert.
4. Press `L` to open Socials.
5. Create a new macro: `/friend | /who friend all | /pet who leader`.
6. Place the macro in the last slot of your main hotbar (slot 12) — any slot works, this is just what the rest of these steps assume.
7. Press `Alt+O` to open Settings, then go to Controls > Hotbar 1 > Button 12 (or whichever slot you used).
8. Rebind that button to one of your movement keys (e.g. Right / D).
9. Pressing that movement key now also fires the macro into the hidden chat tab, refreshing friend/pet data every time you move that direction.
10. Press that direction any time you want to update the friends list. `/who` results also pop up in their own window — right-click the main overlay element and give it a try.

## Notes on accuracy

Log-line formats were calibrated against real gameplay logs; anything the parser doesn't recognize lands in the "Unrecognized lines" tab (Session Report) or the meter's calibration window rather than being silently dropped. Stance/invocation effects are sourced from eqlwiki.com; spell magnitude estimates use classic-era EQEmu reference math and are labeled as estimates in the UI. There is no client-side gear/item-stat data source, so stance/invocation switch detection is regex-based against log text and not independently verified against item data.

## License

MIT — see LICENSE.
