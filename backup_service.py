#!/usr/bin/env python3
"""
jEveAssets Companion - backup service.

Creates zip-file backups of jEveAssets profile data (.db, .xml, .xmlbackup,
.BAC, .dat, .json) in a user-configured local directory.

Retention policy (applied automatically after each backup):
  - Daily:   keep the last 7 days
  - Weekly:  keep 1 per week for the last 4 weeks
  - Monthly: keep 1 per calendar month (indefinitely)

Naming convention:  YYYY-MM-DD_daily.zip  /  _weekly.zip  /  _monthly.zip
"""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path
from datetime import datetime, date, timedelta, timezone

BACKUP_EXTENSIONS = {".db", ".xml", ".xmlbackup", ".bac", ".dat", ".json"}
TIERS = ("daily", "weekly", "monthly")


# ---------------------------------------------------------------------------
# Discover files to back up
# ---------------------------------------------------------------------------

def find_backup_files(data_dir: Path) -> list[Path]:
    """Recursively find files in *data_dir* matching BACKUP_EXTENSIONS."""
    if not data_dir.exists():
        return []
    return [
        p for p in data_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in BACKUP_EXTENSIONS
    ]


# ---------------------------------------------------------------------------
# Create a backup
# ---------------------------------------------------------------------------

def run_backup(data_dir: Path, backup_dir: Path) -> dict:
    """
    Copy every matching file from *data_dir* into a temporary staging folder,
    then zip the staged copy into a daily backup zip under *backup_dir*.

    The original data files are **never** moved or modified -- only read.

    Returns::

        {
            "dest": Path,           # path to the created zip
            "file_count": int,
            "total_bytes": int,     # uncompressed size of source files
            "error": Optional[str],
        }
    """
    today = date.today().isoformat()
    zip_name = f"{today}_daily.zip"
    dest = backup_dir / zip_name

    files = find_backup_files(data_dir)
    if not files:
        return {"dest": dest, "file_count": 0, "total_bytes": 0,
                "error": "No files found to back up."}

    backup_dir.mkdir(parents=True, exist_ok=True)

    total_bytes = 0
    tmp_dir = None
    try:
        tmp_dir = Path(tempfile.mkdtemp(prefix="jeveassets_backup_"))

        for src in files:
            rel = src.relative_to(data_dir)
            staged = tmp_dir / rel
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, staged)
            total_bytes += src.stat().st_size

        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            for staged_file in tmp_dir.rglob("*"):
                if staged_file.is_file():
                    rel = staged_file.relative_to(tmp_dir)
                    zf.write(staged_file, arcname=str(rel))
    except Exception as e:
        return {"dest": dest, "file_count": 0, "total_bytes": 0,
                "error": str(e)}
    finally:
        if tmp_dir and tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return {"dest": dest, "file_count": len(files), "total_bytes": total_bytes,
            "error": None}


# ---------------------------------------------------------------------------
# Should a backup run now?
# ---------------------------------------------------------------------------

def should_backup(last_backup_iso: str, interval_hours: int = 24) -> bool:
    """
    Return True if *interval_hours* have elapsed since *last_backup_iso*
    (ISO timestamp), or if the string is empty / unparseable.
    """
    if not last_backup_iso:
        return True
    try:
        last_dt = datetime.fromisoformat(last_backup_iso)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
        return elapsed >= interval_hours
    except (ValueError, TypeError):
        return True


# ---------------------------------------------------------------------------
# Tiered retention / cleanup
# ---------------------------------------------------------------------------

def _parse_backup_zip(name: str) -> tuple[date, str] | None:
    """
    Parse a backup filename like ``2026-02-20_daily.zip`` into
    ``(date(2026, 2, 20), "daily")``.  Returns None on mismatch.
    """
    if not name.endswith(".zip"):
        return None
    stem = name[:-4]                       # strip .zip
    for tier in TIERS:
        suffix = f"_{tier}"
        if stem.endswith(suffix):
            date_str = stem[: -len(suffix)]
            try:
                d = date.fromisoformat(date_str)
                return (d, tier)
            except ValueError:
                return None
    return None


def _iso_week(d: date) -> tuple[int, int]:
    """Return (ISO year, ISO week number) for grouping."""
    return d.isocalendar()[:2]


def _year_month(d: date) -> tuple[int, int]:
    return (d.year, d.month)


def cleanup_old_backups(backup_dir: Path) -> int:
    """
    Apply the tiered retention policy and return the number of files removed.

    Algorithm:
      1. Parse every zip in *backup_dir*.
      2. Keep the 7 most recent dailies.
      3. Promote the newest daily per ISO-week (outside the daily window,
         within the last 4 weeks) to ``_weekly``; keep those.
      4. Promote the newest remaining backup per calendar month to
         ``_monthly``; keep those indefinitely.
      5. Delete everything else.
    """
    if not backup_dir.exists():
        return 0

    # Collect all backup zips: {Path: (date, tier)}
    backups: dict[Path, tuple[date, str]] = {}
    for entry in backup_dir.iterdir():
        if entry.is_file():
            parsed = _parse_backup_zip(entry.name)
            if parsed:
                backups[entry] = parsed

    if not backups:
        return 0

    today = date.today()
    daily_cutoff = today - timedelta(days=7)
    weekly_cutoff = today - timedelta(weeks=4)

    keep: set[Path] = set()

    # --- Step 1: keep the 7 most recent dailies ---
    dailies = sorted(
        [(p, d) for p, (d, t) in backups.items() if t == "daily"],
        key=lambda x: x[1], reverse=True,
    )
    for p, d in dailies[:7]:
        keep.add(p)

    # --- Step 2: promote one per week (outside daily window, within 4 wks) ---
    # Candidates: all backups in the date range (weekly_cutoff, daily_cutoff]
    weekly_candidates: dict[tuple[int, int], list[tuple[Path, date, str]]] = defaultdict(list)
    for p, (d, t) in backups.items():
        if weekly_cutoff < d <= daily_cutoff:
            weekly_candidates[_iso_week(d)].append((p, d, t))

    for week_key, entries in weekly_candidates.items():
        # Prefer an existing weekly, otherwise pick the newest
        existing_weekly = [e for e in entries if e[2] == "weekly"]
        if existing_weekly:
            winner = max(existing_weekly, key=lambda e: e[1])
            keep.add(winner[0])
        else:
            winner = max(entries, key=lambda e: e[1])
            new_name = f"{winner[1].isoformat()}_weekly.zip"
            new_path = backup_dir / new_name
            try:
                winner[0].rename(new_path)
                keep.add(new_path)
                backups[new_path] = (winner[1], "weekly")
                del backups[winner[0]]
            except Exception:
                keep.add(winner[0])

    # --- Step 3: promote one per calendar month (older than 4 weeks) ---
    monthly_candidates: dict[tuple[int, int], list[tuple[Path, date, str]]] = defaultdict(list)
    for p, (d, t) in backups.items():
        if d <= weekly_cutoff and p not in keep:
            monthly_candidates[_year_month(d)].append((p, d, t))

    for month_key, entries in monthly_candidates.items():
        existing_monthly = [e for e in entries if e[2] == "monthly"]
        if existing_monthly:
            winner = max(existing_monthly, key=lambda e: e[1])
            keep.add(winner[0])
        else:
            winner = max(entries, key=lambda e: e[1])
            new_name = f"{winner[1].isoformat()}_monthly.zip"
            new_path = backup_dir / new_name
            try:
                winner[0].rename(new_path)
                keep.add(new_path)
            except Exception:
                keep.add(winner[0])

    # Also keep any already-promoted monthlies that are still in range
    for p, (d, t) in backups.items():
        if t == "monthly":
            keep.add(p)

    # --- Step 4: delete everything not kept ---
    removed = 0
    for p in list(backups.keys()):
        if p not in keep and p.exists():
            try:
                p.unlink()
                removed += 1
            except Exception:
                pass
    return removed
