@echo off
setlocal
cd /d "%~dp0"

set "EXPLICIT_UPDATE=0"
set "NEEDS_INSTALL=0"
if /i "%~1"=="--update" (
    set "EXPLICIT_UPDATE=1"
    set "NEEDS_INSTALL=1"
)

if not exist "webnovel_env\Scripts\python.exe" (
    python -m venv webnovel_env
    if errorlevel 1 goto startup_error
    set "NEEDS_INSTALL=1"
)

set "STAMP=webnovel_env\.requirements.installed"
if not exist "%STAMP%" set "NEEDS_INSTALL=1"
if "%NEEDS_INSTALL%"=="0" (
    fc /b requirements.txt "%STAMP%" >nul 2>&1
    if errorlevel 1 set "NEEDS_INSTALL=1"
)

if "%NEEDS_INSTALL%"=="1" (
    if "%EXPLICIT_UPDATE%"=="1" (
        "webnovel_env\Scripts\python.exe" -m pip install --upgrade --disable-pip-version-check -r requirements.txt
    ) else (
        "webnovel_env\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
    )
    if errorlevel 1 goto startup_error
    copy /y requirements.txt "%STAMP%" >nul
)

if "%EXPLICIT_UPDATE%"=="1" goto launch_after_update
"webnovel_env\Scripts\python.exe" WebNovelDesktop.py %*
goto launched

:launch_after_update
"webnovel_env\Scripts\python.exe" -c "import runpy, sys; sys.argv = ['WebNovelDesktop.py'] + sys.argv[2:]; runpy.run_path('WebNovelDesktop.py', run_name='__main__')" %*

:launched
if errorlevel 1 goto startup_error
pause
exit /b 0

:startup_error
echo Web Novel Scraper could not start. See the error above.
pause
exit /b 1
