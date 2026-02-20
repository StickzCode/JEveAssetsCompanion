#!/usr/bin/env python3
"""
jEveAssets Companion - backup service.

Copies jEveAssets profile files (.db, .xml, .xmlbackup, .BAC, .dat, .json)
to a user-configured local backup directory.  Each backup is stored in a
timestamped subfolder, preserving the relative directory structure.
"""

import shutil
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Optional

BACKUP_EXTENSIONS = {".db", ".xml", ".xmlbackup", ".bac", ".dat", ".json"}


def find_backup_files(data_dir: Path) -> List[Path]:
    """
    Recursively scan the jEveAssets data directory for files whose extension
    matches BACKUP_EXTENSIONS.  Returns absolute paths.
    """
    results = []
    if not data_dir.exists():
        return results
    for p in data_dir.rglob("*"):
        if p.is_file() and p.suffix.lower() in BACKUP_EXTENSIONS:
            results.append(p)
    return results


def run_backup(data_dir: Path, backup_dir: Path) -> dict:
    """
    Copy every matching file from *data_dir* into a timestamped subfolder
    under *backup_dir*, preserving relative paths.

    Returns a summary dict:
        {
            "dest": Path,          # the timestamped folder that was created
            "file_count": int,
            "total_bytes": int,
            "error": Optional[str],
        }
    """
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest = backup_dir / timestamp

    files = find_backup_files(data_dir)
    if not files:
        return {
            "dest": dest,
            "file_count": 0,
            "total_bytes": 0,
            "error": "No files found to back up.",
        }

    total_bytes = 0
    try:
        for src in files:
            rel = src.relative_to(data_dir)
            dst = dest / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            total_bytes += src.stat().st_size
    except Exception as e:
        return {
            "dest": dest,
            "file_count": 0,
            "total_bytes": 0,
            "error": str(e),
        }

    return {
        "dest": dest,
        "file_count": len(files),
        "total_bytes": total_bytes,
        "error": None,
    }


def should_backup(last_backup_iso: str, interval_hours: int = 24) -> bool:
    """
    Return True if *interval_hours* have elapsed since *last_backup_iso*
    (an ISO-format timestamp string), or if the string is empty / invalid.
    """
    if not last_backup_iso:
        return True
    try:
        last_dt = datetime.fromisoformat(last_backup_iso)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        elapsed_hours = (now - last_dt).total_seconds() / 3600
        return elapsed_hours >= interval_hours
    except (ValueError, TypeError):
        return True


def cleanup_old_backups(backup_dir: Path, keep: int = 7) -> int:
    """
    Delete the oldest timestamped backup folders, keeping the most recent
    *keep*.  Only considers immediate subdirectories whose names look like
    timestamps (YYYY-MM-DD_HHMMSS).

    Returns the number of folders removed.
    """
    if not backup_dir.exists():
        return 0

    folders = []
    for entry in backup_dir.iterdir():
        if entry.is_dir() and _is_timestamp_folder(entry.name):
            folders.append(entry)

    folders.sort(key=lambda p: p.name, reverse=True)

    removed = 0
    for old in folders[keep:]:
        try:
            shutil.rmtree(old)
            removed += 1
        except Exception:
            pass
    return removed


def _is_timestamp_folder(name: str) -> bool:
    """Check if a folder name matches the YYYY-MM-DD_HHMMSS pattern."""
    if len(name) != 17:
        return False
    try:
        datetime.strptime(name, "%Y-%m-%d_%H%M%S")
        return True
    except ValueError:
        return False
