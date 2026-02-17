#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT/.env.dev}"
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT/infra/docker-compose.yml}"

MSG="${1:-}"

if [[ -n "$MSG" ]]; then
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T billing \
    alembic revision --autogenerate -m "$MSG"
fi

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T billing \
  alembic upgrade head
