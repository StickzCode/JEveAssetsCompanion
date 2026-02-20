@echo off
REM Build the jEveAssets Companion app into a single .exe
REM Requires: pip install pyinstaller pystray pillow

cd /d "%~dp0"
echo ============================================
echo  Building jEveAssets Companion
echo ============================================
echo.

REM Pick Python command
where py >nul 2>&1
if %errorlevel% equ 0 (
    set PYCMD=py
) else (
    set PYCMD=python
)

REM Ensure dependencies
%PYCMD% -m pip install pyinstaller pystray pillow --quiet

echo Building jEveAssetsCompanion.exe ...
%PYCMD% -m PyInstaller ^
    --onefile ^
    --noconsole ^
    --name "jEveAssetsCompanion" ^
    --hidden-import=profile_checker ^
    --hidden-import=backup_service ^
    --hidden-import=pystray._win32 ^
    companion_app.py

if errorlevel 1 (
    echo.
    echo BUILD FAILED - see error above.
    pause
    exit /b 1
)

echo.
echo ============================================
echo  Build complete!
echo  Output: dist\jEveAssetsCompanion.exe
echo ============================================
echo.
echo Usage:
echo   Double-click the .exe        - starts the system tray monitor
echo   .exe --check                 - one-shot CLI check (prints and exits)
echo   .exe --check --quiet         - silent CLI check (exit code only)
echo   .exe --check-interval 1800   - check every 30 min (tray mode)
echo   .exe --log-file log.txt      - write log in tray mode
echo.
pause
