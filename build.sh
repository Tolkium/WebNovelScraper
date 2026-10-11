#!/bin/bash
set -e
exec bash "$(dirname -- "$0")/build_exe.sh" "$@"
