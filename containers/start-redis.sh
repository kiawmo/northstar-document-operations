#!/bin/sh
set -eu
password="$(cat /run/secrets/redis_password)"
case "$password" in *[!A-Za-z0-9_-]*|'') echo 'Invalid protected Redis password format' >&2; exit 1;; esac
[ "${#password}" -ge 32 ] || exit 1
umask 077
printf 'bind 0.0.0.0\nprotected-mode yes\nport 6379\nappendonly yes\nappendfsync everysec\nmaxmemory 128mb\nmaxmemory-policy noeviction\nrequirepass %s\n' "$password" > /data/docops-redis.conf
unset password
exec /usr/local/bin/docker-entrypoint.sh redis-server /data/docops-redis.conf
