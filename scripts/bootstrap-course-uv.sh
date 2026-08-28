#!/bin/bash

# Install the pinned macOS ARM64 uv release without executing remote shell code.
# Bash 3.2 compatible (the version shipped with macOS).

set -u
set -o pipefail
umask 077

UV_VERSION="0.12.5"
UV_ARCHIVE_NAME="uv-aarch64-apple-darwin.tar.gz"
UV_ARCHIVE_URL="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${UV_ARCHIVE_NAME}"
UV_ARCHIVE_SHA256="5bb0e5fe008a773c3dbcb97ff79cd89e1241464fe9d2f986d52ad8f1b037bd62"

error() {
    printf 'Error: %s\n' "$*" >&2
}

if [ "$#" -ne 1 ]; then
    error "Usage: $0 /absolute/path/to/bin"
    exit 2
fi

DESTINATION=$1
case "$DESTINATION" in
    /*) ;;
    *)
        error "uv destination must be an absolute path."
        exit 2
        ;;
esac

if [ -L "$DESTINATION" ]; then
    error "Refusing to install uv into a symlinked destination."
    exit 1
fi

if [ -x "$DESTINATION/uv" ] && "$DESTINATION/uv" --version >/dev/null 2>&1; then
    exit 0
fi

TESTING=${COURSE_WORKBENCH_BOOTSTRAP_TESTING:-0}
if [ "$TESTING" = "1" ]; then
    UV_ARCHIVE_URL=${COURSE_WORKBENCH_UV_ARCHIVE_URL:-$UV_ARCHIVE_URL}
    UV_ARCHIVE_SHA256=${COURSE_WORKBENCH_UV_SHA256:-$UV_ARCHIVE_SHA256}
else
    system_name=$(uname -s 2>/dev/null || true)
    machine_name=$(uname -m 2>/dev/null || true)
    if [ "$system_name" != "Darwin" ] || [ "$machine_name" != "arm64" ]; then
        error "Automatic uv bootstrap supports macOS ARM64; detected ${system_name:-unknown}/${machine_name:-unknown}."
        exit 1
    fi
fi

for command_name in curl shasum tar mktemp awk mkdir cp chmod mv; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        error "$command_name is required to install uv safely."
        exit 1
    fi
done

TEMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/course-workbench-uv.XXXXXX") || {
    error "Could not create a temporary uv installation directory."
    exit 1
}

cleanup() {
    rm -rf -- "$TEMP_ROOT"
}
trap cleanup EXIT HUP INT TERM

ARCHIVE="$TEMP_ROOT/$UV_ARCHIVE_NAME"
EXTRACTED="$TEMP_ROOT/extracted"
MEMBERS="$TEMP_ROOT/members.txt"
STAGED="$TEMP_ROOT/staged"

if [ "$TESTING" = "1" ]; then
    if ! curl --fail --location --silent --show-error "$UV_ARCHIVE_URL" --output "$ARCHIVE"; then
        error "Could not download the uv test archive."
        exit 1
    fi
else
    if ! curl --fail --location --silent --show-error --proto '=https' --tlsv1.2 \
        "$UV_ARCHIVE_URL" --output "$ARCHIVE"; then
        error "Could not download uv ${UV_VERSION}. Install uv on PATH and retry."
        exit 1
    fi
fi

ACTUAL_SHA256=$(shasum -a 256 "$ARCHIVE" | awk '{print $1}')
if [ "$ACTUAL_SHA256" != "$UV_ARCHIVE_SHA256" ]; then
    error "uv archive SHA256 verification failed."
    exit 1
fi

if ! tar -tzf "$ARCHIVE" > "$MEMBERS"; then
    error "uv archive could not be inspected."
    exit 1
fi

while IFS= read -r member; do
    case "$member" in
        /*|../*|*/../*|*/..)
            error "Unsafe archive path in uv download: $member"
            exit 1
            ;;
    esac
done < "$MEMBERS"

mkdir -m 700 "$EXTRACTED" "$STAGED" || {
    error "Could not stage the uv installation."
    exit 1
}
if ! tar -xzf "$ARCHIVE" -C "$EXTRACTED"; then
    error "uv archive could not be extracted."
    exit 1
fi

EXTRACTED_UV="$EXTRACTED/uv-aarch64-apple-darwin/uv"
EXTRACTED_UVX="$EXTRACTED/uv-aarch64-apple-darwin/uvx"
if [ -L "$EXTRACTED_UV" ] || [ ! -f "$EXTRACTED_UV" ]; then
    error "uv archive does not contain a regular uv executable."
    exit 1
fi
if [ -L "$EXTRACTED_UVX" ] || [ ! -f "$EXTRACTED_UVX" ]; then
    error "uv archive does not contain a regular uvx executable."
    exit 1
fi

cp "$EXTRACTED_UV" "$STAGED/uv" || exit 1
cp "$EXTRACTED_UVX" "$STAGED/uvx" || exit 1
chmod 755 "$STAGED/uv" "$STAGED/uvx" || exit 1
if ! "$STAGED/uv" --version >/dev/null 2>&1; then
    error "Verified uv executable failed its version check."
    exit 1
fi

mkdir -p "$DESTINATION" || {
    error "Could not create the uv destination."
    exit 1
}
if [ -L "$DESTINATION" ]; then
    error "Refusing to install uv into a symlinked destination."
    exit 1
fi
mv "$STAGED/uv" "$DESTINATION/uv" || exit 1
mv "$STAGED/uvx" "$DESTINATION/uvx" || exit 1
chmod 755 "$DESTINATION/uv" "$DESTINATION/uvx" || exit 1

printf 'Installed verified uv %s at %s\n' "$UV_VERSION" "$DESTINATION/uv"
