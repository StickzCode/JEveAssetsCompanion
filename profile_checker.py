#!/usr/bin/env python3
"""
jEveAssets Companion - core profile-reading logic.

Reads a jEveAssets profile (XML or SQLite .db), finds ESI owners, and
returns how many days since each owner's data was last updated.

The caller decides the alert threshold; this module only returns the raw
(name, last_update_ms, days_ago) tuples.  DEFAULT_WARN_DAYS (14) is the
CLI fallback and matches the main app default in companion_app.py.
"""

import os
import sys
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Optional, Tuple

DEFAULT_WARN_DAYS = 14

def _default_profile_dir():
    base = os.environ.get("JEVEASSETS_DATA", os.path.expanduser("~"))
    return Path(base) / ".jeveassets"

def _find_profile_file(profile_dir: Path) -> Optional[Path]:
    profiles = profile_dir / "profiles"
    if not profiles.exists():
        return None
    candidates = [
        "#Default.db",
        "#Default.xml",
        "#Default.xmlbackup",
        "Default.db",
        "Default.xml",
    ]
    for name in candidates:
        p = profiles / name
        if p.exists():
            return p
    return None

# XML attributes on <esiowner> that hold "last update" timestamps (epoch ms).
# *nextupdate attributes are intentionally excluded -- they are future timestamps.
LAST_UPDATE_ATTRS = (
    "assetslastupdate",
    "balancelastupdate",
)

def _get_last_update_ms(esiowner: ET.Element) -> Optional[int]:
    latest = None
    for attr in LAST_UPDATE_ATTRS:
        val = esiowner.get(attr)
        if val and val.isdigit():
            ts = int(val)
            if latest is None or ts > latest:
                latest = ts
    return latest

def check_profile_db(profile_path: Path, warn_days: int = 0, debug: bool = False) -> List[Tuple[str, int, float]]:
    """
    Read profile from SQLite database and return list of (name, last_update_ms, days_ago).
    warn_days is accepted for interface consistency but filtering is done by the caller.
    """
    conn = sqlite3.connect(profile_path)
    cursor = conn.cursor()

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    ms_per_day = 24 * 60 * 60 * 1000
    results = []

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in cursor.fetchall()]

    if debug:
        print(f"DEBUG: Found tables: {tables}", file=sys.stderr)

    esi_table = None
    for table_name in ["esiowners", "owners", "accounts", "esi_owners"]:
        if table_name in tables:
            esi_table = table_name
            break

    if esi_table is None:
        if debug:
            print(f"DEBUG: No ESI owners table found. Available tables: {tables}", file=sys.stderr)
        conn.close()
        return results

    cursor.execute(f"PRAGMA table_info({esi_table})")
    columns = [row[1] for row in cursor.fetchall()]

    if debug:
        print(f"DEBUG: Table '{esi_table}' columns: {columns}", file=sys.stderr)

    name_col = None
    for col in ["name", "accountname", "character_name"]:
        if col in columns:
            name_col = col
            break

    invalid_col = None
    for col in ["invalid", "is_invalid", "disabled"]:
        if col in columns:
            invalid_col = col
            break

    timestamp_cols = []
    for col in columns:
        if "lastupdate" in col.lower() and "next" not in col.lower():
            timestamp_cols.append(col)

    if not timestamp_cols:
        for col in columns:
            if any(x in col.lower() for x in ["update", "timestamp", "last", "time"]):
                if "next" not in col.lower():
                    timestamp_cols.append(col)

    if debug:
        print(f"DEBUG: Using name column: {name_col}, timestamp columns: {timestamp_cols}", file=sys.stderr)

    select_cols = []
    if name_col:
        select_cols.append(name_col)
    if invalid_col:
        select_cols.append(invalid_col)
    select_cols.extend(timestamp_cols)

    if not select_cols:
        conn.close()
        return results

    query = f"SELECT {', '.join(select_cols)} FROM {esi_table}"
    cursor.execute(query)

    for row in cursor.fetchall():
        row_dict = dict(zip(select_cols, row))

        if invalid_col and row_dict.get(invalid_col):
            continue

        name = row_dict.get(name_col) or "Unknown"

        last_ms = None
        for col in timestamp_cols:
            val = row_dict.get(col)
            if val and isinstance(val, (int, str)):
                try:
                    ts = int(val)
                    if last_ms is None or ts > last_ms:
                        last_ms = ts
                except (ValueError, TypeError):
                    pass

        if last_ms is None:
            continue

        if debug:
            last_dt = datetime.fromtimestamp(last_ms / 1000)
            print(f"DEBUG {name}:", file=sys.stderr)
            print(f"  last_ms: {last_ms}  ({last_dt})", file=sys.stderr)
            print(f"  now_ms:  {now_ms}", file=sys.stderr)
            for col in timestamp_cols:
                print(f"  {col}: {row_dict.get(col)}", file=sys.stderr)

        days_ago = (now_ms - last_ms) / ms_per_day
        if days_ago < 0:
            continue
        results.append((name, last_ms, days_ago))

    conn.close()
    return results

def check_profile_xml(profile_path: Path, warn_days: int = 0, debug: bool = False) -> List[Tuple[str, int, float]]:
    """
    Parse profile XML and return list of (name, last_update_ms, days_ago).
    Only includes owners that have a last-update and are not marked invalid.
    warn_days is accepted for interface consistency but filtering is done by the caller.
    """
    tree = ET.parse(profile_path)
    root = tree.getroot()
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    ms_per_day = 24 * 60 * 60 * 1000
    results = []

    esiowners = root.find(".//esiowners")
    if esiowners is None:
        return results

    for owner in esiowners.findall("esiowner"):
        if owner.get("invalid") == "true":
            continue
        name = owner.get("name") or owner.get("accountname") or "Unknown"
        last_ms = _get_last_update_ms(owner)
        if last_ms is None:
            continue

        if debug:
            last_dt = datetime.fromtimestamp(last_ms / 1000)
            print(f"DEBUG {name}:", file=sys.stderr)
            print(f"  last_ms: {last_ms}  ({last_dt})", file=sys.stderr)
            print(f"  now_ms:  {now_ms}", file=sys.stderr)
            for attr in LAST_UPDATE_ATTRS:
                val = owner.get(attr)
                if val:
                    print(f"  {attr}: {val}", file=sys.stderr)

        days_ago = (now_ms - last_ms) / ms_per_day
        if days_ago < 0:
            continue
        results.append((name, last_ms, days_ago))

    return results

def check_profile(profile_path: Path, warn_days: int, debug: bool = False) -> List[Tuple[str, int, float]]:
    """Check profile file - supports both database (.db) and XML formats."""
    if profile_path.suffix.lower() == '.db':
        return check_profile_db(profile_path, warn_days, debug)
    else:
        return check_profile_xml(profile_path, warn_days, debug)

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Check jEveAssets ESI token freshness.")
    p.add_argument("--days", type=int, default=DEFAULT_WARN_DAYS,
                    help=f"Alert if no update in this many days (default: {DEFAULT_WARN_DAYS})")
    p.add_argument("--data-dir", type=Path, default=None,
                    help="jEveAssets data dir (default: ~/.jeveassets or JEVEASSETS_DATA)")
    p.add_argument("--quiet", action="store_true", help="Suppress output; exit code only.")
    p.add_argument("--debug", action="store_true", help="Show debug timestamp details.")
    args = p.parse_args()

    profile_dir = args.data_dir or _default_profile_dir()
    profile_path = _find_profile_file(profile_dir)

    if profile_path is None:
        if not args.quiet:
            print("No profile found.", file=sys.stderr)
            print(f"  Looked in: {profile_dir / 'profiles'}", file=sys.stderr)
        sys.exit(2)

    if not args.quiet:
        print(f"Using profile: {profile_path} ({'database' if profile_path.suffix == '.db' else 'XML'})", file=sys.stderr)

    owners = check_profile(profile_path, args.days, debug=args.debug)
    if not owners:
        if not args.quiet:
            print("No ESI owners found in profile (or all invalid).")
        sys.exit(0)

    owners.sort(key=lambda x: x[2], reverse=True)
    stale = [(n, ms, d) for n, ms, d in owners if d >= args.days]

    if stale:
        if not args.quiet:
            print(f"\n  {len(stale)} character(s) stale (>{args.days} days):\n")
            for name, _ms, days_ago in stale:
                print(f"    - {name}: {days_ago:.0f} days ago")
            print()
        sys.exit(1)

    if not args.quiet:
        for name, _ms, days_ago in owners:
            print(f"  {name}: OK ({days_ago:.0f} days ago)")
    sys.exit(0)
