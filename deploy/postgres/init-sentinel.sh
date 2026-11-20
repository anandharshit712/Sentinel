#!/bin/sh
# First-boot database setup (postgres image runs /docker-entrypoint-initdb.d/* once, on an empty
# volume). The application role must NOT be a superuser: superusers bypass row-level security,
# and tenancy (migration 0007) depends on it. Keycloak gets its own role and database.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE ROLE sentinel LOGIN PASSWORD '${SENTINEL_DB_PASSWORD}' NOSUPERUSER NOBYPASSRLS NOCREATEROLE;
CREATE DATABASE sentinel OWNER sentinel;
CREATE ROLE keycloak LOGIN PASSWORD '${KEYCLOAK_DB_PASSWORD}' NOSUPERUSER;
CREATE DATABASE keycloak OWNER keycloak;
SQL
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname sentinel <<SQL
CREATE SCHEMA sentinel AUTHORIZATION sentinel;
ALTER ROLE sentinel SET search_path = sentinel;
SQL
