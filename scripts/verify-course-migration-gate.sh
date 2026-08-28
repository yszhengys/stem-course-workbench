#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPOSITORY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SURREAL_IMAGE="${SURREAL_IMAGE:-surrealdb/surrealdb:v2.6.5}"
CONTAINER_NAME="stem-course-migration-gate.$$.$RANDOM"
TEMP_PARENT="${TMPDIR:-/tmp}"
DATA_ROOT="$(mktemp -d "$TEMP_PARENT/stem-course-migration-gate-data.XXXXXX")"
CONTAINER_EXISTS=0
MAPPED_PORT=""
UV_BIN=""

resolve_uv() {
  repository_uv="$REPOSITORY_ROOT/.tools/bin/uv"
  if [ -x "$repository_uv" ]; then
    UV_BIN="$repository_uv"
  elif command -v uv >/dev/null 2>&1; then
    UV_BIN="$(command -v uv)"
  else
    echo "uv is required for the Course migration gate." >&2
    return 1
  fi
}

validate_data_root() {
  candidate="$1"
  case "$candidate" in
    ""|"/"|"$HOME"|*surreal_data*|*notebook_data*)
      echo "Refusing unsafe migration-gate data root: $candidate" >&2
      return 1
      ;;
  esac
  case "$candidate" in
    "$TEMP_PARENT"/stem-course-migration-gate-data.*) return 0 ;;
    *)
      echo "Refusing non-temporary migration-gate data root: $candidate" >&2
      return 1
      ;;
  esac
}

cleanup() {
  cleanup_status=$?
  set +e
  if [ "$CONTAINER_EXISTS" -eq 1 ]; then
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1
  fi
  if validate_data_root "$DATA_ROOT" >/dev/null 2>&1; then
    rm -rf -- "$DATA_ROOT"
  fi
  exit "$cleanup_status"
}
trap cleanup EXIT INT TERM

validate_data_root "$DATA_ROOT"

start_container() {
  docker run --detach \
    --name "$CONTAINER_NAME" \
    --publish 127.0.0.1::8000 \
    --user "$(id -u):$(id -g)" \
    --volume "$DATA_ROOT:/gate-data" \
    "$SURREAL_IMAGE" \
    start --log warn --user root --pass root \
    rocksdb:/gate-data/migration-gate.db >/dev/null
  CONTAINER_EXISTS=1
  MAPPED_PORT="$(
    docker port "$CONTAINER_NAME" 8000/tcp \
      | sed -n 's/.*://p' \
      | tail -n 1
  )"
  if [ -z "$MAPPED_PORT" ]; then
    echo "Docker did not publish the temporary SurrealDB port." >&2
    return 1
  fi
}

wait_for_health() {
  attempt=0
  while [ "$attempt" -lt 90 ]; do
    if curl -fsS "http://127.0.0.1:$MAPPED_PORT/health" >/dev/null 2>&1; then
      return 0
    fi
    attempt=$((attempt + 1))
    sleep 1
  done
  echo "Temporary SurrealDB did not become healthy." >&2
  docker logs "$CONTAINER_NAME" >&2 || true
  return 1
}

run_verifier() {
  phase="$1"
  export SURREAL_URL="ws://127.0.0.1:$MAPPED_PORT/rpc"
  export SURREAL_USER="root"
  export SURREAL_PASSWORD="root"
  export SURREAL_NAMESPACE="course_migration_gate"
  export SURREAL_DATABASE="course_migration_gate"
  if [ -n "${COURSE_MIGRATION_GATE_VERIFIER:-}" ]; then
    "$COURSE_MIGRATION_GATE_VERIFIER" --phase "$phase"
  else
    if [ -z "$UV_BIN" ]; then
      resolve_uv
    fi
    "$UV_BIN" run python \
      "$SCRIPT_DIR/verify-course-migration-gate.py" --phase "$phase"
  fi
}

cd "$REPOSITORY_ROOT"
start_container
wait_for_health
run_verifier "seed-up"

docker stop "$CONTAINER_NAME" >/dev/null
docker rm "$CONTAINER_NAME" >/dev/null
CONTAINER_EXISTS=0

start_container
wait_for_health
run_verifier "restart-down-up"

echo "Course migration disk gate passed."
