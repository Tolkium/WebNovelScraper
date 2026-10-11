#!/bin/bash
set -e
cd -- "$(dirname -- "$0")"
echo "Building WebNovelScraper executable..."
if [ ! -x webnovel_env/bin/python ]; then
    python3 -m venv webnovel_env
fi
webnovel_env/bin/python -m pip install --disable-pip-version-check -r requirements.txt
export PYINSTALLER_CONFIG_DIR="$PWD/build/pyinstaller-cache"
webnovel_env/bin/python -m PyInstaller --clean --noconfirm WebNovelScraper.spec
echo "Build complete! The desktop executable is in the dist folder."
