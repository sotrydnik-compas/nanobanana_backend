#!/usr/bin/env sh
set -eu

create_user() {
  USER="$1"
  PASS="$2"
  EXISTS="$(psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" -tAc "SELECT 1 FROM pg_roles WHERE rolname='${USER}'")"
  if [ "$EXISTS" != "1" ]; then
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" -c "CREATE USER \"${USER}\" WITH PASSWORD '${PASS}';"
  fi
}

create_db() {
  DB="$1"
  OWNER="$2"
  EXISTS="$(psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" -tAc "SELECT 1 FROM pg_database WHERE datname='${DB}'")"
  if [ "$EXISTS" != "1" ]; then
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" -c "CREATE DATABASE \"${DB}\" OWNER \"${OWNER}\";"
  fi
}

grant_schema() {
  DB="$1"
  USER="$2"
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$DB" -c "GRANT USAGE, CREATE ON SCHEMA public TO \"${USER}\";"
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$DB" -c "ALTER SCHEMA public OWNER TO \"${USER}\";"
}

# users first
create_user "$AUTH_DB_USER" "$AUTH_DB_PASSWORD"
create_user "$BILLING_DB_USER" "$BILLING_DB_PASSWORD"
create_user "$AI_DB_USER" "$AI_DB_PASSWORD"

# dbs with correct owner
create_db "$AUTH_DB_NAME" "$AUTH_DB_USER"
create_db "$BILLING_DB_NAME" "$BILLING_DB_USER"
create_db "$AI_DB_NAME" "$AI_DB_USER"

# schema privileges
grant_schema "$AUTH_DB_NAME" "$AUTH_DB_USER"
grant_schema "$BILLING_DB_NAME" "$BILLING_DB_USER"
grant_schema "$AI_DB_NAME" "$AI_DB_USER"
