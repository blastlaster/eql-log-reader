# Building a Setup.exe for other players

This is for *you* (whoever maintains this repo) to produce a distributable
installer. Players just run the resulting `EQL-Log-Reader-Setup.exe` -- they
never see Python, PyInstaller, or a command line.

## One-time setup

1. Install Python 3.8+ from https://python.org/downloads/ if you don't have
   it (check "Add python.exe to PATH" during install).
2. Install Inno Setup (free) from https://jrsoftware.org/isdl.php -- accept
   the defaults.

## Every time you want to cut a new build

Run these two from the project folder, in order:

1. **`build_exe.bat`** -- creates a throwaway virtual environment, installs
   PyInstaller into it, and builds `eql_suite.spec`, which compiles all four
   tools into one shared **onedir** bundle:
   - `eql_launcher.exe`
   - `eql_friend_overlay.exe`
   - `eql_dps_meter.exe`
   - `eql_session_report.exe`
   - one shared `_internal\` folder holding the Python runtime and libraries
     all four tools depend on

   These land in `dist\EQL Log Reader\` alongside `icon.png`, `LICENSE`, and
   `README.md`. The launcher already knows to look for sibling `.exe` files
   by name in that same folder (see the `frozen` checks in `eql_launcher.py`
   and friends), so nothing else needs wiring up -- `MERGE()` in the spec
   keeps that shared dependency code stored once instead of once per tool.

2. **`make_installer.bat`** -- finds Inno Setup's compiler and builds
   `Output\EQL-Log-Reader-Setup.exe` from `installer.iss`. That one file is
   what you upload/share -- double-click it, click through, done. It puts
   the tools in Program Files, adds a Start Menu entry (and optional desktop
   icon), and includes a normal Windows uninstaller entry.

Re-run both scripts whenever you change the Python source; they clean up
after themselves (`build\`, `dist\`, `.buildenv\` are all disposable and
already covered by `.gitignore` additions below).

## What each new file is

- `icon.ico` -- multi-resolution version of `icon.png`, needed because
  Windows exe/installer icons must be .ico, not .png.
- `eql_suite.spec` -- the actual PyInstaller build recipe (onedir, all four
  tools merged into one shared bundle, UPX disabled). `build_exe.bat` just
  invokes this.
- `build_exe.bat` -- PyInstaller build driver.
- `installer.iss` -- Inno Setup project (edit `MyAppVersion` here each
  release).
- `make_installer.bat` -- compiles `installer.iss` into Setup.exe; signs
  everything first when `signing.bat` exists (see "Code signing" below).
- `signing.example.bat` -- template for the (git-ignored) `signing.bat`
  code-signing config.

## Code signing

Unsigned installers trip Windows SmartScreen on download AND on install
("Publisher: Unknown", "not commonly downloaded") -- users have to click
through two scary prompts. Signing fixes the "Unknown publisher" half
immediately; the "not commonly downloaded" half fades as reputation
accrues on the certificate (keep signing every release with the same
cert -- reputation transfers to new versions).

**Getting a certificate** (one-time decision; identity verification is
part of all of them):

- **Azure Trusted Signing** -- ~$9.99/month. Microsoft's own service;
  best SmartScreen standing since Microsoft itself vets the identity.
  Check current availability for individual (non-company) accounts.
- **Certum Open Source Code Signing** -- ~EUR 70/year (+ card/reader or
  their SimplySign cloud app). The budget classic for open-source
  projects; an OV cert, so reputation still builds over some weeks.
- **SignPath Foundation** -- free for qualifying open-source projects,
  but releases must be built through their CI pipeline (GitHub Actions),
  not on your machine.
- Standard OV/EV certs from DigiCert/Sectigo/SSL.com ($200-500/year)
  buy the same thing at brand-name prices; EV is overkill here.

**Wiring it up** (already plumbed): copy `signing.example.bat` to
`signing.bat` (git-ignored) and fill in the variant matching your
certificate. From then on `make_installer.bat` automatically signs the
four tool EXEs, the installer, and its uninstaller. Always keep the
timestamp arguments (`/tr` + `/td`) so signatures outlive the cert.

**Until then**: the warning is a false positive common to all unsigned
PyInstaller output. Users can click "Keep" on the download and
"More info" -> "Run anyway" at install. Submitting each release to
Microsoft's false-positive review (https://www.microsoft.com/wdsi/filesubmission,
"Software developer" option) helps the Defender side; only signing +
reputation clears SmartScreen properly.

## Notes
- **No Python required on the player's machine.** PyInstaller bundles a
  private Python runtime into the bundle, so players don't install anything
  but your Setup.exe.
- **Why onedir, not onefile.** Onefile builds pack everything into a single
  .exe that self-extracts to a fresh temp folder every time it's launched.
  That self-extraction step is both slower to start and a much more common
  target for antivirus engines to flag or "clean" (patch/quarantine part of)
  after the fact -- which is exactly what produces symptoms like "ordinal
  not found" errors or a blank icon on a build that compiled without error.
  `eql_suite.spec` builds onedir instead: the four tools share one
  `_internal\` folder of real files sitting next to their .exe's, nothing
  to self-extract, and UPX compression (another AV-heuristic trigger) is
  turned off too. The tradeoff is a folder of files per install instead of
  a single portable .exe per tool -- irrelevant here since the installer
  already presents players with one Setup.exe regardless.

## Troubleshooting a build

- **"Ordinal not found" when launching, and/or a blank shortcut icon.**
  These two showing up together almost always mean the freshly-built exe
  got corrupted *after* PyInstaller wrote it -- the single most common
  cause is Windows Defender (or another AV) quietly patching/quarantining
  part of a brand-new, unsigned, UPX/onefile-packed exe during its
  real-time scan, which can zero out the icon resource and/or mangle the
  import table. The build already switched to onedir + no UPX (see "Why
  onedir" above) specifically to avoid this. If it still happens:
  1. Delete `build\`, `dist\`, `.buildenv\`, and `Output\`, then re-run
     `build_exe.bat` and `make_installer.bat` from a clean slate -- confirm
     you're building with the current `eql_suite.spec`, not a leftover
     onefile build from before.
  2. Check Windows Security -> Virus & threat protection -> Protection
     history for anything referencing the project/dist folder around the
     time you built.
  3. Add a Defender exclusion for the whole project folder (Windows
     Security -> Virus & threat protection -> Manage settings -> Add or
     remove exclusions -> Folder) and rebuild again with it in place.
  4. Before wrapping into the installer, sanity-check the raw build by
     double-clicking `dist\EQL Log Reader\eql_launcher.exe` directly -- if
     that launches clean with its real icon, the installer step wasn't the
     problem; something upstream (AV, a previous corrupted build) was.
  5. If it's still broken with no AV involved, it may instead be a missing
     Universal C Runtime on that Windows install -- install the Microsoft
     Visual C++ Redistributable (x64) from
     https://aka.ms/vs/17/release/vc_redist.x64.exe and try again.
- **Stale-looking desktop icon after a successful install.** Windows
  caches shortcut icons aggressively. If the exe itself has a correct icon
  (check it in `dist\EQL Log Reader\` or in Program Files directly) but the
  Desktop/Start Menu shortcut still looks blank or generic, that's usually
  just the icon cache -- log off/on, or rebuild the icon cache, clears it.
- **`make_installer.bat` says ISCC.exe wasn't found even though Inno Setup
  is installed.** The script checks the standard install paths for Inno
  Setup 5/6/7 plus a version-agnostic fallback scan of
  `Program Files(x86)\Inno Setup*`; if your install is somewhere
  nonstandard, run Inno Setup's own IDE, open `installer.iss`, and click
  Compile instead.
