#!/usr/bin/env python3
"""
EQL Log Reader -- Update check
================================
A tiny, deliberately dumb "is there a newer version?" check against this
project's GitHub releases. On purpose this module does NOT download or run
anything -- it only ever fetches a small release-metadata JSON blob over
plain HTTPS using the standard library, then hands the caller back a version
string + a release URL to show the user. No auto-download, no auto-run, no
silent file replacement, no bundled networking library, no bespoke protocol.

That's a deliberate choice, not laziness: this is exactly the kind of code
antivirus heuristics look hardest at, and "fetches a JSON blob, never writes
to disk" doesn't look anything like what a dropper/updater-malware pattern
does. If a more hands-off installer/relaunch flow is ever wanted later, it
should get its own explicit design pass (and ideally a code-signed exe)
rather than quietly growing out of this module.

Callers should always run check_for_update() in a background thread -- it
does a blocking network call with a short timeout, which is fine off the Tk
main loop but would freeze the UI if called directly from it.
"""

import json
import re
import urllib.error
import urllib.request

REPO = "blastlaster/eql-log-reader"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO}/releases/latest"
TIMEOUT = 4  # seconds -- never let a slow/flaky connection hang very long


def _parse_version(text):
    """Pull the first dotted-number run out of a version/tag string, e.g.
    "v1.2" -> (1, 2), "1.2" -> (1, 2), "EQL Log Reader 1.2" -> (1, 2),
    a bare "1" -> (1,). Returns None if no number is found at all. Tuples
    (rather than the raw string) let versions compare numerically, so
    "1.10" correctly counts as newer than "1.9"."""
    m = re.search(r"\d+(?:\.\d+)*", text or "")
    if not m:
        return None
    return tuple(int(part) for part in m.group(0).split("."))


def _is_newer(latest, current):
    """True if version tuple `latest` is greater than `current`, padding the
    shorter tuple with trailing zeros so (1, 2) and (1, 2, 0) compare equal."""
    length = max(len(latest), len(current))
    latest = latest + (0,) * (length - len(latest))
    current = current + (0,) * (length - len(current))
    return latest > current


def check_for_update(current_version, timeout=TIMEOUT):
    """Hit GitHub's public releases API once. Returns (version_label,
    release_url) if a newer release is published, or None if we're up to
    date, offline, rate-limited, or anything else goes wrong.

    Deliberately swallows every exception: this is a "nice to know"
    background check, never something that should pop an error dialog or
    block the app on its own -- most runs will be on a machine that's mid-
    game with the update check as a total afterthought.
    """
    try:
        req = urllib.request.Request(
            API_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "EQL-Log-Reader-Update-Check",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        tag = data.get("tag_name", "") or ""
        url = data.get("html_url") or RELEASES_URL

        latest = _parse_version(tag)
        current = _parse_version(current_version)
        if latest is None or current is None:
            return None
        if not _is_newer(latest, current):
            return None

        label = tag if tag.lower().startswith("v") else f"v{tag}"
        return (label, url)
    except Exception:
        return None
