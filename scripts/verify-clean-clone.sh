#!/bin/bash

# Validate dependency installation from an uncontaminated macOS checkout.
# This deliberately does not start services, read .env, or invoke models.

set -eu
set -o pipefail
umask 077

error() {
    printf 'Error: %s\n' "$*" >&2
}

if [ "$#" -gt 1 ]; then
    error "Usage: $0 [/absolute/path/to/checkout]"
    exit 2
fi

SCRIPT_DIR=$(CDPATH= cd -P "$(dirname "$0")" 2>/dev/null && pwd)
DEFAULT_ROOT=$(CDPATH= cd -P "$SCRIPT_DIR/.." 2>/dev/null && pwd)
REQUESTED_ROOT=${1:-$DEFAULT_ROOT}

if [ ! -d "$REQUESTED_ROOT" ]; then
    error "Checkout directory does not exist: $REQUESTED_ROOT"
    exit 2
fi
CHECKOUT_ROOT=$(CDPATH= cd -P "$REQUESTED_ROOT" 2>/dev/null && pwd)

for contaminant in .tools .venv; do
    if [ -e "$CHECKOUT_ROOT/$contaminant" ] || [ -L "$CHECKOUT_ROOT/$contaminant" ]; then
        error "Clean-clone preflight refuses an existing $contaminant directory: $CHECKOUT_ROOT/$contaminant"
        exit 1
    fi
done

BOOTSTRAP="$CHECKOUT_ROOT/scripts/bootstrap-course-uv.sh"
if [ ! -x "$BOOTSTRAP" ]; then
    error "Missing executable uv bootstrap: $BOOTSTRAP"
    exit 1
fi
if [ ! -f "$CHECKOUT_ROOT/uv.lock" ]; then
    error "Missing Python lockfile: $CHECKOUT_ROOT/uv.lock"
    exit 1
fi
if [ ! -f "$CHECKOUT_ROOT/frontend/package-lock.json" ]; then
    error "Missing frontend lockfile: $CHECKOUT_ROOT/frontend/package-lock.json"
    exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
    error "npm is required for the frontend clean-clone preflight."
    exit 1
fi

"$BOOTSTRAP" "$CHECKOUT_ROOT/.tools/bin"
UV_BIN="$CHECKOUT_ROOT/.tools/bin/uv"
if [ ! -x "$UV_BIN" ]; then
    error "uv bootstrap did not create an executable at $UV_BIN"
    exit 1
fi

(
    cd "$CHECKOUT_ROOT"
    "$UV_BIN" sync --locked --no-dev
)
(
    cd "$CHECKOUT_ROOT/frontend"
    npm ci --ignore-scripts
)

printf 'Clean-clone dependency preflight passed for %s\n' "$CHECKOUT_ROOT"
