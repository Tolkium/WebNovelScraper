@echo off
setlocal
cd /d "%~dp0"
if not exist "webnovel_env\Scripts\python.exe" (
    python -m venv webnovel_env
    if errorlevel 1 goto startup_error
)
"webnovel_env\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto startup_error
"webnovel_env\Scripts\python.exe" WebNovelDesktop.py %*
if errorlevel 1 goto startup_error
pause
exit /b 0

:startup_error
echo Web Novel Scraper could not start. See the error above.
pause
exit /b 1
