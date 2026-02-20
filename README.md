# jEveAssets Companion

A single-exe companion tool for [jEveAssets](https://github.com/GoldenGnu/jeveassets) that monitors your ESI tokens and alerts you when they haven't been refreshed recently.

It reads your jEveAssets profile (`.db` or `.xml`), checks the last-update timestamp for each character, and alerts if any are older than a configurable threshold (default **14 days**).

## Features

- **System tray icon** -- green "T" when all tokens are OK, red when attention is needed.
- **Status window on launch** -- automatically shows a summary of all characters when the app starts, with buttons to launch jEveAssets or open Settings.
- **Background monitoring** -- checks periodically (default: every hour) and shows a single Windows toast notification listing all stale tokens.
- **Reminder interval** -- re-notifies you about stale tokens at a configurable interval (default: every 24 hours) so they don't get lost.
- **Status window** -- double-click the tray icon (or right-click > *Show Status*) to see a table of all characters and their token age.
- **Settings dialog** -- configure alert threshold, check interval, reminder interval, jEveAssets path, data directory, and startup behavior -- all from a GUI.
- **Open jEveAssets** -- launch `jeveassets.jar` or `jmemory.jar` directly from the tray menu or the status window.
- **Run at Windows startup** -- toggle in Settings; no separate scripts needed.
- **Single-instance guard** -- prevents multiple copies from running at the same time.
- **CLI mode** -- run with `--check` for a one-shot command-line check (useful for scripts / Task Scheduler).
- **Persistent config** -- settings are stored in `%APPDATA%\jEveAssetsCompanion\config.json`. Use the "Open Config Folder" button in Settings to locate it.

## Building

1. **Install Python 3.10+** if you don't already have it.

2. **Install dependencies** (one-time):

```
pip install pyinstaller pystray pillow
```

3. **Run the build script** from the project directory:

```
build_app.bat
```

This creates a single executable: **`dist\jEveAssetsCompanion.exe`**

4. **Distribute**: copy the `.exe` anywhere you like. No Python installation needed on the target machine.

## Usage

### System tray mode (default)

Double-click `jEveAssetsCompanion.exe` (or run it with no arguments). A "T" icon appears in your system tray and a status window shows your current token state.

**Right-click menu:**

| Item | What it does |
|------|-------------|
| **Show Status** | Opens a window listing every character and their token age (also triggered by double-click) |
| **Open jEveAssets** | Launches `jeveassets.jar` (or `jmemory.jar` if configured) |
| **Settings** | Opens the settings dialog |
| **Quit** | Exits the app |

**Status window buttons:**

| Button | What it does |
|--------|-------------|
| **Launch jEveAssets** | Opens jEveAssets (same as the tray menu option) |
| **Settings** | Opens the settings dialog |
| **Close** | Closes the status window (the tray icon keeps running) |

**Settings dialog fields:**

| Setting | Default | Description |
|---------|---------|-------------|
| Alert threshold | 14 days | Warn when a token hasn't been updated in this many days |
| Check interval | 60 min | How often the background check runs |
| Reminder interval | 24 hours | How often to re-notify about the same stale token |
| jEveAssets folder | (auto) | Path to your jEveAssets installation (for "Open jEveAssets") |
| Use jmemory.jar | off | Launch `jmemory.jar` instead of `jeveassets.jar` |
| Data directory | (auto) | Override the jEveAssets data directory (`~/.jeveassets`) |
| Run at Windows startup | off | Create/remove a startup shortcut so the app launches on login |

The "Open Config Folder" button at the bottom opens the folder containing `config.json` in Windows Explorer.

### CLI mode (one-shot)

```
jEveAssetsCompanion.exe --check
```

Prints the status of all characters and exits.

**CLI options:**

| Option | Description |
|--------|-------------|
| `--check` | Required to enter CLI mode |
| `--days N` | Alert threshold (default: 14) |
| `--quiet` | No output; only the exit code matters |
| `--debug` | Show raw timestamp details |
| `--data-dir PATH` | Override data directory |

**Exit codes:**

| Code | Meaning |
|------|---------|
| 0 | All characters updated recently |
| 1 | At least one character is past the threshold |
| 2 | No jEveAssets profile found |

## Where it looks for data

- **Default:** `%USERPROFILE%\.jeveassets\profiles\`
- Supports both database (`.db`) and XML profile formats.
- Override with the Settings dialog, `--data-dir`, or the `JEVEASSETS_DATA` environment variable.

## Development (run from source)

```
pip install pystray pillow
python companion_app.py            # tray mode
python companion_app.py --check    # CLI mode
```

**Requirements:** Python 3.10+, Windows, `pystray`, `Pillow`

## Project structure

| File | Purpose |
|------|---------|
| `companion_app.py` | Main application -- tray icon, settings, notifications, CLI |
| `profile_checker.py` | Core logic -- reads jEveAssets profiles and returns token ages |
| `build_app.bat` | Build script -- packages everything into a single `.exe` |
| `requirements.txt` | Python dependencies |
