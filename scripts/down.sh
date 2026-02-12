#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
docker compose --env-file "$ROOT/.env.dev" -f "$ROOT/infra/docker-compose.yml" down
