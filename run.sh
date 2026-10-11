#!/bin/bash
set -e
cd -- "$(dirname -- "$0")"
if [ ! -x webnovel_env/bin/python ]; then
    python3 -m venv webnovel_env
fi
webnovel_env/bin/python -m pip install --disable-pip-version-check -r requirements.txt
exec webnovel_env/bin/python WebNovelDesktop.py "$@"
