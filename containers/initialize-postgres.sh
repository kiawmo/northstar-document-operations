#!/bin/sh
set -eu
export DOCOPS_APP_PASSWORD="$(cat /run/secrets/postgres_app_password)"
export PGPASSWORD="$(cat /run/secrets/postgres_admin_password)"
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set=ON_ERROR_STOP=1 <<'SQL'
\getenv app_password DOCOPS_APP_PASSWORD
CREATE ROLE docops LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEDB NOCREATEROLE;
ALTER DATABASE docops OWNER TO docops;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
SQL
unset DOCOPS_APP_PASSWORD
unset PGPASSWORD
