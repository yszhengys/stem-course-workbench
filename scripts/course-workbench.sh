#!/bin/bash

# One-command, local-first launcher for STEM Course Workbench.
# Bash 3.2 compatible (the version shipped with macOS).

set -u
set -o pipefail

SCRIPT_DIR=$(CDPATH= cd -P "$(dirname "$0")" 2>/dev/null && pwd)
REPO_ROOT=$(CDPATH= cd -P "$SCRIPT_DIR/.." 2>/dev/null && pwd)
RUNTIME_DIR="$REPO_ROOT/.runtime/course-workbench"
LOG_DIR="$RUNTIME_DIR/logs"
LOCK_DIR="$RUNTIME_DIR/launcher.lock"
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
}

acquire_lock() {
    if ! ensure_runtime; then
        return 1
    fi
    if mkdir "$LOCK_DIR" 2>/dev/null; then
        if ! write_atomic "$LOCK_DIR/pid" "$$"; then
            rmdir "$LOCK_DIR" 2>/dev/null || true
            return 1
        fi
        return 0
    fi
    owner=$(read_first_line "$LOCK_DIR/pid")
    owner_command=""
    if [ -n "$owner" ]; then
        owner_command=$(process_command "$owner")
    fi
    case "$owner_command" in
        *"course-workbench.sh"*) ;;
        *)
            # A crashed launcher must not leave the checkout permanently locked.
            # Remove only the lock metadata; never signal the unverified PID.
            rm -f "$LOCK_DIR/pid"
            if rmdir "$LOCK_DIR" 2>/dev/null && mkdir "$LOCK_DIR" 2>/dev/null; then
                if write_atomic "$LOCK_DIR/pid" "$$"; then
                    return 0
                fi
                rmdir "$LOCK_DIR" 2>/dev/null || true
            fi
            ;;
    esac
    if [ -n "$owner" ]; then
        error "Another launcher operation is in progress (PID $owner). Try again when it finishes."
    else
        error "Another launcher operation is in progress. Try again when it finishes."
    fi
    return 1
}

release_lock() {
    if [ -d "$LOCK_DIR" ]; then
        rm -f "$LOCK_DIR/pid"
        rmdir "$LOCK_DIR" 2>/dev/null || true
    fi
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

ensure_env() {
    if [ -f "$ENV_FILE" ]; then
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
    temporary="${ENV_FILE}.tmp.$$"
    if ! umask 077; then
        return 1
    fi
    if ! awk -v replacement="$key" '
        BEGIN { replaced = 0 }
        /^OPEN_NOTEBOOK_ENCRYPTION_KEY=/ {
            print "OPEN_NOTEBOOK_ENCRYPTION_KEY=" replacement
            replaced = 1
            next
        }
        { print }
        END {
            if (!replaced) print "OPEN_NOTEBOOK_ENCRYPTION_KEY=" replacement
        }
    ' "$REPO_ROOT/.env.example" > "$temporary"; then
        rm -f "$temporary"
        error "Could not create .env."
        return 1
    fi
    if ! chmod 600 "$temporary" || ! mv "$temporary" "$ENV_FILE"; then
        rm -f "$temporary"
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
    ps -p "$1" -o command= 2>/dev/null | sed -e 's/^[[:space:]]*//'
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
    stored_marker=$(read_first_line "$(runtime_path "$service" command)")
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
    return 0
}

cleanup_process_metadata() {
    service=$1
    rm -f \
        "$(runtime_path "$service" pid)" \
        "$(runtime_path "$service" pgid)" \
        "$(runtime_path "$service" cwd)" \
        "$(runtime_path "$service" command)"
}

remove_stale_metadata() {
    service=$1
    if [ -f "$(runtime_path "$service" pid)" ] && ! validate_process "$service"; then
        say "Replacing stale $service state: $PROCESS_REASON"
        cleanup_process_metadata "$service"
    fi
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
    container_id=$(docker compose --project-directory "$REPO_ROOT" ps -q surrealdb 2>/dev/null || true)
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
    if ! docker compose --project-directory "$REPO_ROOT" up -d surrealdb; then
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

launch_service() {
    service=$1
    cwd=$2
    marker=$3
    shift 3
    log_file=$(log_path "$service")
    : > "$log_file" || return 1
    API_RELOAD=false "$PYTHON_BIN" -c '
import os
import sys
cwd = sys.argv[1]
command = sys.argv[2:]
os.chdir(cwd)
os.setsid()
os.execvpe(command[0], command, os.environ)
' "$cwd" "$@" >> "$log_file" 2>&1 < /dev/null &
    pid=$!
    if ! write_atomic "$(runtime_path "$service" pid)" "$pid" || \
       ! write_atomic "$(runtime_path "$service" pgid)" "$pid" || \
       ! write_atomic "$(runtime_path "$service" cwd)" "$cwd" || \
       ! write_atomic "$(runtime_path "$service" command)" "$marker"; then
        kill -TERM "-$pid" 2>/dev/null || true
        kill -TERM "$pid" 2>/dev/null || true
        cleanup_process_metadata "$service"
        return 1
    fi
    attempts=0
    while [ "$attempts" -lt 20 ]; do
        if validate_process "$service"; then
            return 0
        fi
        process_alive "$pid" || break
        attempts=$((attempts + 1))
        sleep 0.05
    done
    error "$service did not start as a verifiable process group: $PROCESS_REASON"
    return 1
}

start_api() {
    remove_stale_metadata api
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
    STARTED_API=1
}

start_worker() {
    remove_stale_metadata worker
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
    STARTED_WORKER=1
}

start_frontend() {
    remove_stale_metadata frontend
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
    STARTED_FRONTEND=1
}

surreal_ready() {
    curl -fsS --max-time 2 -o /dev/null http://127.0.0.1:8000/health
}

api_health_ready() {
    curl -fsS --max-time 2 -o /dev/null http://127.0.0.1:5055/health
}

api_database_ready() {
    response=$(curl -fsS --max-time 2 http://127.0.0.1:5055/api/config 2>/dev/null) || return 1
    printf '%s' "$response" | grep -Eq '"dbStatus"[[:space:]]*:[[:space:]]*"online"'
}

course_router_ready() {
    curl -fsS --max-time 2 -o /dev/null http://127.0.0.1:5055/api/courses
}

worker_ready() {
    worker_log=$(log_path worker)
    [ -f "$worker_log" ] || return 1
    grep -Eq 'Successfully imported [1-9][0-9]*/[1-9][0-9]* modules' "$worker_log" && \
        grep -Fq 'Starting LIVE query listener for new commands' "$worker_log"
}

frontend_config_ready() {
    response=$(curl -fsS --max-time 2 http://127.0.0.1:3000/config 2>/dev/null) || return 1
    printf '%s' "$response" | grep -Eq '"apiUrl"[[:space:]]*:[[:space:]]*"http://(127\.0\.0\.1|localhost):5055/?"'
}

course_page_ready() {
    response=$(curl -fsS --max-time 2 "$COURSE_URL" 2>/dev/null) || return 1
    printf '%s' "$response" | grep -Fq 'data-course-workbench-ready="new-course"'
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
        docker compose --project-directory "$REPO_ROOT" stop surrealdb || return 1
    fi
}

rollback_new_services() {
    error "Startup failed; rolling back only services started by this invocation."
    [ "$STARTED_FRONTEND" -eq 1 ] && stop_verified_service frontend >/dev/null 2>&1 || true
    [ "$STARTED_WORKER" -eq 1 ] && stop_verified_service worker >/dev/null 2>&1 || true
    [ "$STARTED_API" -eq 1 ] && stop_verified_service api >/dev/null 2>&1 || true
    if [ "$STARTED_DB" -eq 1 ]; then
        docker compose --project-directory "$REPO_ROOT" stop surrealdb >/dev/null 2>&1 || true
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
    if ! wait_for "new-course page marker data-course-workbench-ready=\"new-course\"" course_page_ready; then
        error "The frontend answered, but the Course UI readiness marker is missing. Finish/rebuild the Course UI and retry."
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
