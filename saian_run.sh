#!/usr/bin/env bash

# ==============================================================================
# Script: runner_saian.sh
# Description: Unified cross-platform runner for waves2saian.py (macOS & Linux)
# ==============================================================================

# 1. Dynamic Resolution of the PGAI Target Directory
PGAI_DIR="./pgai"
CONFIG_FILE="path_to_git_saian.txt"

if [[ -f "$CONFIG_FILE" ]]; then
    read -r custom_path < "$CONFIG_FILE"
    # Trim whitespaces
    custom_path=$(echo "$custom_path" | xargs)
    # Safely replace tilde with absolute home path
    PGAI_DIR="${custom_path/#\~/$HOME}"
fi

# Define target paths
WAVES_SCRIPT="${PGAI_DIR}/waves2saian.py"
REQ_FILE="${PGAI_DIR}/requirements.py"

# Validate main Python script existence
if [[ ! -f "$WAVES_SCRIPT" ]]; then
    echo -e "\n[ERROR] Python script not found: $WAVES_SCRIPT"
    echo "Verify $CONFIG_FILE or check the installation path."
    exit 1
fi

# 2. Environment Verification via requirements.py
check_python_requirements() {
    if [[ ! -f "$REQ_FILE" ]]; then
        echo -e "\n[ERROR] requirements.py not found in $PGAI_DIR."
        return 1
    fi

    # Attempt to load requirements.py to test dependencies in the current environment
    python3 -c "
import importlib.util
import sys

try:
    spec = importlib.util.spec_from_file_location('req_check', '$REQ_FILE')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
except ImportError as e:
    # Output the missing module to stderr
    sys.stderr.write(str(e))
    sys.exit(1)
" 2>/dev/null
    return $?
}

if ! check_python_requirements; then
    echo -e "\n[ERROR] The current Python environment does not satisfy the required dependencies."
    echo "Please activate a compatible virtual environment (e.g., Conda, venv) and ensure the required libraries are installed."
    echo "Refer to $REQ_FILE for the complete list of dependencies."
    echo ""
    exit 1
fi

# 3. Command Line Arguments Validation
if [[ $# -lt 2 ]]; then
    echo ""
    echo "Usage: $0 [eventid,originid] [mindist,maxdist]"
    echo ""
    exit 1
fi

# Native string parsing using internal field separator (RAM-based, POSIX compliant)
IFS=, read -r eid oid <<< "$1"
IFS=, read -r mindist maxdist <<< "$2"

# Populate optional arguments array
opt_args=()
if [[ -n "$oid" ]]; then
    opt_args+=(--originid "$oid")
fi

# 4. Action Execution Logic
python3 "$WAVES_SCRIPT" \
    --config ./saian_config.json \
    --eventid "$eid" \
    "${opt_args[@]}" \
    --networks IV \
    --channels EH,HH \
    --distances "$mindist,$maxdist" \
    --full
