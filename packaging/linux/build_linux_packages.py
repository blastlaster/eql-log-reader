#!/usr/bin/env python3
"""
EQL Log Reader -- Linux package builder (dev tool)
====================================================
Builds BOTH Linux artifacts into Output/, from any OS -- no Linux
toolchain needed (a .deb is just an `ar` archive of two tarballs, and
this script writes the ar container by hand):

  * eql-log-reader-<ver>-linux.tar.gz
        universal: unpack anywhere, run ./install.sh -- per-user XDG
        install (~/.local/share/eql-log-reader + ~/.local/bin wrapper +
        menu entry), no root needed. install.sh --uninstall removes it.

  * eql-log-reader_<ver>_all.deb
        Debian/Ubuntu/Mint: sudo apt install ./eql-log-reader_<ver>_all.deb
        Files land in /opt/eql-log-reader (read-only for users --
        eql_overlay_common.data_path sends per-user data to
        ~/.local/share/eql-log-reader), command `eql-log-reader` in
        /usr/bin, menu entry + icon system-wide.
        Depends: python3 (>= 3.8), python3-tk.

The suite is pure stdlib Python, so "packaging" is just carrying the
sources + data files; both artifacts run them with the distro's python3.

    python packaging/linux/build_linux_packages.py

Version comes from eql_overlay_common.CURRENT_VERSION. Text files are
normalized to LF so a Windows checkout can't smuggle CRLF into shebangs.
"""

import gzip
import io
import os
import re
import sys
import tarfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
OUT = os.path.join(ROOT, "Output")

RUNTIME_FILES = [
    "eql_launcher.py", "eql_friend_overlay.py", "eql_dps_meter.py",
    "eql_session_report.py", "eql_atlas.py", "eql_atlas_map.py",
    "eql_quest.py", "eql_overlay_common.py", "eql_combat_tracker.py",
    "eql_spell_db.py", "eql_verified_spells.py", "eql_update_check.py",
    "eql_atlas_baseline.json.gz", "eql_quest_db.json.gz",
    "icon.png", "LICENSE", "README.md", "RELEASE_NOTES.md",
]

APP = "eql-log-reader"
MAINTAINER = "EQL Log Reader <noreply@github.com/blastlaster/eql-log-reader>"
DESCRIPTION = "Overlay tools for EverQuest Legends"
LONG_DESCRIPTION = (
    "Always-on-top overlay tools driven entirely by the game's own log\n"
    "file: Friends overlay, DPS/HPS meter, Session Report, Atlas\n"
    "loot/spawn collector with map and quest tracking. No injection, no\n"
    "memory reading -- the tools just tail the log the game writes."
)


def suite_version():
    src = open(os.path.join(ROOT, "eql_overlay_common.py"),
               encoding="utf-8").read()
    m = re.search(r'CURRENT_VERSION = "([^"]+)"', src)
    if not m:
        raise SystemExit("CURRENT_VERSION not found in eql_overlay_common.py")
    return m.group(1)


def read_file(path, text=False):
    """File bytes; text files get their line endings normalized to LF
    (a Windows checkout must not smuggle CRLF into Linux shebangs)."""
    data = open(path, "rb").read()
    if text:
        data = data.replace(b"\r\n", b"\n")
    return data


def is_text(name):
    return name.endswith((".py", ".md", ".sh")) or name in ("LICENSE",)


def _tar_add(tar, arcname, data=None, mode=0o644, is_dir=False):
    info = tarfile.TarInfo(arcname)
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    info.mtime = int(time.time())
    if is_dir:
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
        tar.addfile(info)
    else:
        info.mode = mode
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))


# ----------------------------------------------------------------------------
# artifact 1: the universal tarball (sources + install.sh at its root)
# ----------------------------------------------------------------------------
def build_tarball(version):
    name = f"{APP}-{version}-linux"
    out_path = os.path.join(OUT, f"{name}.tar.gz")
    with tarfile.open(out_path, "w:gz") as tar:
        _tar_add(tar, name, is_dir=True)
        for fn in RUNTIME_FILES:
            data = read_file(os.path.join(ROOT, fn), text=is_text(fn))
            _tar_add(tar, f"{name}/{fn}", data)
        inst = read_file(os.path.join(HERE, "install.sh"), text=True)
        _tar_add(tar, f"{name}/install.sh", inst, mode=0o755)
    return out_path


# ----------------------------------------------------------------------------
# artifact 2: the .deb (hand-built ar container -- works on any OS)
# ----------------------------------------------------------------------------
def _ar_entry(name, data):
    """One `ar` archive member: 60-byte header + data, 2-byte aligned."""
    header = "{:<16}{:<12}{:<6}{:<6}{:<8}{:<10}`\n".format(
        name, int(time.time()), 0, 0, "100644", len(data)).encode("ascii")
    assert len(header) == 60
    return header + data + (b"\n" if len(data) % 2 else b"")


def _targz(build_fn):
    buf = io.BytesIO()
    # mtime=0 keeps rebuilds byte-comparable-ish; not load-bearing
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w") as tar:
            build_fn(tar)
    return buf.getvalue()


def build_deb(version):
    out_path = os.path.join(OUT, f"{APP}_{version}_all.deb")

    files = {}                      # arcname (under ./) -> (data, mode)
    for fn in RUNTIME_FILES:
        files[f"opt/{APP}/{fn}"] = (
            read_file(os.path.join(ROOT, fn), text=is_text(fn)), 0o644)
    files[f"usr/bin/{APP}"] = (
        (f"#!/bin/sh\nexec python3 /opt/{APP}/eql_launcher.py"
         " \"$@\"\n").encode(), 0o755)
    files[f"usr/share/applications/{APP}.desktop"] = ((
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=EQL Log Reader\n"
        f"Comment={DESCRIPTION}, driven by the game's log file\n"
        f"Exec={APP}\n"
        f"Icon={APP}\n"
        "Terminal=false\n"
        "Categories=Game;Utility;\n").encode(), 0o644)
    files[f"usr/share/pixmaps/{APP}.png"] = (
        read_file(os.path.join(ROOT, "icon.png")), 0o644)

    def add_data(tar):
        seen = set()
        for arcname in files:
            parts = arcname.split("/")[:-1]
            for i in range(1, len(parts) + 1):
                d = "./" + "/".join(parts[:i])
                if d not in seen:
                    seen.add(d)
                    _tar_add(tar, d, is_dir=True)
        for arcname, (data, mode) in files.items():
            _tar_add(tar, f"./{arcname}", data, mode=mode)

    data_tar = _targz(add_data)

    installed_kb = sum(len(d) for d, _ in files.values()) // 1024 + 1
    control = (
        f"Package: {APP}\n"
        f"Version: {version}\n"
        "Section: games\n"
        "Priority: optional\n"
        "Architecture: all\n"
        "Depends: python3 (>= 3.8), python3-tk\n"
        f"Installed-Size: {installed_kb}\n"
        f"Maintainer: {MAINTAINER}\n"
        f"Description: {DESCRIPTION}\n"
        + "".join(f" {ln}\n" for ln in LONG_DESCRIPTION.splitlines()))

    def add_control(tar):
        _tar_add(tar, "./control", control.encode())

    control_tar = _targz(add_control)

    with open(out_path, "wb") as f:
        f.write(b"!<arch>\n")
        f.write(_ar_entry("debian-binary", b"2.0\n"))
        f.write(_ar_entry("control.tar.gz", control_tar))
        f.write(_ar_entry("data.tar.gz", data_tar))
    return out_path


def main():
    version = suite_version()
    os.makedirs(OUT, exist_ok=True)
    missing = [f for f in RUNTIME_FILES
               if not os.path.isfile(os.path.join(ROOT, f))]
    if missing:
        raise SystemExit(f"missing runtime files: {missing}")
    t = build_tarball(version)
    d = build_deb(version)
    for p in (t, d):
        print(f"wrote {p}  ({os.path.getsize(p):,} bytes)")


if __name__ == "__main__":
    main()
