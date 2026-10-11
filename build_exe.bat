@echo off
setlocal
cd /d "%~dp0"
echo Building WebNovelScraper executable...
if not exist "webnovel_env\Scripts\python.exe" (
    python -m venv webnovel_env
    if errorlevel 1 goto build_error
)
"webnovel_env\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto build_error
set "PYINSTALLER_CONFIG_DIR=%CD%\build\pyinstaller-cache"
"webnovel_env\Scripts\python.exe" -m PyInstaller --clean --noconfirm WebNovelScraper.spec
if errorlevel 1 goto build_error
echo Build successful! Open dist\WebNovelScraper.exe to start the desktop app.
pause
exit /b 0

:build_error
echo Build failed. See the error above.
pause
exit /b 1
