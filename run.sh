#!/bin/bash
set -e
cd -- "$(dirname -- "$0")"

EXPLICIT_UPDATE=0
NEEDS_INSTALL=0
if [ "${1:-}" = "--update" ]; then
    EXPLICIT_UPDATE=1
    NEEDS_INSTALL=1
    shift
fi

if [ ! -x webnovel_env/bin/python ]; then
    python3 -m venv webnovel_env
    NEEDS_INSTALL=1
fi

STAMP="webnovel_env/.requirements.installed"
if [ "$NEEDS_INSTALL" -eq 1 ] || [ ! -f "$STAMP" ] || ! cmp -s requirements.txt "$STAMP"; then
    if [ "$EXPLICIT_UPDATE" -eq 1 ]; then
        webnovel_env/bin/python -m pip install --upgrade --disable-pip-version-check -r requirements.txt
    else
        webnovel_env/bin/python -m pip install --disable-pip-version-check -r requirements.txt
    fi
    cp requirements.txt "$STAMP"
fi

exec webnovel_env/bin/python WebNovelDesktop.py "$@"
