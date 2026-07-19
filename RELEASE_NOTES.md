# EQL Log Reader — v1.7

**Release date:** July 21, 2026

A whole new tool: the **Atlas Collector** — a cartography companion that turns your log into a living loot-and-spawn database, drawn on real zone maps. Kill, loot, and die anywhere and the Atlas remembers what, who, and exactly where.

## What's new in v1.7

**Atlas Collector (new tool).** Tails the log for kills, drops, corpse coin, and deaths — EQL's auto-loot lines name the mob, so every drop attributes exactly — and persists a per-character database. First launch imports your entire existing log history in seconds; restarts only catch up, never double-count. Observed drop rates sharpen with every kill.

**Pre-discovered baseline.** Ships with a distilled copy of the public Project Quarm database (same EQMacEmu lineage as EQL, same item IDs): expected drop percentages, named spawn points, respawn timers, zone connections. Your play corroborates or contradicts it — and anything the baseline doesn't know (EQL's custom named, new items) is flagged **★NEW**: your discoveries. Later-era content stays hidden until you enable it (right-click → Expansions).

**Map window.** Brewall-compatible zone maps (drop his pack into `mapsrewall\`) with pan/zoom, your live position + heading + trail, loot/kill/coin/death marks where they happened, named-spawn pins with hover cards (level, respawn, drops + rates — "MAY BE UP" when a named you've killed has had time to respawn), notes, a per-story **floor filter** that follows your own elevation, **auto-follow**, **ghost mode** (chroma-key: only the drawing floats over the game), lockable size, and a full **3D view** — the map files carry true elevation the in-game map can't show; tilt and orbit the dungeon with right-drag.

**Search + commands.** A search bar with live autofill (items and mobs, yours + baseline) and scrolling grouped results; double-click anything to `find` it — orange rings mark every spot it's dropped for you. A private in-game chat channel accepts the same commands mid-fight: `find`, `guide <item>` (routes you zone-by-zone across a hand-curated classic zone graph, then follows the map geometry to the exact spot), `clear`, `note <text>` (pins at your `/loc`), `fav`. Channel safety is strict: only your own messages parse, and commands lock unless the channel has exactly one member. Full setup guide in the README.

**Launcher.** Atlas Collector appears as the fifth tool row and restarts automatically on character switch, like the other live overlays.

---

# EQL Log Reader — v1.6

**Release date:** July 13, 2026

The Fight Summary grows up: the post-fight popup introduced in v1.5 becomes a full fight browser — paginated across the session with catch-up flashing, its own saved theme with proper Neon HUD transparency, a minimize mode whose filter doubles as a query box, per-stance/invocation time-and-damage shares, and resists in both directions. Plus a log-tailing reliability fix worth updating for on its own.

## What's new in v1.6

**Fight Summary: paginated fight browser.** `‹ ›` step one fight, `«` jumps to the session's first fight, `»` to the newest — seeded history included, so the back-arrows reach fights from before the meter started. A "fight N of M · time" counter sits between. When a new fight ends while you're on the latest one, the popup follows it automatically; while you're studying an older fight it stays put — the counter grows and `»` **flashes** until you catch up (click `»`, or step there with `›`).

**Fight Summary: stance & invocation shares.** Every stance used in the fight with its percentage of active combat time *and* the damage you dealt while in it — `stances: Offense 64% (2.1K dmg)  Defense 36% (900 dmg)` — one line for stances, one for invocations. The numbers answer both "how long was I in it" and "what did it actually produce."

**Fight Summary: resists in both directions.** `enemy resisted: Tishan's Clash x1` (warn color) and `you resisted: Cancelling of Life x1` (green), with section breaks around the stance block for cleaner reading.

**Fight Summary: minimize + query filter.** A `–` button collapses the popup to the title/nav/filter rows; pagination and the `»` flash keep working. Typing in the filter — minimized or not — brings up matching ability/heal/cast rows, and **section names work too**: `stances`, `invocs`, `damage`, `healing`, `casts`, `resisted` pull up that whole section. Minimize it, type `stances`, and arrow through fights watching just that line change.

**Fight Summary: own theme + Neon HUD transparency.** Right-click the popup for its own theme, saved separately (defaults to matching the meter). The body renders with the suite's outlined-text treatment over a chroma-keyed background — true Neon HUD transparency, exactly like the meter and Friends overlay — with the title/nav/filter bars keeping their panel color as the grab handle.

**Settings survive reinstalls.** Installed builds used to write settings, rosters, personal records, and all-time stats *next to the EXE* — inside Program Files, where writes fail (or get virtualized) for normal users and vanish across install cycles. All per-user data now lives in `%APPDATA%\EQL Log Reader`; anything found next to the EXE from an older install is migrated automatically on first run, so nothing resets when you update.

**Windowed builds no longer die on a UI error — and leave evidence.** A no-console EXE has no stderr, so an error inside any UI callback (the kind a source run just prints and shrugs off) could take down the whole overlay — this is what made choosing the Neon HUD theme on the Fight Summary crash the installed build (the theme menu's parent window was destroyed mid-click; fixed too: the menu now belongs to the meter and the rebuild is deferred past the click). Every tool now logs callback errors to `%APPDATA%\EQL Log Reader\eql_errors.log` and keeps running.

**Log tailing survives transient file locks.** A momentary `PermissionError` on the log (antivirus scan, indexer, backup tool touching the file) could previously kill the polling loop silently — the overlay kept rendering but stopped updating. The watcher now skips the blocked read and retries next tick, and both overlays' poll loops always reschedule.

---

# EQL Log Reader — v1.5

**Release date:** July 12, 2026

Encounter analytics release: the Session Report learns who you actually fight — a per-mob fight history across every session in the log with best-vs-worst comparison and a ranked "what changed" analysis — plus a post-fight summary popup on the meter. Charm pets finally count as your pet (and register themselves when you send them to attack), the meter gains All-timescales layouts in both orientations, the horizontal layout stops clipping, and new Elder/Legend text-size presets double or 2.5× the overlay fonts for eyes that want bigger text.

## What's new in v1.5

**New Session Report tab: Encounters — per-mob fight history across every session.** Every completed Combat in the whole log becomes an encounter, grouped by the mob it was against. Search a name, see every attempt (when, session, zone, duration, DPS, damage dealt/taken, deaths, stance — best attempt starred), open one for full detail (damage/healing by ability with shares, casts, resists, rates at /s /m /h), and compare any two — or hit **Compare best vs worst** — for a side-by-side plus a ranked **"what changed"** analysis pointing at the likely impact drivers: ability-mix shares, stance/invocation, spells-resisted rate, damage taken, accuracy, crit, deaths, and spells cast in one attempt but not the other. The log is the database — history reaches as far back as the log file does, no new files.

**Fight summary popup (meter).** When a Combat ends, a small draggable window pops up next to the meter with that fight's numbers: who it was against, DPS/DPM/DPH, dealt/taken/healed, accuracy/crit/big, kills/deaths, stance, resists, and a **filterable** damage/healing/cast breakdown. It refreshes in place when the next fight ends, never fires during log seeding/backfill, and toggles via right-click → "Fight summary popup". (v1.6 turns this into a full paginated fight browser.)

**Under the hood:** each Fight now carries its own per-fight ability, heal, and cast breakdowns plus kill/death counts (session-wide totals unchanged), and the tracker exposes a completed-fight hook that both features build on.

**Pets register themselves when you send them to attack.** The pet attack announcement — `Becca`s warder told you, 'Attacking an elite gnoll fighter Master.'` (confirmed from the owner's own log; the tell goes only to the master) — now registers the pet automatically. Every `/pet attack` is an ownership proof, so charm pets get attributed without remembering `/pet leader` (which still works too). The tracker also remembers every player name seen in `/who`: a known player name can never register as a pet, a cheap safety net for name collisions and the substrate for future mob-vs-mob attribution heuristics.

**Charm pets now count as your pet.** The `/pet leader` recognizer only accepted single-word names (necro/mage-style "Jenann"), but a charmed mob keeps its multi-word mob name — confirmed: `An abhorrent says, 'My leader is Urgar.'` — so charm pets never registered and their damage vanished. Multi-word names register now, DoT damage *dealt by* your pet is recorded (it previously only counted DoTs ticking *on* the pet), and a registered pet is forgotten the moment it's slain — critical for charms, whose generic names would otherwise credit every same-named mob as your pet for the rest of the session (re-charming announces again via `/pet leader`). Known edge: between a charm *breaking* and that mob dying, its actions still count as your pet's — the log prints nothing at the moment charm breaks.

**Text size presets: Elder and Legend.** For eyes that want bigger text: the Friends Overlay and the DPS/HPS Meter gain a right-click **Text size** menu — Standard (100%), **Elder (200%)**, and **Legend (250%)**. Fonts and layout scale together, independently of the existing Size (element footprint) setting, and the choice persists per overlay.

**Horizontal layout: no more clipping.** The bottom stat strips (fight stats, ALL TIME, BUFFS, RESISTED) used to be fixed single lines — a long ALL TIME row (stance + invocation shares) ran off both edges of the canvas. They now wrap onto extra centered lines and the canvas grows to fit, breaking at the natural group boundaries (stats | stance shares | invocation shares) rather than mid-group.

**All timescales, now horizontal too.** The Layout menu offers both orientations of the All-timescales view: the existing vertical column, and a new horizontal strip where each metric column (DMG / HEAL / TAKEN) shows its three clocks side by side — `now/s` (rolling 30s), `1m/m` (last 60s per minute), `cbt/h` (whole Combat per hour). Same 1h–5h Combat timeout and log backfill as the vertical variant.

**Fullscreen note removed.** The old "requires Windowed / Borderless-Windowed mode" caveat was wrong for EQL — the overlays draw fine over the game's fullscreen mode — and is gone from the README and docs.

---

# EQL Log Reader — v1.4

**Release date:** July 12, 2026

Correctness release for the live BUFFS tracking introduced in v1.3, driven by real play sessions: buffs no longer ghost on the list after death, zoning, or auto-played bard songs (Symphonic Aura), dual-meaning spell messages are disambiguated, and the guessed classic-EQ phrasings for resists/fizzles/interrupts have been replaced with EQL's real ones — now attributed per spell in the Session Report.

## What's new in v1.4

**Buff list no longer keeps ghosts after you die.** Death strips every buff and debuff in game, but the log prints no fade messages for them — the meter's BUFFS block used to carry the whole pre-death list (with `+elapsed` / `?` timers) until the next recast. "You have been slain..." now clears the tracked list; uptime accounting banks the stretch as usual.

**Instant spells no longer land on the buff list.** Direct heals, lifetaps, and nukes log a "cast on you" message just like buffs do (e.g. Light Healing's "You feel a little better."), but nothing lands that could ever fade — they used to sit on the BUFFS list forever as `+elapsed` entries. Messages whose spell candidates are **majority** instant are now skipped: the classic enemy-lifetap emote "You feel your life force drain away." maps to ~180 spells, 137 of them instant taps (a permanent self-only necro buff among the stragglers used to immortalize the row), and frost-nuke text like "You feel your skin freeze." reads the same way. A rare drain-over-time variant still records its damage through its own damage lines.

**Resists, fizzles, and interrupts now use EQL's real phrasings — and attribute per spell.** v1.3 shipped classic-EQ guesses; calibration lines from a real log confirmed EQL's actual forms, which all carry the spell name:
- `A dry bone skeleton resisted your Fingers of Fire!`
- `Your Cascade of Hail spell fizzles!`
- `Your Force Snap spell is interrupted.`

The Session Report's Spells cast table gains **Fizzled** and **Interrupted** columns next to Resisted (the Casts column keeps counting attempts — a fizzled or interrupted cast still logged "You begin casting..."). Third parties' fizzles/interrupts (`Henelope's Convoke Shadow spell fizzles!`) are recognized and ignored. A failed cast also cancels its attribution window, so damage or an ambiguous buff-landed line right after a fizzle can no longer be pinned on the dead cast. The classic nameless phrasings remain as fallbacks.

Two more confirmed from a later log: **incoming resists** use `You resist a lesser mummy's Rabies!` (counted in "Resisted by you"), and **bard song interruptions** log `Your melody has been interrupted!` (counted with interrupts).

**Code-signing plumbing.** The build pipeline can now sign everything (the four tool EXEs, the installer, and its uninstaller) once a certificate is configured — copy `signing.example.bat` to `signing.bat` and fill in one line (see BUILDING.md "Code signing" for certificate options). This is what will eventually remove the Windows SmartScreen "Publisher: Unknown" warnings on download/install; until the certificate lands, the README documents the click-through.

**Misc.** "... is healed from within." (the Budding Heal line's delayed-heal trigger firing on someone else) is recognized and ignored instead of landing in the calibration tab; first-person chat ("You tell General:1, ...") no longer leaks into the calibration tab when it quotes combat words.

**Buff tracking survives auto-played bard songs (Symphonic Aura).** The aura logs no "You begin singing..." lines — only the pulse and fade messages — which exposed three tracking bugs, confirmed against a real log:

- *A message can be one spell's landing AND another's fade.* "You slow down." is the cast-on-you text of 28 snare/slow spells **and** the fade text of the Selo's run-speed line. It was always read as a debuff landing, so Selo's fading created a phantom up-counting debuff row and left the real Selo's entry open forever. Dual-meaning messages now read as the fade when something active matches the fade candidates, and are otherwise **dropped** — Selo's wear-off can print after zoning has already stripped the song's entry, so an unconfirmable line proves nothing, and tracking it fabricates debuffs. (Tradeoff: slow-type debuffs whose landing text is exactly "You slow down." are no longer tracked.)
- *A fade message shared by two active buffs closed neither.* "Your surge of strength fades." means Anthem de Arms or Yaulp; with both up, the tracker refused to guess and Anthem ghosted at `?` forever. Ties now resolve to the active buff nearest its estimated natural end (permanent/unknown-duration buffs sort last; still no guess if nothing has a usable estimate). Also fixes DoTs: "You feel better." now closes Infectious Cloud instead of being misread as a Light Healing landing.
- *"Your wounds begin to heal." is not passive regen.* An old pattern ate it as an amountless regen tick; it's actually the Hymn of Restoration / Elixir / Pact HoT landing message, and now reaches the buff tracker (the old pattern remains as a fallback when the client's string file is missing).

**Seed heals (Budding/Sprouting/Flowering Heal line) leave the BUFFS list on time.** These heal-over-time "seeds" print no fade line — their only ending signal is the "* Heal Trigger" spell they cast as they expire (SPA 289 trigger-on-fade in the spell data), and even that logs nothing when it heals for 0 at full health. Two mechanisms now end them: the trigger's cast-on-you message ("The heal within you blooms.") closes its parent seed, and buffs YOU cast expire from the list on their own estimated clock — your own level is known, so the estimate doesn't suffer the unknown-caster problem (Flowering Heal: gone at 0:24, exactly as its tooltip says). The HoT ticks and the trigger's final heal already count toward HPS through their attributed heal lines. Buying, scribing, and memorizing spells no longer leaks spell names into the calibration tab.

**New rate unit: per hour (DPH/HPH/DTPH) — and per-unit Combat timeouts.** Rate units now cycle per second / per minute / per hour (same numbers ×60 / ×3600; per-hour readouts abbreviate, e.g. `108.0K`). Each unit mode keeps its **own** Combat timeout and swaps it in when you switch: per-second keeps the familiar 5s–60s feel-tuning; per-minute offers 1m/5m/15m/30m/45m/60m so chained pulls group into one Combat; per-hour offers 1h–5h so a whole grind session reads as one. Rates still divide by ACTIVE combat time (downtime between pulls stays capped out of the denominator), so the timeout changes how fights are grouped, not the math. The fight timer shows h:mm:ss once a Combat runs past an hour.

**New layout: All timescales (s · m · h).** The vertical column with a 3×3 grid up top — DMG / HEAL / TAKEN rows against three live columns: `now/s` (rolling 30s), `1m/m` (the last 60 seconds expressed per minute), and `combat/h` (the whole Combat's average per hour). Runs on the per-hour Combat timeout (defaults 1h, up to 5h), so a grind session reads as one Combat while the left columns stay twitchy.

**Growing the Combat window backfills it from the log.** Switching to a longer timescale (rate units, a bigger timeout, or the All-timescales layout) now re-pulls that much log history automatically — the meter scans back through the log to cover the new window and replays it, so the Combat shows the whole-window total immediately instead of starting empty (verified: a 1h window over a 9.6MB log seeds in ~3s). "Reset current fight" still starts from scratch when that's what you want.

**Bottom sections decluttered (both meter layouts).** The stat strips (acc/crit/big, kills, stance, ALL TIME, and the horizontal BUFFS/RESISTED rows) are now centered with colored values — labels stay dim, values render in the theme's fg/accent/warn — instead of a single dim wall of text.

**Friends overlay: the /who window can minimize.** A `–` button next to `✕` collapses it to just the title bar, which already carries the player count and the time of the last pull ("WHO (12) 09:41:33"). The choice persists across sessions.

**New meter block: — RESISTED —.** A per-fight tally of YOUR spells and songs the enemy resisted (`A dry bone skeleton resisted your Denon's Disruptive Discord!`), most-resisted first — a live nudge that an ability isn't landing on this enemy and needs swapping. Clears when the fight ends; lifetime counts stay in the Session Report's Resisted column. Both layouts; toggle with right-click → "Show resisted (per fight)".

**The spell that just hit you names its buff message.** An enemy cast logs its damage line in the same instant as its cast-on-you emote (`Asaka L`Rei hit you for 12 points of magic damage by Lifespike.` + `You feel your life force drain away.`), so an ambiguous message that arrives within 2s of a named spell hit now resolves to that spell. Enemy lifetaps confirm as the instant tap they are, and duration'd debuffs ("You are engulfed by darkness." after an Engulfing Darkness tick) get a real named row with a countdown instead of a quoted `+elapsed` entry. With no adjacent hit the message stays quoted — honest, never a guess.

**Debuffs whose ending is never logged now expire on a worst-case clock.** Mob debuffs like "You feel your skin freeze." often end with no fade line, leaving `+elapsed` rows squatting on the BUFFS list long after combat. Every non-self-cast entry now closes once it's past the longest ANY of its candidate spells could run for a level-50 caster (EQL's cap) — nothing it could possibly be is still active, so it can't be up. Permanent and unknown-duration entries are exempt and still close only on a fade line, death, or zoning.

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
