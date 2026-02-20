#!/usr/bin/env python3
"""
jEveAssets Companion - companion app for jEveAssets.

Monitors your jEveAssets profile data and alerts you when ESI tokens
haven't been refreshed within a configurable threshold.

Modes:
  (default)   System tray icon with background monitoring + toast notifications
  --check     One-shot CLI check (prints status and exits)

Config is stored in %APPDATA%/jEveAssetsCompanion/config.json and
can be edited via the Settings dialog in the tray menu.
"""

from __future__ import annotations

import os
import sys
import json
import time
import threading
import subprocess
import ctypes
import tkinter as tk
from tkinter import ttk, filedialog
from pathlib import Path
from datetime import datetime, timezone

APP_NAME = "jEveAssets Companion"

# ---------------------------------------------------------------------------
# Single-instance guard (Windows named mutex)
# ---------------------------------------------------------------------------

_MUTEX_NAME = "Global\\jEveAssetsCompanion_SingleInstance"
_mutex_handle = None

def _acquire_single_instance() -> bool:
    """Try to create a named mutex. Returns False if another instance owns it."""
    global _mutex_handle
    _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, True, _MUTEX_NAME)
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS

try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Missing dependencies. Install with:  pip install pystray pillow", file=sys.stderr)
    sys.exit(1)

from profile_checker import (
    check_profile,
    _default_profile_dir,
    _find_profile_file,
    DEFAULT_WARN_DAYS,
)
from backup_service import (
    run_backup,
    should_backup,
    cleanup_old_backups,
)

# ---------------------------------------------------------------------------
# Config file
# ---------------------------------------------------------------------------

def _config_path() -> Path:
    return Path(os.environ.get("APPDATA", os.path.expanduser("~"))) / "jEveAssetsCompanion" / "config.json"

_DEFAULT_CONFIG = {
    "warn_days": 14,
    "check_interval": 3600,
    "reminder_hours": 24,
    "jeveassets_path": "",
    "use_jmem": False,
    "data_dir": "",
    "backup_enabled": True,
    "backup_dir": "",
    "backup_interval_hours": 24,
    "last_backup_time": "",
}

def load_config() -> dict:
    path = _config_path()
    cfg = dict(_DEFAULT_CONFIG)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                stored = json.load(f)
            cfg.update({k: stored[k] for k in _DEFAULT_CONFIG if k in stored})
        except Exception:
            pass
    else:
        save_config(cfg)
    return cfg

def save_config(cfg: dict):
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


# ---------------------------------------------------------------------------
# Startup shortcut management
# ---------------------------------------------------------------------------

def _startup_shortcut_path() -> Path:
    startup = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    return startup / "jEveAssetsCompanion.lnk"

def is_startup_enabled() -> bool:
    return _startup_shortcut_path().exists()

def _get_exe_path() -> str:
    if getattr(sys, 'frozen', False):
        return sys.executable
    return str(Path(__file__).resolve())

def set_startup_enabled(enabled: bool):
    shortcut_path = _startup_shortcut_path()
    if enabled:
        target = _get_exe_path()
        ps_script = (
            f'$ws = New-Object -ComObject WScript.Shell; '
            f'$sc = $ws.CreateShortcut("{shortcut_path}"); '
            f'$sc.TargetPath = "{target}"; '
            f'$sc.WorkingDirectory = "{Path(target).parent}"; '
            f'$sc.Description = "{APP_NAME}"; '
            f'$sc.Save()'
        )
        try:
            subprocess.run(
                ["powershell", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
                capture_output=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception:
            pass
    else:
        try:
            shortcut_path.unlink(missing_ok=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Icon helpers
# ---------------------------------------------------------------------------

def _create_icon_image(state: str = "ok") -> Image.Image:
    """Generate a 64x64 tray icon: green="ok", red="warn", grey="error"."""
    colours = {
        "ok":    ((34, 139, 34),  (255, 255, 255)),
        "warn":  ((200, 60, 30),  (255, 255, 255)),
        "error": ((120, 120, 120),(255, 255, 255)),
    }
    bg, fg = colours.get(state, colours["ok"])
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([2, 2, 62, 62], radius=12, fill=bg)
    try:
        font = ImageFont.truetype("arialbd.ttf", 36)
    except Exception:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), "T", font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((64 - tw) / 2 - bbox[0], (64 - th) / 2 - bbox[1]), "T", fill=fg, font=font)
    return img


# ---------------------------------------------------------------------------
# Windows toast notification
# ---------------------------------------------------------------------------

def show_notification(title: str, message: str):
    message_escaped = message.replace('"', '`"').replace("$", '`$').replace("\n", " ")
    title_escaped = title.replace('"', '`"').replace("$", '`$')

    ps_script = f'''
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null

$xml = [Windows.Data.Xml.Dom.XmlDocument]::new()
$xml.LoadXml(@"
<toast>
    <visual>
        <binding template="ToastText02">
            <text id="1">{title_escaped}</text>
            <text id="2">{message_escaped}</text>
        </binding>
    </visual>
</toast>
"@)

$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("{APP_NAME}").Show($toast)
'''
    try:
        subprocess.run(
            ["powershell", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Settings dialog  (tkinter)
# ---------------------------------------------------------------------------

def show_settings_dialog(cfg: dict, on_save=None):
    root = tk.Tk()
    root.title(f"{APP_NAME} - Settings")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    frame = ttk.Frame(root, padding=16)
    frame.grid(sticky="nsew")

    row = 0

    ttk.Label(frame, text="Alert threshold (days):").grid(row=row, column=0, sticky="w", pady=(0, 6))
    var_days = tk.IntVar(value=cfg["warn_days"])
    ttk.Spinbox(frame, from_=1, to=365, textvariable=var_days, width=8).grid(row=row, column=1, sticky="w", pady=(0, 6))
    row += 1

    ttk.Label(frame, text="Check interval (minutes):").grid(row=row, column=0, sticky="w", pady=(0, 6))
    var_interval = tk.IntVar(value=cfg["check_interval"] // 60)
    ttk.Spinbox(frame, from_=1, to=1440, textvariable=var_interval, width=8).grid(row=row, column=1, sticky="w", pady=(0, 6))
    row += 1

    ttk.Label(frame, text="Reminder interval (hours):").grid(row=row, column=0, sticky="w", pady=(0, 6))
    var_reminder = tk.IntVar(value=cfg.get("reminder_hours", 24))
    ttk.Spinbox(frame, from_=1, to=168, textvariable=var_reminder, width=8).grid(row=row, column=1, sticky="w", pady=(0, 6))
    row += 1

    ttk.Separator(frame, orient="horizontal").grid(row=row, column=0, columnspan=3, sticky="ew", pady=8)
    row += 1

    ttk.Label(frame, text="jEveAssets folder:").grid(row=row, column=0, sticky="w", pady=(0, 6))
    var_path = tk.StringVar(value=cfg["jeveassets_path"])
    ttk.Entry(frame, textvariable=var_path, width=36).grid(row=row, column=1, sticky="w", pady=(0, 6))

    def browse_folder():
        initial = var_path.get().strip()
        if initial and Path(initial).is_dir():
            start = initial
        else:
            start = str(Path.home())
        d = filedialog.askdirectory(title="Select jEveAssets installation folder", initialdir=start)
        if d:
            var_path.set(d)

    ttk.Button(frame, text="...", width=3, command=browse_folder).grid(row=row, column=2, padx=(4, 0), pady=(0, 6))
    row += 1

    var_jmem = tk.BooleanVar(value=cfg["use_jmem"])
    ttk.Checkbutton(frame, text="Use jmemory.jar instead of jeveassets.jar", variable=var_jmem).grid(
        row=row, column=0, columnspan=3, sticky="w", pady=(0, 6)
    )
    row += 1

    ttk.Separator(frame, orient="horizontal").grid(row=row, column=0, columnspan=3, sticky="ew", pady=8)
    row += 1

    ttk.Label(frame, text="Data directory (blank = auto):").grid(row=row, column=0, sticky="w", pady=(0, 6))
    var_datadir = tk.StringVar(value=cfg.get("data_dir", ""))
    ttk.Entry(frame, textvariable=var_datadir, width=36).grid(row=row, column=1, sticky="w", pady=(0, 6))

    def browse_data():
        initial = var_datadir.get().strip()
        if initial and Path(initial).is_dir():
            start = initial
        else:
            start = str(_default_profile_dir())
        d = filedialog.askdirectory(title="Select jEveAssets data directory (.jeveassets)", initialdir=start)
        if d:
            var_datadir.set(d)

    ttk.Button(frame, text="...", width=3, command=browse_data).grid(row=row, column=2, padx=(4, 0), pady=(0, 6))
    row += 1

    ttk.Separator(frame, orient="horizontal").grid(row=row, column=0, columnspan=3, sticky="ew", pady=8)
    row += 1

    # -- Backup section --
    ttk.Label(frame, text="Backup", font=("Segoe UI", 9, "bold")).grid(row=row, column=0, sticky="w", pady=(0, 4))
    row += 1

    var_backup_enabled = tk.BooleanVar(value=cfg.get("backup_enabled", True))
    ttk.Checkbutton(frame, text="Enable automatic backups", variable=var_backup_enabled).grid(
        row=row, column=0, columnspan=3, sticky="w", pady=(0, 6)
    )
    row += 1

    ttk.Label(frame, text="Backup directory:").grid(row=row, column=0, sticky="w", pady=(0, 6))
    var_backup_dir = tk.StringVar(value=cfg.get("backup_dir", ""))
    ttk.Entry(frame, textvariable=var_backup_dir, width=36).grid(row=row, column=1, sticky="w", pady=(0, 6))

    def browse_backup():
        initial = var_backup_dir.get().strip()
        if initial and Path(initial).is_dir():
            start = initial
        else:
            start = str(Path.home())
        d = filedialog.askdirectory(title="Select backup directory", initialdir=start)
        if d:
            var_backup_dir.set(d)

    ttk.Button(frame, text="...", width=3, command=browse_backup).grid(row=row, column=2, padx=(4, 0), pady=(0, 6))
    row += 1

    last_backup = cfg.get("last_backup_time", "")
    if last_backup:
        try:
            dt = datetime.fromisoformat(last_backup)
            last_backup_display = dt.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            last_backup_display = last_backup
    else:
        last_backup_display = "Never"
    lbl_last_backup = ttk.Label(frame, text=f"Last backup: {last_backup_display}")
    lbl_last_backup.grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 6))

    def _backup_worker(data_dir, bdir):
        result = run_backup(data_dir, Path(bdir))
        if result["error"]:
            ctypes.windll.user32.MessageBoxW(0, f"Backup failed:\n{result['error']}", "Backup Error", 0x10)
        else:
            cleanup_old_backups(Path(bdir))
            now_iso = datetime.now(timezone.utc).isoformat()
            cfg["last_backup_time"] = now_iso
            save_config(cfg)
            root.after(0, lambda: lbl_last_backup.config(
                text=f"Last backup: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"))
            size_mb = result["total_bytes"] / (1024 * 1024)
            ctypes.windll.user32.MessageBoxW(
                0,
                f"Backup complete!\n\n"
                f"Files: {result['file_count']}\n"
                f"Size: {size_mb:.1f} MB\n"
                f"Location: {result['dest']}",
                "Backup",
                0x40,
            )
        root.after(0, lambda: btn_backup.config(state="normal"))

    def do_backup_now():
        data_dir_str = var_datadir.get().strip()
        data_dir = Path(data_dir_str) if data_dir_str else _default_profile_dir()
        bdir = var_backup_dir.get().strip()
        if not bdir:
            ctypes.windll.user32.MessageBoxW(0, "Please set a backup directory first.", "Backup", 0x30)
            return
        cfg["backup_dir"] = bdir
        btn_backup.config(state="disabled")
        threading.Thread(target=_backup_worker, args=(data_dir, bdir), daemon=True).start()

    btn_backup = ttk.Button(frame, text="Backup Now", command=do_backup_now)
    btn_backup.grid(row=row, column=2, padx=(4, 0), pady=(0, 6))
    row += 1

    ttk.Separator(frame, orient="horizontal").grid(row=row, column=0, columnspan=3, sticky="ew", pady=8)
    row += 1

    var_startup = tk.BooleanVar(value=is_startup_enabled())
    ttk.Checkbutton(frame, text="Run at Windows startup", variable=var_startup).grid(
        row=row, column=0, columnspan=3, sticky="w", pady=(0, 6)
    )
    row += 1

    saved = [False]

    def do_save():
        cfg["warn_days"] = max(1, var_days.get())
        cfg["check_interval"] = max(60, var_interval.get() * 60)
        cfg["reminder_hours"] = max(1, var_reminder.get())
        cfg["jeveassets_path"] = var_path.get().strip()
        cfg["use_jmem"] = var_jmem.get()
        cfg["data_dir"] = var_datadir.get().strip()
        cfg["backup_enabled"] = var_backup_enabled.get()
        cfg["backup_dir"] = var_backup_dir.get().strip()
        save_config(cfg)
        set_startup_enabled(var_startup.get())
        saved[0] = True
        root.destroy()

    def do_cancel():
        root.destroy()

    def open_config_folder():
        config_file = _config_path()
        config_file.parent.mkdir(parents=True, exist_ok=True)
        if config_file.exists():
            subprocess.Popen(["explorer", "/select,", str(config_file)])
        else:
            subprocess.Popen(["explorer", str(config_file.parent)])

    btn_frame = ttk.Frame(frame)
    btn_frame.grid(row=row, column=0, columnspan=3, pady=(8, 0))
    ttk.Button(btn_frame, text="Save", command=do_save).pack(side="left", padx=(0, 8))
    ttk.Button(btn_frame, text="Cancel", command=do_cancel).pack(side="left")
    ttk.Button(btn_frame, text="Open Config Folder", command=open_config_folder).pack(side="left", padx=(8, 0))

    root.mainloop()
    if saved[0] and on_save:
        on_save(cfg)
    return saved[0]


# ---------------------------------------------------------------------------
# Status window  (tkinter)
# ---------------------------------------------------------------------------

def show_status_window(profile_path: Path, warn_days: int, cfg: dict = None,
                       on_launch=None, on_settings=None):
    try:
        owners = check_profile(profile_path, warn_days, debug=False)
    except Exception as e:
        ctypes.windll.user32.MessageBoxW(0, f"Error reading profile:\n{e}", "Error", 0x10)
        return

    root = tk.Tk()
    root.title(APP_NAME)
    root.resizable(True, True)
    root.attributes("-topmost", True)
    root.minsize(420, 250)

    frame = ttk.Frame(root, padding=12)
    frame.grid(sticky="nsew")
    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)

    if not owners:
        ttk.Label(frame, text="No ESI owners found in profile.").grid(sticky="w")
        ttk.Button(frame, text="Close", command=root.destroy).grid(pady=(12, 0))
        root.mainloop()
        return

    owners.sort(key=lambda x: x[2], reverse=True)
    stale = [o for o in owners if o[2] >= warn_days]

    if stale:
        header = ttk.Label(frame, text=f"{len(stale)} of {len(owners)} character(s) need attention", foreground="red")
    else:
        header = ttk.Label(frame, text=f"All {len(owners)} character(s) are OK", foreground="green")
    header.grid(row=0, column=0, sticky="w", pady=(0, 8))
    header.configure(font=("Segoe UI", 11, "bold"))

    cols = ("status", "character", "last_update")
    tree = ttk.Treeview(frame, columns=cols, show="headings", height=min(len(owners), 12))
    tree.heading("status", text="Status")
    tree.heading("character", text="Character")
    tree.heading("last_update", text="Last Update (days ago)")
    tree.column("status", width=50, anchor="center")
    tree.column("character", width=180)
    tree.column("last_update", width=150, anchor="center")

    for name, _ms, days_ago in owners:
        status = "!!" if days_ago >= warn_days else "OK"
        tree.insert("", "end", values=(status, name, f"{days_ago:.0f}"))

    tree.grid(row=1, column=0, sticky="nsew", pady=(0, 8))
    frame.rowconfigure(1, weight=1)

    scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)
    scrollbar.grid(row=1, column=1, sticky="ns", pady=(0, 8))

    info = ttk.Label(frame, text=f"Threshold: {warn_days} days  |  Profile: {profile_path.name}  |  {datetime.now().strftime('%H:%M:%S')}")
    info.grid(row=2, column=0, sticky="w")

    btn_frame = ttk.Frame(frame)
    btn_frame.grid(row=3, column=0, columnspan=2, pady=(8, 0), sticky="ew")

    if on_launch is not None:
        jar_label = "jmemory.jar" if cfg and cfg.get("use_jmem") else "jeveassets.jar"
        ttk.Button(btn_frame, text=f"Launch jEveAssets ({jar_label})", command=on_launch).pack(side="left", padx=(0, 8))

    if on_settings is not None:
        ttk.Button(btn_frame, text="Settings", command=lambda: (root.destroy(), on_settings())).pack(side="left", padx=(0, 8))

    ttk.Button(btn_frame, text="Close", command=root.destroy).pack(side="right")

    root.mainloop()


# ---------------------------------------------------------------------------
# Launch jEveAssets
# ---------------------------------------------------------------------------

def _find_jeveassets_jar(cfg: dict) -> Path | None:
    jar_name = "jmemory.jar" if cfg.get("use_jmem") else "jeveassets.jar"
    jeveassets_dir = cfg.get("jeveassets_path", "").strip()
    if jeveassets_dir:
        candidate = Path(jeveassets_dir) / jar_name
        if candidate.exists():
            return candidate
    common_locations = [
        Path(os.environ.get("PROGRAMFILES", "C:\\Program Files")) / "jEveAssets",
        Path(os.environ.get("LOCALAPPDATA", "")) / "jEveAssets",
        Path.home() / "Desktop" / "jEveAssets",
        Path.home() / "jEveAssets",
    ]
    for loc in common_locations:
        candidate = loc / jar_name
        if candidate.exists():
            return candidate
    return None


def open_jeveassets(cfg: dict):
    jar = _find_jeveassets_jar(cfg)
    if jar is None:
        jar_name = "jmemory.jar" if cfg.get("use_jmem") else "jeveassets.jar"
        ctypes.windll.user32.MessageBoxW(
            0,
            f"Could not find {jar_name}.\n\n"
            "Please set the jEveAssets folder in Settings.",
            "jEveAssets Not Found",
            0x30,
        )
        return
    try:
        subprocess.Popen(["javaw", "-jar", str(jar)], cwd=str(jar.parent))
    except Exception as e:
        ctypes.windll.user32.MessageBoxW(0, f"Failed to launch jEveAssets:\n{e}", "Error", 0x10)


# ---------------------------------------------------------------------------
# Tray application
# ---------------------------------------------------------------------------

class CompanionApp:
    def __init__(
        self,
        profile_path: Path,
        cfg: dict,
        log_file: Path | None = None,
    ):
        self.profile_path = profile_path
        self.cfg = cfg
        self.warn_days = cfg["warn_days"]
        self.check_interval = cfg["check_interval"]
        self.log_file = log_file
        self.running = True
        self.icon: pystray.Icon | None = None
        self._last_alert: dict[str, float] = {}
        self._stale_count = 0
        self._total_count = 0
        self._seconds_until_check = 0

    def _log(self, msg: str):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {msg}"
        if self.log_file:
            try:
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                pass

    def _update_tooltip(self):
        if not self.icon:
            return
        mins_left = max(0, self._seconds_until_check) // 60
        secs_left = max(0, self._seconds_until_check) % 60
        if self._stale_count > 0:
            status = f"{self._stale_count} token(s) need attention"
        elif self._total_count > 0:
            status = f"All {self._total_count} token(s) OK"
        else:
            status = "Starting..."
        self.icon.title = f"{APP_NAME}\n{status}\nNext check: {mins_left}m {secs_left:02d}s"

    def _set_icon_state(self, state: str):
        if self.icon:
            self.icon.icon = _create_icon_image(state)

    def do_check(self, notify: bool = True) -> bool:
        try:
            owners = check_profile(self.profile_path, self.warn_days, debug=False)
            stale = [(n, ms, d) for n, ms, d in owners if d >= self.warn_days]
            self._total_count = len(owners)
            self._stale_count = len(stale)

            if stale:
                now = time.time()
                reminder_seconds = self.cfg.get("reminder_hours", 24) * 3600
                due = [
                    (name, days_ago)
                    for name, _ms, days_ago in stale
                    if (now - self._last_alert.get(name, 0)) > reminder_seconds
                ]
                if notify and due:
                    lines = [f"{name}: {days_ago:.0f} days" for name, days_ago in due]
                    show_notification(
                        f"jEveAssets Token Alert - {len(due)} character(s)",
                        ", ".join(lines) + " - please refresh in jEveAssets.",
                    )
                    for name, _ in due:
                        self._last_alert[name] = now
                self._set_icon_state("warn")
                self._log(f"Check: {len(stale)}/{len(owners)} stale")
                return False

            self._set_icon_state("ok")
            self._log(f"Check: all {len(owners)} OK")
            return True

        except Exception as e:
            self._set_icon_state("error")
            self._log(f"Error: {e}")
            return False

    def _show_status(self):
        show_status_window(
            self.profile_path, self.warn_days, cfg=self.cfg,
            on_launch=lambda: open_jeveassets(self.cfg),
            on_settings=lambda: show_settings_dialog(self.cfg, on_save=self._apply_settings),
        )

    def _apply_settings(self, new_cfg):
        self.cfg = new_cfg
        self.warn_days = new_cfg["warn_days"]
        self.check_interval = new_cfg["check_interval"]
        self._seconds_until_check = min(self._seconds_until_check, self.check_interval)
        self.do_check(notify=False)
        self._update_tooltip()

    def _try_scheduled_backup(self):
        if not self.cfg.get("backup_enabled", True):
            return
        if not self.cfg.get("backup_dir", "").strip():
            return
        interval = self.cfg.get("backup_interval_hours", 24)
        last = self.cfg.get("last_backup_time", "")
        if should_backup(last, interval):
            self._do_backup(notify=True)

    def _checker_loop(self):
        self.do_check(notify=True)
        self._try_scheduled_backup()
        self._seconds_until_check = self.check_interval
        threading.Thread(target=self._show_status, daemon=True).start()
        while self.running:
            time.sleep(1)
            self._seconds_until_check -= 1
            if self._seconds_until_check % 5 == 0:
                self._update_tooltip()
            if self._seconds_until_check <= 0:
                if self.running:
                    self.do_check(notify=True)
                    self._try_scheduled_backup()
                self._seconds_until_check = self.check_interval

    # -- menu callbacks -----------------------------------------------------

    def _on_show_status(self, _icon, _item):
        threading.Thread(target=self._show_status, daemon=True).start()

    def _get_data_dir(self) -> Path:
        data_dir_str = self.cfg.get("data_dir", "").strip()
        return Path(data_dir_str) if data_dir_str else _default_profile_dir()

    def _do_backup(self, notify: bool = True) -> bool:
        backup_dir = self.cfg.get("backup_dir", "").strip()
        if not backup_dir:
            self._log("Backup skipped: no backup directory configured")
            return False

        data_dir = self._get_data_dir()
        result = run_backup(data_dir, Path(backup_dir))

        if result["error"]:
            self._log(f"Backup failed: {result['error']}")
            if notify:
                show_notification("Backup Failed", result["error"])
            return False

        cleanup_old_backups(Path(backup_dir))
        now_iso = datetime.now(timezone.utc).isoformat()
        self.cfg["last_backup_time"] = now_iso
        save_config(self.cfg)

        size_mb = result["total_bytes"] / (1024 * 1024)
        self._log(f"Backup complete: {result['file_count']} files, {size_mb:.1f} MB -> {result['dest']}")
        if notify:
            show_notification(
                "Backup Complete",
                f"{result['file_count']} files ({size_mb:.1f} MB) backed up.",
            )
        return True

    def _on_backup_now(self, _icon, _item):
        threading.Thread(target=self._do_backup, daemon=True).start()

    def _on_open_jeveassets(self, _icon, _item):
        threading.Thread(target=lambda: open_jeveassets(self.cfg), daemon=True).start()

    def _on_settings(self, _icon, _item):
        threading.Thread(
            target=lambda: show_settings_dialog(self.cfg, on_save=self._apply_settings),
            daemon=True,
        ).start()

    def _on_quit(self, icon, _item):
        self.running = False
        icon.stop()

    def run(self):
        jar_label = "jmemory.jar" if self.cfg.get("use_jmem") else "jeveassets.jar"
        menu = pystray.Menu(
            pystray.MenuItem("Show Status", self._on_show_status, default=True),
            pystray.MenuItem(f"Open jEveAssets ({jar_label})", self._on_open_jeveassets),
            pystray.MenuItem("Backup Now", self._on_backup_now),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Settings", self._on_settings),
            pystray.MenuItem("Quit", self._on_quit),
        )

        self.icon = pystray.Icon(
            APP_NAME,
            _create_icon_image("ok"),
            f"{APP_NAME}\nStarting...",
            menu,
        )

        checker = threading.Thread(target=self._checker_loop, daemon=True)
        checker.start()

        self.icon.run()


# ---------------------------------------------------------------------------
# CLI one-shot check
# ---------------------------------------------------------------------------

def cli_check(args, cfg):
    data_dir_str = args.data_dir or cfg.get("data_dir", "").strip()
    profile_dir = Path(data_dir_str) if data_dir_str else _default_profile_dir()
    profile_path = _find_profile_file(profile_dir)
    days = args.days if args.days is not None else cfg.get("warn_days", DEFAULT_WARN_DAYS)

    if profile_path is None:
        if not args.quiet:
            print(f"{APP_NAME}: no profile found.", file=sys.stderr)
            print(f"  Looked in: {profile_dir / 'profiles'}", file=sys.stderr)
            profiles_dir = profile_dir / "profiles"
            if profiles_dir.exists():
                for f in profiles_dir.iterdir():
                    print(f"    - {f.name}", file=sys.stderr)
        sys.exit(2)

    if not args.quiet:
        fmt = "database" if profile_path.suffix == ".db" else "XML"
        print(f"Using profile: {profile_path} ({fmt})", file=sys.stderr)

    owners = check_profile(profile_path, days, debug=args.debug)
    if not owners:
        if not args.quiet:
            print("No ESI owners found in profile (or all invalid).")
        sys.exit(0)

    owners.sort(key=lambda x: x[2], reverse=True)
    stale = [(n, ms, d) for n, ms, d in owners if d >= days]

    if stale:
        if not args.quiet:
            print()
            print("  *** jEveAssets ESI Token Alert ***")
            print()
            print(f"  {len(stale)} character(s) have not been updated in at least {days} days.")
            print("  Please open jEveAssets and refresh your data, or re-authorize ESI if needed.")
            print()
            for name, _ms, days_ago in stale:
                print(f"    - {name}: last update {days_ago:.0f} days ago")
            print()
        sys.exit(1)

    if not args.quiet:
        for name, _ms, days_ago in owners:
            print(f"  {name}: OK (last update {days_ago:.0f} days ago)")
    sys.exit(0)


# ---------------------------------------------------------------------------
# Argument parsing & dispatch
# ---------------------------------------------------------------------------

def main():
    import argparse

    cfg = load_config()

    p = argparse.ArgumentParser(
        description=f"{APP_NAME} - system tray monitor and CLI tool.",
    )
    p.add_argument("--check", action="store_true", help="Run a one-shot CLI check and exit.")
    p.add_argument("--days", type=int, default=None, help=f"Alert threshold in days (default: {DEFAULT_WARN_DAYS}).")
    p.add_argument("--data-dir", type=Path, default=None, help="jEveAssets data directory override.")
    p.add_argument("--check-interval", type=int, default=None, help="Check interval in seconds (overrides config).")
    p.add_argument("--log-file", type=Path, default=None, help="Optional log file for tray mode.")
    p.add_argument("--quiet", action="store_true", help="CLI mode: suppress output.")
    p.add_argument("--debug", action="store_true", help="CLI mode: show debug info.")

    args = p.parse_args()

    # ----- CLI one-shot mode -----
    if args.check:
        cli_check(args, cfg)
        return

    # ----- Tray mode (default) -----
    if not _acquire_single_instance():
        ctypes.windll.user32.MessageBoxW(
            0,
            f"{APP_NAME} is already running.\n\n"
            "Look for the \"T\" icon in your system tray (bottom-right of taskbar).",
            "Already Running",
            0x40,
        )
        sys.exit(0)

    if args.check_interval:
        cfg["check_interval"] = args.check_interval
    if args.days is not None:
        cfg["warn_days"] = args.days

    def _resolve_profile(cfg, args):
        data_dir_str = str(args.data_dir) if args.data_dir else cfg.get("data_dir", "").strip()
        profile_dir = Path(data_dir_str) if data_dir_str else _default_profile_dir()
        return _find_profile_file(profile_dir), profile_dir

    profile_path, profile_dir = _resolve_profile(cfg, args)

    while profile_path is None:
        msg = (
            f"No jEveAssets profile found in:\n"
            f"{profile_dir / 'profiles'}\n\n"
            "Would you like to open Settings to configure the data directory?"
        )
        # MB_YESNO | MB_ICONWARNING = 0x34, IDYES = 6
        answer = ctypes.windll.user32.MessageBoxW(0, msg, f"{APP_NAME} - Setup", 0x34)
        if answer != 6:
            sys.exit(2)
        show_settings_dialog(cfg)
        save_config(cfg)
        profile_path, profile_dir = _resolve_profile(cfg, args)

    log_file = args.log_file
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)

    app = CompanionApp(profile_path, cfg, log_file=log_file)
    try:
        app.run()
    except KeyboardInterrupt:
        app.running = False
        if app.icon:
            app.icon.stop()


if __name__ == "__main__":
    main()
