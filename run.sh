#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=${0:A:h}
exec conda run --no-capture-output -n mobileposer python "$SCRIPT_DIR/receiver_visualizer.py" "$@"
