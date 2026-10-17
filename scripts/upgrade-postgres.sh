#!/usr/bin/env bash
set -euo pipefail

if [[ $# != 2 ]]; then
  echo "usage: bash scripts/upgrade-postgres.sh OLD_IMAGE NEW_IMAGE" >&2
  echo "stop the application's containers before upgrading data/postgres17" >&2
  exit 2
fi
old_image=$1
new_image=$2
source=$PWD/data/postgres17
target=$PWD/data/postgres18
mkdir -p data
state=$(docker run --rm --user 0 --entrypoint sh -v "$PWD/data:/data" "$new_image" -c '
  if [ -e /data/postgres18 ]; then
    test "$(cat /data/postgres18/PG_VERSION)" = 18 && echo ready
  elif [ -e /data/postgres17 ]; then
    test "$(cat /data/postgres17/PG_VERSION)" = 17 && echo upgrade
  else
    echo fresh
  fi
')
case "$state" in
  ready) echo "PostgreSQL 18 cluster is already available" >&2; exit 0 ;;
  fresh) echo "No PostgreSQL 17 cluster to upgrade" >&2; exit 0 ;;
  upgrade) ;;
  *) echo "error: expected data/postgres17 and an absent data/postgres18" >&2; exit 1 ;;
esac

staging=$(docker run --rm --user 0 --entrypoint sh -v "$PWD/data:/data" "$new_image" \
  -c 'mktemp -d /data/.postgres18-upgrade-XXXXXXXX')
staging=$PWD/data/${staging##*/}
container=openaedmap-upgrade-${staging##*-}
trap 'docker rm -f "$container" >/dev/null 2>&1 || true' EXIT
docker run --rm -i --user 0 --entrypoint sh -v "$staging:/upgrade" "$new_image" -ceu '
  cat > /upgrade/manifest.sql
  chown -R 999:999 /upgrade
  chmod 700 /upgrade
' <<'SQL'
SELECT 'aed', count(*), md5(coalesce(string_agg(md5(
  jsonb_build_array(id,version,tags,encode(ST_AsEWKB(position),'hex'),country_codes)::text
),'' ORDER BY id),'')) FROM aed;
SELECT 'country', count(*), md5(coalesce(string_agg(md5(
  jsonb_build_array(code,names,encode(ST_AsEWKB(geometry),'hex'),encode(ST_AsEWKB(label_position),'hex'))::text
),'' ORDER BY code COLLATE "C"),'')) FROM country;
SELECT 'photo', count(*), md5(coalesce(string_agg(md5(to_jsonb(t)::text),'' ORDER BY id COLLATE "C"),'')) FROM photo t;
SELECT 'photo_report', count(*), md5(coalesce(string_agg(md5(to_jsonb(t)::text),'' ORDER BY id COLLATE "C"),'')) FROM photo_report t;
SELECT 'state', count(*), md5(coalesce(string_agg(md5(to_jsonb(t)::text),'' ORDER BY key COLLATE "C"),'')) FROM state t;
SELECT 'roles', count(*), md5(coalesce(string_agg(md5(
  jsonb_build_array(rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,
    rolreplication,rolconnlimit,rolpassword,rolvaliduntil,rolbypassrls)::text
),'' ORDER BY rolname COLLATE "C"),'')) FROM pg_authid WHERE left(rolname,3) <> 'pg_';
SQL

echo "Exporting PostgreSQL 17 data and roles to $staging" >&2
timeout 10m docker run --rm -i --name "$container" --entrypoint /bin/sh \
  -v "$source:/app/data/postgres" -v "$staging:/upgrade" "$old_image" -seu <<'BASH'
  set -o pipefail
  umask 077
  export PGHOST=/tmp/upgrade-postgres PGUSER=postgres PGDATABASE=postgres
  mkdir "$PGHOST"
  rm -f data/postgres/postmaster.pid
  pg_ctl -D data/postgres -l /upgrade/source.log -o "-k $PGHOST -h \"\"" -w start >&2
  trap "pg_ctl -D data/postgres -m fast -w stop >&2" EXIT
  test "$(psql -XAtc "SELECT count(*) FROM pg_database WHERE NOT datistemplate AND datname <> 'postgres'")" = 0
  test "$(psql -XAtc "SELECT count(*) FROM pg_tablespace WHERE spcname NOT IN ('pg_default','pg_global')")" = 0
  psql -XAt -F "|" -v ON_ERROR_STOP=1 -f /upgrade/manifest.sql > /upgrade/source.inventory
  pg_dumpall --globals-only > /upgrade/globals.sql
  pg_dump --format=custom --file=/upgrade/database.dump
BASH

echo "Restoring and checking PostgreSQL 18" >&2
timeout 10m docker run --rm -i --name "$container" --entrypoint /bin/sh \
  -v "$staging:/upgrade" "$new_image" -seu <<'BASH'
  set -o pipefail
  umask 077
  export PGHOST=/tmp/upgrade-postgres PGUSER=postgres PGDATABASE=postgres
  mkdir "$PGHOST"
  initdb -D /upgrade/cluster --auth=trust --username=postgres --locale-provider=icu --icu-locale=und --no-locale >&2
  pg_ctl -D /upgrade/cluster -l /upgrade/target.log -o "-k $PGHOST -h \"\"" -w start >&2
  trap "pg_ctl -D /upgrade/cluster -m fast -w stop >&2" EXIT
  grep -v "^CREATE ROLE postgres;$" /upgrade/globals.sql |
    psql -X -v ON_ERROR_STOP=1 >&2
  pg_restore --exit-on-error --dbname=postgres /upgrade/database.dump
  psql -XAt -F "|" -v ON_ERROR_STOP=1 -f /upgrade/manifest.sql > /upgrade/target.inventory
  python -c 'import filecmp,sys; sys.exit(not filecmp.cmp("/upgrade/source.inventory", "/upgrade/target.inventory", shallow=False))'
  psql -X -v ON_ERROR_STOP=1 --single-transaction -f schema.sql >&2
  psql -X -v ON_ERROR_STOP=1 -c "VACUUM (ANALYZE)" >&2
  psql -X -v ON_ERROR_STOP=1 >&2 <<'SQL'
    DO $$ BEGIN
      IF (SELECT feature_count FROM map_revision) <> (SELECT count(*) FROM aed WHERE tags IS NOT NULL)
      THEN RAISE EXCEPTION 'restored feature count mismatch'; END IF;
    END $$;
SQL
BASH

docker run --rm --user 0 --entrypoint sh -v "$PWD/data:/data" -v "$staging:/upgrade" "$new_image" -ceu '
  test ! -e /data/postgres18
  sync -f /upgrade
  mv /upgrade/cluster /data/postgres18
  sync -f /data
'
echo "Published $target; preserved $source and the backup in $staging" >&2
