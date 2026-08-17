#!/bin/bash

# One-command, local-first launcher for STEM Course Workbench.
# Bash 3.2 compatible (the version shipped with macOS).

set -u
set -o pipefail
umask 077

SCRIPT_DIR=$(CDPATH= cd -P "$(dirname "$0")" 2>/dev/null && pwd)
REPO_ROOT=$(CDPATH= cd -P "$SCRIPT_DIR/.." 2>/dev/null && pwd)
RUNTIME_DIR="$REPO_ROOT/.runtime/course-workbench"
LOG_DIR="$RUNTIME_DIR/logs"
LOCK_DIR="$RUNTIME_DIR/launcher.lock"
LOCK_OWNER_FILE="$LOCK_DIR/pid"
ENV_FILE="$REPO_ROOT/.env"
UV_BIN="$REPO_ROOT/.tools/bin/uv"
PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
COURSE_URL="http://127.0.0.1:3000/courses/new"
READY_TIMEOUT=${COURSE_WORKBENCH_READY_TIMEOUT:-180}
POLL_INTERVAL=${COURSE_WORKBENCH_POLL_INTERVAL:-1}

STARTED_DB=0
STARTED_API=0
STARTED_WORKER=0
STARTED_FRONTEND=0
PROCESS_REASON=""
CONTAINER_REASON=""

usage() {
    cat <<'EOF'
Usage:
  ./scripts/course-workbench.sh [start] [--no-open]
  ./scripts/course-workbench.sh status
  ./scripts/course-workbench.sh stop
  ./scripts/course-workbench.sh restart [--no-open]
  ./scripts/course-workbench.sh logs [api|worker|frontend|all]
EOF
}

say() {
    printf '%s\n' "$*"
}

error() {
    printf 'Error: %s\n' "$*" >&2
}

runtime_path() {
    printf '%s/%s.%s' "$RUNTIME_DIR" "$1" "$2"
}

log_path() {
    printf '%s/%s.log' "$LOG_DIR" "$1"
}

read_first_line() {
    if [ -f "$1" ]; then
        IFS= read -r REPLY < "$1" || true
        printf '%s' "${REPLY:-}"
    fi
}

write_atomic() {
    destination=$1
    value=$2
    temporary="${destination}.tmp.$$"
    if ! (umask 077 && printf '%s\n' "$value" > "$temporary"); then
        return 1
    fi
    if ! mv "$temporary" "$destination"; then
        rm -f "$temporary"
        return 1
    fi
}

ensure_runtime() {
    if ! mkdir -p "$LOG_DIR"; then
        error "Cannot create runtime directory: $RUNTIME_DIR"
        return 1
    fi
    chmod 700 "$RUNTIME_DIR" "$LOG_DIR" || return 1
}

acquire_lock() {
    if ! ensure_runtime; then
        return 1
    fi
    if mkdir "$LOCK_DIR" 2>/dev/null; then
        # mkdir is the atomic ownership operation. The owner file is immutable
        # for this lock's lifetime; contenders never remove an initializing lock.
        if ! (set -C; printf '%s\n' "$$" > "$LOCK_OWNER_FILE") 2>/dev/null; then
            error "Acquired the launcher lock but could not record its owner. The lock was left in place for safety."
            return 1
        fi
        return 0
    fi
    attempts=0
    while [ ! -s "$LOCK_OWNER_FILE" ] && [ "$attempts" -lt 20 ]; do
        attempts=$((attempts + 1))
        sleep 0.05
    done
    owner=$(read_first_line "$LOCK_OWNER_FILE")
    case "$owner" in
        ''|*[!0-9]*)
            error "Another launcher operation owns an initializing lock. It was not modified; retry shortly."
            return 1
            ;;
    esac
    if kill -0 "$owner" 2>/dev/null; then
        error "Another launcher operation is in progress (PID $owner). Try again when it finishes."
        return 1
    fi

    # The kernel proves this owner PID is dead. Re-read the immutable owner
    # before removal so a racing contender cannot make us delete a new lock.
    [ "$(read_first_line "$LOCK_OWNER_FILE")" = "$owner" ] || {
        error "Launcher lock ownership changed while checking it; retry."
        return 1
    }
    rm -f "$LOCK_OWNER_FILE"
    if ! rmdir "$LOCK_DIR" 2>/dev/null; then
        error "Could not recover the stale launcher lock safely; retry."
        return 1
    fi
    if ! mkdir "$LOCK_DIR" 2>/dev/null; then
        error "Another launcher operation acquired the lock first; retry."
        return 1
    fi
    if ! (set -C; printf '%s\n' "$$" > "$LOCK_OWNER_FILE") 2>/dev/null; then
        error "Acquired the launcher lock but could not record its owner. The lock was left in place for safety."
        return 1
    fi
    return 0
}

release_lock() {
    [ -d "$LOCK_DIR" ] || return 0
    if [ "$(read_first_line "$LOCK_OWNER_FILE")" != "$$" ]; then
        error "Refusing to release a launcher lock owned by another process."
        return 1
    fi
    rm -f "$LOCK_OWNER_FILE"
    rmdir "$LOCK_DIR" 2>/dev/null || true
}

check_platform() {
    system_name=$(uname -s 2>/dev/null || true)
    machine_name=$(uname -m 2>/dev/null || true)
    if [ "$system_name" != "Darwin" ] || [ "$machine_name" != "arm64" ]; then
        error "This launcher supports macOS ARM64; detected ${system_name:-unknown}/${machine_name:-unknown}."
        return 1
    fi
}

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        error "$2"
        return 1
    fi
}

check_tools() {
    if [ ! -x "$UV_BIN" ]; then
        error "Repository-local uv is missing at $UV_BIN. Install uv there before starting."
        return 1
    fi
    require_command docker "Docker Desktop is required. Install and start Docker Desktop." || return 1
    if ! docker info >/dev/null 2>&1; then
        error "Docker Desktop is installed but is not running. Start it and retry."
        return 1
    fi
    require_command node "Node.js is required (Node 20 or newer recommended)." || return 1
    require_command npm "npm is required." || return 1
    require_command curl "curl is required." || return 1
    require_command lsof "lsof is required for safe process ownership checks." || return 1
    require_command shasum "shasum is required for lockfile checks." || return 1
    require_command ps "ps is required for safe process ownership checks." || return 1
}

random_key() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 32 2>/dev/null && return 0
    fi
    LC_ALL=C od -An -N32 -tx1 /dev/urandom 2>/dev/null | tr -d ' \n'
}

rewrite_env_key() {
    source_file=$1
    destination=$2
    key=$3
    temporary="${destination}.tmp.$$"
    # The key travels through stdin, never argv. Global umask keeps the temp
    # file private from its first byte; mv atomically replaces the destination.
    if ! {
        printf '%s\n' "$key"
        cat "$source_file"
    } | awk '
        NR == 1 { replacement = $0; next }
        /^OPEN_NOTEBOOK_ENCRYPTION_KEY=/ {
            if (!replaced) print "OPEN_NOTEBOOK_ENCRYPTION_KEY=" replacement
            replaced = 1
            next
        }
        { print }
        END {
            if (!replaced) print "OPEN_NOTEBOOK_ENCRYPTION_KEY=" replacement
        }
    ' > "$temporary"; then
        rm -f "$temporary"
        return 1
    fi
    if ! chmod 600 "$temporary" || ! mv "$temporary" "$destination"; then
        rm -f "$temporary"
        return 1
    fi
}

ensure_env() {
    if [ -L "$ENV_FILE" ]; then
        error "Refusing to use symlinked .env: $ENV_FILE"
        return 1
    fi
    if [ -f "$ENV_FILE" ]; then
        chmod 600 "$ENV_FILE" || {
            error "Could not set .env permissions to 600."
            return 1
        }
        existing_key=$(awk '
            /^OPEN_NOTEBOOK_ENCRYPTION_KEY=/ {
                sub(/^OPEN_NOTEBOOK_ENCRYPTION_KEY=/, "")
                value = $0
            }
            END { print value }
        ' "$ENV_FILE")
        case "$existing_key" in
            ''|*change-me-to-a-secret-string*|*replace-me*|*your-secret*|*CHANGE_ME*)
                key=$(random_key)
                if [ "${#key}" -lt 32 ] || ! rewrite_env_key "$ENV_FILE" "$ENV_FILE" "$key"; then
                    error "Could not replace the placeholder encryption key in .env securely."
                    return 1
                fi
                say "Secured the local .env encryption key and permissions."
                ;;
        esac
        return 0
    fi
    if [ ! -f "$REPO_ROOT/.env.example" ]; then
        error "Missing .env.example; cannot create local configuration."
        return 1
    fi
    key=$(random_key)
    if [ "${#key}" -lt 32 ]; then
        error "Could not generate OPEN_NOTEBOOK_ENCRYPTION_KEY securely."
        return 1
    fi
    if ! rewrite_env_key "$REPO_ROOT/.env.example" "$ENV_FILE" "$key"; then
        error "Could not secure .env."
        return 1
    fi
    say "Created private local configuration at $ENV_FILE (mode 600)."
}

file_hash() {
    shasum -a 256 "$1" | awk '{print $1}'
}

ensure_dependencies() {
    if [ ! -f "$REPO_ROOT/uv.lock" ]; then
        error "Missing uv.lock."
        return 1
    fi
    uv_hash=$(file_hash "$REPO_ROOT/uv.lock") || return 1
    uv_stamp="$RUNTIME_DIR/uv-lock.sha256"
    saved_uv_hash=$(read_first_line "$uv_stamp")
    if [ ! -x "$PYTHON_BIN" ] || [ "$uv_hash" != "$saved_uv_hash" ]; then
        say "Preparing locked Python dependencies..."
        if ! (cd "$REPO_ROOT" && "$UV_BIN" sync --locked); then
            error "Python dependency installation failed."
            return 1
        fi
        if [ ! -x "$PYTHON_BIN" ]; then
            error "Locked Python install completed without $PYTHON_BIN."
            return 1
        fi
        write_atomic "$uv_stamp" "$uv_hash" || return 1
    fi

    npm_lock="$REPO_ROOT/frontend/package-lock.json"
    if [ ! -f "$npm_lock" ]; then
        error "Missing frontend/package-lock.json."
        return 1
    fi
    npm_hash=$(file_hash "$npm_lock") || return 1
    npm_stamp="$RUNTIME_DIR/npm-lock.sha256"
    saved_npm_hash=$(read_first_line "$npm_stamp")
    if [ ! -d "$REPO_ROOT/frontend/node_modules" ] || [ "$npm_hash" != "$saved_npm_hash" ]; then
        say "Preparing locked frontend dependencies..."
        if ! (cd "$REPO_ROOT/frontend" && npm ci); then
            error "Frontend dependency installation failed."
            return 1
        fi
        write_atomic "$npm_stamp" "$npm_hash" || return 1
    fi
}

process_alive() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
    esac
    kill -0 "$1" 2>/dev/null || return 1
    process_state=$(ps -p "$1" -o stat= 2>/dev/null | tr -d '[:space:]')
    case "$process_state" in
        ''|*Z*) return 1 ;;
        *) return 0 ;;
    esac
}

process_command() {
    ps -ww -p "$1" -o command= 2>/dev/null | \
        sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
}

process_executable() {
    ps -ww -p "$1" -o comm= 2>/dev/null | \
        sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
}

process_started() {
    LC_ALL=C ps -ww -p "$1" -o lstart= 2>/dev/null | \
        sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
}

process_group() {
    ps -p "$1" -o pgid= 2>/dev/null | tr -d '[:space:]'
}

process_cwd() {
    lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1
}

expected_cwd() {
    case "$1" in
        frontend) printf '%s/frontend' "$REPO_ROOT" ;;
        *) printf '%s' "$REPO_ROOT" ;;
    esac
}

expected_marker() {
    case "$1" in
        api) printf '%s' 'run_api.py' ;;
        worker) printf '%s' 'surreal-commands-worker' ;;
        frontend) printf '%s' 'npm run dev' ;;
        *) return 1 ;;
    esac
}

validate_process() {
    service=$1
    pid=$(read_first_line "$(runtime_path "$service" pid)")
    stored_pgid=$(read_first_line "$(runtime_path "$service" pgid)")
    stored_cwd=$(read_first_line "$(runtime_path "$service" cwd)")
    stored_marker=$(read_first_line "$(runtime_path "$service" signature)")
    stored_argv=$(read_first_line "$(runtime_path "$service" argv)")
    stored_executable=$(read_first_line "$(runtime_path "$service" executable)")
    stored_started=$(read_first_line "$(runtime_path "$service" started)")
    wanted_cwd=$(expected_cwd "$service")
    wanted_marker=$(expected_marker "$service")
    PROCESS_REASON=""

    if [ -z "$pid" ]; then
        PROCESS_REASON="no PID file"
        return 1
    fi
    if ! process_alive "$pid"; then
        PROCESS_REASON="PID $pid is not alive"
        return 1
    fi
    actual_pgid=$(process_group "$pid")
    actual_cwd=$(process_cwd "$pid")
    actual_command=$(process_command "$pid")
    actual_executable=$(process_executable "$pid")
    actual_started=$(process_started "$pid")
    if [ -z "$actual_pgid" ] || [ "$actual_pgid" != "$pid" ] || [ "$stored_pgid" != "$actual_pgid" ]; then
        PROCESS_REASON="PID $pid is not the verified process-group leader"
        return 1
    fi
    if [ "$stored_cwd" != "$wanted_cwd" ] || [ "$actual_cwd" != "$wanted_cwd" ]; then
        PROCESS_REASON="PID $pid belongs to cwd ${actual_cwd:-unknown}, expected $wanted_cwd"
        return 1
    fi
    if [ "$stored_marker" != "$wanted_marker" ]; then
        PROCESS_REASON="stored command signature is invalid"
        return 1
    fi
    case "$actual_command" in
        *"$wanted_marker"*) ;;
        *)
            PROCESS_REASON="PID $pid command does not match $wanted_marker"
            return 1
            ;;
    esac
    if [ -z "$stored_argv" ] || [ "$actual_command" != "$stored_argv" ]; then
        PROCESS_REASON="PID $pid exact argv changed since launch"
        return 1
    fi
    if [ -z "$stored_executable" ] || [ "$actual_executable" != "$stored_executable" ]; then
        PROCESS_REASON="PID $pid executable changed since launch"
        return 1
    fi
    if [ -z "$stored_started" ] || [ "$actual_started" != "$stored_started" ]; then
        PROCESS_REASON="PID $pid start fingerprint changed (possible PID reuse)"
        return 1
    fi
    return 0
}

cleanup_process_metadata() {
    service=$1
    rm -f \
        "$(runtime_path "$service" pid)" \
        "$(runtime_path "$service" pgid)" \
        "$(runtime_path "$service" cwd)" \
        "$(runtime_path "$service" signature)" \
        "$(runtime_path "$service" argv)" \
        "$(runtime_path "$service" executable)" \
        "$(runtime_path "$service" started)" \
        "$(runtime_path "$service" command)"
}

remove_stale_metadata() {
    service=$1
    if [ -f "$(runtime_path "$service" pid)" ] && ! validate_process "$service"; then
        stale_pid=$(read_first_line "$(runtime_path "$service" pid)")
        if process_alive "$stale_pid"; then
            error "Refusing to replace $service metadata: PID $stale_pid is live but unverified ($PROCESS_REASON)."
            return 1
        fi
        say "Replacing stale $service state: $PROCESS_REASON"
        cleanup_process_metadata "$service"
    fi
    return 0
}

owner_diagnostic() {
    pid=$1
    owner_command=$(process_command "$pid")
    owner_cwd=$(process_cwd "$pid")
    printf 'PID %s, cwd %s, command %s' "$pid" "${owner_cwd:-unknown}" "${owner_command:-unknown}"
}

port_pids() {
    lsof -t -nP -iTCP:"$1" -sTCP:LISTEN 2>/dev/null || true
}

pid_belongs_to_service() {
    owner_pid=$1
    service=$2
    validate_process "$service" || return 1
    service_pid=$(read_first_line "$(runtime_path "$service" pid)")
    service_pgid=$(read_first_line "$(runtime_path "$service" pgid)")
    owner_pgid=$(process_group "$owner_pid")
    owner_cwd=$(process_cwd "$owner_pid")
    wanted_cwd=$(expected_cwd "$service")
    [ "$owner_pgid" = "$service_pgid" ] && [ "$owner_cwd" = "$wanted_cwd" ] && process_alive "$service_pid"
}

assert_host_port_available() {
    service=$1
    port=$2
    owners=$(port_pids "$port")
    [ -z "$owners" ] && return 0
    for owner in $owners; do
        if ! pid_belongs_to_service "$owner" "$service"; then
            error "Port $port is held by another process or checkout: $(owner_diagnostic "$owner")."
            error "Stop that owner, or use its checkout's launcher, then retry."
            return 1
        fi
    done
}

compose_container_state() {
    CONTAINER_REASON=""
    container_id=$(docker compose -f "$REPO_ROOT/docker-compose.yml" \
        --project-directory "$REPO_ROOT" ps --all -q surrealdb 2>/dev/null || true)
    [ -z "$container_id" ] && return 1
    container_root=$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' "$container_id" 2>/dev/null || true)
    container_service=$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.service" }}' "$container_id" 2>/dev/null || true)
    container_running=$(docker inspect -f '{{.State.Running}}' "$container_id" 2>/dev/null || true)
    if [ "$container_root" != "$REPO_ROOT" ] || [ "$container_service" != "surrealdb" ]; then
        CONTAINER_REASON="SurrealDB container belongs to another checkout: ${container_root:-unknown}"
        return 2
    fi
    [ "$container_running" = "true" ] || return 1
    return 0
}

start_database() {
    compose_container_state
    container_state=$?
    if [ "$container_state" -eq 0 ]; then
        say "Reusing this checkout's SurrealDB container."
        return 0
    fi
    if [ "$container_state" -eq 2 ]; then
        error "$CONTAINER_REASON"
        return 1
    fi
    db_owners=$(port_pids 8000)
    if [ -n "$db_owners" ]; then
        first_owner=$(printf '%s\n' "$db_owners" | head -n 1)
        error "Port 8000 is held by another process or checkout: $(owner_diagnostic "$first_owner")."
        error "Stop that SurrealDB instance with its own checkout before retrying."
        return 1
    fi
    say "Starting SurrealDB (Docker only)..."
    if ! docker compose -f "$REPO_ROOT/docker-compose.yml" \
        --project-directory "$REPO_ROOT" up -d surrealdb; then
        error "Could not start SurrealDB."
        return 1
    fi
    STARTED_DB=1
    compose_container_state
    container_state=$?
    if [ "$container_state" -ne 0 ]; then
        error "Started SurrealDB but its Compose ownership could not be verified: $CONTAINER_REASON"
        return 1
    fi
}

set_started_flag() {
    service=$1
    value=$2
    case "$service" in
        api) STARTED_API=$value ;;
        worker) STARTED_WORKER=$value ;;
        frontend) STARTED_FRONTEND=$value ;;
    esac
}

terminate_new_launch() {
    service=$1
    pid=$2
    handshake=$3
    handshake_pid=""
    handshake_pgid=""
    if [ -f "$handshake" ]; then
        handshake_pid=$(sed -n '1p' "$handshake")
        handshake_pgid=$(sed -n '2p' "$handshake")
    fi
    actual_pgid=$(process_group "$pid")
    if [ "$handshake_pid" = "$pid" ] && [ "$handshake_pgid" = "$pid" ] && \
       [ "$actual_pgid" = "$pid" ]; then
        kill -TERM "-$pid" 2>/dev/null || true
        attempts=0
        while process_alive "$pid" && [ "$attempts" -lt 20 ]; do
            attempts=$((attempts + 1))
            sleep 0.05
        done
        process_alive "$pid" && kill -KILL "-$pid" 2>/dev/null || true
    else
        # `$pid` is the direct child created by this invocation. Without a
        # verified session handshake we may terminate only that PID, not a PGID.
        kill -TERM "$pid" 2>/dev/null || true
    fi
    rm -f "$handshake"
    cleanup_process_metadata "$service"
    set_started_flag "$service" 0
}

write_service_metadata() {
    service=$1
    pid=$2
    pgid=$3
    cwd=$4
    signature=$5
    argv=$6
    executable=$7
    started=$8
    # PID is the commit marker and is written last; status cannot mistake a
    # partially written metadata set for a reusable process.
    write_atomic "$(runtime_path "$service" pgid)" "$pgid" && \
        write_atomic "$(runtime_path "$service" cwd)" "$cwd" && \
        write_atomic "$(runtime_path "$service" signature)" "$signature" && \
        write_atomic "$(runtime_path "$service" argv)" "$argv" && \
        write_atomic "$(runtime_path "$service" executable)" "$executable" && \
        write_atomic "$(runtime_path "$service" started)" "$started" && \
        write_atomic "$(runtime_path "$service" pid)" "$pid"
}

launch_service() {
    service=$1
    cwd=$2
    marker=$3
    shift 3
    log_file=$(log_path "$service")
    handshake_prefix="$RUNTIME_DIR/${service}.session"
    : > "$log_file" || return 1
    API_RELOAD=false "$PYTHON_BIN" -c '
import os
import sys
cwd = sys.argv[1]
handshake_prefix = sys.argv[2]
command = sys.argv[3:]
os.chdir(cwd)
os.setsid()
pid = os.getpid()
handshake = f"{handshake_prefix}.{pid}"
fd = os.open(handshake, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    handle.write(f"{pid}\n{os.getpgrp()}\n")
os.execvpe(command[0], command, os.environ)
' "$cwd" "$handshake_prefix" "$@" >> "$log_file" 2>&1 < /dev/null &
    pid=$!
    handshake="${handshake_prefix}.${pid}"
    set_started_flag "$service" 1

    attempts=0
    while [ "$attempts" -lt 40 ]; do
        if [ -f "$handshake" ]; then
            handshake_pid=$(sed -n '1p' "$handshake")
            handshake_pgid=$(sed -n '2p' "$handshake")
            actual_pgid=$(process_group "$pid")
            actual_cwd=$(process_cwd "$pid")
            actual_argv=$(process_command "$pid")
            actual_executable=$(process_executable "$pid")
            actual_started=$(process_started "$pid")
            case "$actual_argv" in
                *"$marker"*) marker_matches=1 ;;
                *) marker_matches=0 ;;
            esac
            if [ "$handshake_pid" = "$pid" ] && [ "$handshake_pgid" = "$pid" ] && \
               [ -n "$actual_pgid" ] && [ -n "$actual_cwd" ] && \
               [ -n "$actual_argv" ] && [ -n "$actual_executable" ] && \
               [ -n "$actual_started" ]; then
                if [ "$actual_pgid" = "$pid" ] && [ "$actual_cwd" = "$cwd" ] && \
                   [ "$marker_matches" -eq 1 ]; then
                    if write_service_metadata "$service" "$pid" "$pid" "$cwd" \
                        "$marker" "$actual_argv" "$actual_executable" "$actual_started"; then
                        if validate_process "$service"; then
                            rm -f "$handshake"
                            return 0
                        fi
                    fi
                fi
                break
            fi
        fi
        process_alive "$pid" || break
        attempts=$((attempts + 1))
        sleep 0.05
    done
    PROCESS_REASON="session/argv/executable/cwd/start fingerprint validation failed"
    error "$service did not start as an owned process group: $PROCESS_REASON"
    terminate_new_launch "$service" "$pid" "$handshake"
    return 1
}

start_api() {
    remove_stale_metadata api || return 1
    if validate_process api; then
        assert_host_port_available api 5055 || return 1
        say "Reusing API process $(read_first_line "$(runtime_path api pid)")."
        return 0
    fi
    assert_host_port_available api 5055 || return 1
    say "Starting API on 127.0.0.1:5055..."
    if ! launch_service api "$REPO_ROOT" "run_api.py" \
        "$UV_BIN" run --env-file "$ENV_FILE" python run_api.py; then
        return 1
    fi
}

start_worker() {
    remove_stale_metadata worker || return 1
    if validate_process worker; then
        say "Reusing worker process $(read_first_line "$(runtime_path worker pid)")."
        return 0
    fi
    max_tasks=${OPEN_NOTEBOOK_WORKER_MAX_TASKS:-5}
    say "Starting background worker (upstream concurrency $max_tasks)..."
    if ! launch_service worker "$REPO_ROOT" "surreal-commands-worker" \
        "$UV_BIN" run --env-file "$ENV_FILE" surreal-commands-worker \
        --import-modules commands --max-tasks "$max_tasks"; then
        return 1
    fi
}

start_frontend() {
    remove_stale_metadata frontend || return 1
    if validate_process frontend; then
        assert_host_port_available frontend 3000 || return 1
        say "Reusing frontend process $(read_first_line "$(runtime_path frontend pid)")."
        return 0
    fi
    assert_host_port_available frontend 3000 || return 1
    say "Starting frontend on 127.0.0.1:3000..."
    if ! launch_service frontend "$REPO_ROOT/frontend" "npm run dev" npm run dev; then
        return 1
    fi
}

http_200_to_file() {
    url=$1
    output_file=$2
    status=$(curl -sS --max-time 2 -o "$output_file" -w '%{http_code}' "$url" 2>/dev/null) || return 1
    [ "$status" = "200" ]
}

surreal_ready() {
    http_200_to_file http://127.0.0.1:8000/health /dev/null
}

api_health_ready() {
    http_200_to_file http://127.0.0.1:5055/health /dev/null
}

api_database_ready() {
    response_file="$RUNTIME_DIR/api-config-response.$$"
    if ! http_200_to_file http://127.0.0.1:5055/api/config "$response_file"; then
        rm -f "$response_file"
        return 1
    fi
    grep -Eq '"dbStatus"[[:space:]]*:[[:space:]]*"online"' "$response_file"
    result=$?
    rm -f "$response_file"
    return "$result"
}

course_router_ready() {
    http_200_to_file http://127.0.0.1:5055/api/courses /dev/null
}

worker_ready() {
    worker_log=$(log_path worker)
    [ -f "$worker_log" ] || return 1
    grep -Eq 'Successfully imported [1-9][0-9]*/[1-9][0-9]* modules' "$worker_log" && \
        grep -Fq 'Starting LIVE query listener for new commands' "$worker_log"
}

frontend_config_ready() {
    response_file="$RUNTIME_DIR/frontend-config-response.$$"
    if ! http_200_to_file http://127.0.0.1:3000/config "$response_file"; then
        rm -f "$response_file"
        return 1
    fi
    grep -Eq '"apiUrl"[[:space:]]*:[[:space:]]*"http://(127\.0\.0\.1|localhost):5055/?"' "$response_file"
    result=$?
    rm -f "$response_file"
    return "$result"
}

course_page_ready() {
    response_file="$RUNTIME_DIR/course-page-response.$$"
    if ! http_200_to_file "$COURSE_URL" "$response_file"; then
        rm -f "$response_file"
        return 1
    fi
    grep -Eq 'data-course-workbench-ready="(new-course|connection-checking)"' "$response_file"
    result=$?
    rm -f "$response_file"
    return "$result"
}

wait_for() {
    label=$1
    readiness_function=$2
    deadline=$((SECONDS + READY_TIMEOUT))
    while [ "$SECONDS" -le "$deadline" ]; do
        if "$readiness_function"; then
            say "Ready: $label"
            return 0
        fi
        sleep "$POLL_INTERVAL"
    done
    error "Timed out waiting for $label."
    return 1
}

stop_verified_service() {
    service=$1
    if [ ! -f "$(runtime_path "$service" pid)" ]; then
        return 0
    fi
    if ! validate_process "$service"; then
        error "Refusing to stop unverified $service process: $PROCESS_REASON"
        return 1
    fi
    pid=$(read_first_line "$(runtime_path "$service" pid)")
    pgid=$(read_first_line "$(runtime_path "$service" pgid)")
    say "Stopping $service process group $pgid..."
    kill -TERM "-$pgid" 2>/dev/null || true
    attempts=0
    while process_alive "$pid" && [ "$attempts" -lt 50 ]; do
        attempts=$((attempts + 1))
        sleep 0.1
    done
    if process_alive "$pid"; then
        kill -KILL "-$pgid" 2>/dev/null || true
        sleep 0.1
    fi
    if process_alive "$pid"; then
        error "$service process group $pgid did not stop."
        return 1
    fi
    cleanup_process_metadata "$service"
}

stop_owned_database() {
    compose_container_state
    container_state=$?
    if [ "$container_state" -eq 2 ]; then
        error "Refusing to stop SurrealDB: $CONTAINER_REASON"
        return 1
    fi
    if [ "$container_state" -eq 0 ]; then
        say "Stopping this checkout's SurrealDB container (data is preserved)..."
        docker compose -f "$REPO_ROOT/docker-compose.yml" \
            --project-directory "$REPO_ROOT" stop surrealdb || return 1
    fi
}

rollback_new_services() {
    error "Startup failed; rolling back only services started by this invocation."
    [ "$STARTED_FRONTEND" -eq 1 ] && stop_verified_service frontend >/dev/null 2>&1 || true
    [ "$STARTED_WORKER" -eq 1 ] && stop_verified_service worker >/dev/null 2>&1 || true
    [ "$STARTED_API" -eq 1 ] && stop_verified_service api >/dev/null 2>&1 || true
    if [ "$STARTED_DB" -eq 1 ]; then
        docker compose -f "$REPO_ROOT/docker-compose.yml" \
            --project-directory "$REPO_ROOT" stop surrealdb >/dev/null 2>&1 || true
    fi
    error "Logs: $(log_path api), $(log_path worker), $(log_path frontend)"
    for service in api worker frontend; do
        file=$(log_path "$service")
        if [ -s "$file" ]; then
            error "--- $service log (last 20 lines) ---"
            tail -n 20 "$file" >&2 || true
        fi
    done
}

start_locked() {
    open_browser=$1
    STARTED_DB=0
    STARTED_API=0
    STARTED_WORKER=0
    STARTED_FRONTEND=0

    check_platform || return 1
    check_tools || return 1
    ensure_env || return 1
    ensure_dependencies || return 1
    start_database || return 1
    wait_for "SurrealDB health" surreal_ready || return 1
    start_api || return 1
    wait_for "API health" api_health_ready || return 1
    wait_for "API database connection" api_database_ready || return 1
    wait_for "Course router and migrations" course_router_ready || return 1
    start_worker || return 1
    wait_for "worker command imports and LIVE listener" worker_ready || return 1
    start_frontend || return 1
    wait_for "frontend runtime API configuration" frontend_config_ready || return 1
    if ! wait_for "new-course route and SSR readiness marker" course_page_ready; then
        error "The frontend answered, but /courses/new did not expose a Course or ConnectionGuard readiness marker. Finish/rebuild the Course UI and retry."
        return 1
    fi

    say "STEM Course Workbench is ready: $COURSE_URL"
    if [ "$open_browser" -eq 1 ]; then
        if ! command -v open >/dev/null 2>&1 || ! open "$COURSE_URL"; then
            say "Could not open the browser automatically. Open this URL: $COURSE_URL"
        fi
    else
        say "Open this URL: $COURSE_URL"
    fi
}

run_start() {
    open_browser=$1
    acquire_lock || return 1
    if start_locked "$open_browser"; then
        release_lock
        return 0
    fi
    rollback_new_services
    release_lock
    return 1
}

stop_locked() {
    result=0
    stop_verified_service frontend || result=1
    stop_verified_service worker || result=1
    stop_verified_service api || result=1
    stop_owned_database || result=1
    if [ "$result" -eq 0 ]; then
        say "Stopped Course Workbench services. Course data was preserved."
    fi
    return "$result"
}

run_stop() {
    acquire_lock || return 1
    stop_locked
    result=$?
    release_lock
    return "$result"
}

run_restart() {
    open_browser=$1
    acquire_lock || return 1
    if ! stop_locked; then
        release_lock
        return 1
    fi
    if start_locked "$open_browser"; then
        release_lock
        return 0
    fi
    rollback_new_services
    release_lock
    return 1
}

database_status() {
    compose_container_state
    state=$?
    if [ "$state" -eq 0 ]; then
        say "SurrealDB: running (verified Compose checkout: $REPO_ROOT)"
        return 0
    fi
    if [ "$state" -eq 2 ]; then
        say "SurrealDB: foreign/stale ($CONTAINER_REASON)"
        return 1
    fi
    say "SurrealDB: stopped"
    return 1
}

service_status() {
    service=$1
    if validate_process "$service"; then
        say "$service: running (PID $(read_first_line "$(runtime_path "$service" pid)"), log $(log_path "$service"))"
        return 0
    fi
    if [ -f "$(runtime_path "$service" pid)" ]; then
        say "$service: stale/unverified ($PROCESS_REASON)"
    else
        say "$service: stopped"
    fi
    return 1
}

run_status() {
    say "Course Workbench status for $REPO_ROOT"
    say "Runtime files: $RUNTIME_DIR"
    result=0
    database_status || result=1
    service_status api || result=1
    service_status worker || result=1
    service_status frontend || result=1
    return "$result"
}

run_logs() {
    target=$1
    case "$target" in
        api|worker|frontend)
            file=$(log_path "$target")
            if [ ! -f "$file" ]; then
                error "No $target log yet: $file"
                return 1
            fi
            tail -n 100 -f "$file"
            ;;
        all)
            set --
            for service in api worker frontend; do
                file=$(log_path "$service")
                [ -f "$file" ] && set -- "$@" "$file"
            done
            if [ "$#" -eq 0 ]; then
                error "No Course Workbench logs exist under $LOG_DIR."
                return 1
            fi
            tail -n 100 -f "$@"
            ;;
        *)
            error "Unknown log target: $target"
            usage
            return 2
            ;;
    esac
}

command_name=${1:-start}
if [ "$#" -gt 0 ]; then
    shift
fi

case "$command_name" in
    start)
        open_browser=1
        if [ "$#" -eq 1 ] && [ "$1" = "--no-open" ]; then
            open_browser=0
        elif [ "$#" -ne 0 ]; then
            usage
            exit 2
        fi
        run_start "$open_browser"
        exit $?
        ;;
    status)
        [ "$#" -eq 0 ] || { usage; exit 2; }
        run_status
        exit $?
        ;;
    stop)
        [ "$#" -eq 0 ] || { usage; exit 2; }
        run_stop
        exit $?
        ;;
    restart)
        open_browser=1
        if [ "$#" -eq 1 ] && [ "$1" = "--no-open" ]; then
            open_browser=0
        elif [ "$#" -ne 0 ]; then
            usage
            exit 2
        fi
        run_restart "$open_browser"
        exit $?
        ;;
    logs)
        target=${1:-all}
        [ "$#" -le 1 ] || { usage; exit 2; }
        run_logs "$target"
        exit $?
        ;;
    -h|--help|help)
        usage
        exit 0
        ;;
    *)
        error "Unknown command: $command_name"
        usage
        exit 2
        ;;
esac
