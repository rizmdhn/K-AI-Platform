#!/bin/bash
# Usage (from App, stack running): bash scripts/restore_drill_keyring.sh backups/<UTC>. No live writes; prints only counts and OK/FAIL.
# P6-03 keyring drill: backup -> fresh PG -> synthetic binding encrypted with escrow keyring copy -> pg_dumpall -> second fresh PG -> decrypt.
set -euo pipefail
ERR=$(mktemp)
B=${1:?usage: restore_drill_keyring.sh backups/<UTC>}; B=$(cd "$B" && pwd)
PG=$(docker inspect kai-kai-metadata-1 --format "{{.Config.Image}}")
MCP=$(docker inspect kai-clickhouse-mcp-1 --format "{{.Config.Image}}")
NET=kai-drill-net
cleanup(){ rm -f "$ERR"; docker rm -f kai-drill-pg1 kai-drill-pg2 >/dev/null 2>&1 || true; docker volume rm kai-drill-keys >/dev/null 2>&1 || true; docker network rm $NET >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup
PW=$(openssl rand -hex 16)
docker network create --internal $NET >/dev/null
# 1. "escrow" copy of the keyring (backup deliberately excludes it)
docker volume create kai-drill-keys >/dev/null
docker run --rm --network none --entrypoint sh -v kai_metadata_binding_keys:/src:ro -v kai-drill-keys:/dst $PG -c 'cp -a /src/. /dst/ && chmod 700 /dst'
start(){ docker run -d --name $1 --network $NET -e POSTGRES_PASSWORD=$PW $PG >/dev/null; for i in $(seq 30); do docker exec $1 pg_isready -U postgres -q 2>/dev/null && sleep 2 && return; sleep 1; done; exit 1; }
psqlc(){ docker exec -i -e PGPASSWORD=$PW $1 psql -v ON_ERROR_STOP=0 -q -U postgres -d postgres "${@:2}"; }
# 2. restore the real backup into PG1
start kai-drill-pg1
gunzip -c $B/postgres.sql.gz | psqlc kai-drill-pg1 >/dev/null 2>"$ERR" || true
echo "restore errors (non-'already exists'):" $(grep -c ERROR "$ERR" | tr -d ' ') total, $(grep ERROR "$ERR" | grep -vc 'already exists' | tr -d ' ') other
docker exec -e PGPASSWORD=$PW kai-drill-pg1 psql -U postgres -d kai_metadata -At -c "select 'tables', count(*) from pg_tables where schemaname='kai'" -c "select 'roles', count(*) from pg_roles where rolname like 'kai%'" -c "select 'bindings', count(*), count(*) filter (where status='revoked' and cipher_blob is null) from kai.ch_bindings" -c "select 'active', count(*) from kai.active_ch_bindings"
# 3. synthetic binding encrypted with the escrow keyring, stored in PG1
SECRET=$(openssl rand -hex 24); WANT=$(printf %s "$SECRET" | shasum -a 256 | cut -c1-16)
CTX='["00000000-0000-4000-8000-000000000001","drill_user",1,"00000000-0000-4000-8000-000000000002","https://drill.invalid:8443"]'
HEX=$(printf %s "$SECRET" | docker run --rm -i --network none -v kai-drill-keys:/k:ro -e CTX="$CTX" --entrypoint python $MCP -c '
import json,os,sys,uuid; from credential_crypto import BindingContext,encrypt_password,load_keyring
p,u,v,b,t=json.loads(os.environ["CTX"]); c=BindingContext(uuid.UUID(p),u,v,uuid.UUID(b),t)
print(encrypt_password(sys.stdin.read(),c,load_keyring("/k")).hex())')
docker exec -e PGPASSWORD=$PW kai-drill-pg1 psql -q -U postgres -d kai_metadata -c "create table kai.drill_secret(ctx text, blob bytea); insert into kai.drill_secret values ('$CTX', decode('$HEX','hex'))"
unset SECRET HEX
# 4. same dump format as scripts/backup.sh, restored into a fresh PG2
start kai-drill-pg2
docker exec -e PGPASSWORD=$PW kai-drill-pg1 pg_dumpall -U postgres | gzip | gunzip -c | psqlc kai-drill-pg2 >/dev/null 2>/dev/null || true
# 5. decrypt from PG2 with the escrow keyring; compare only a hash prefix
GOT=$(docker exec -e PGPASSWORD=$PW kai-drill-pg2 psql -U postgres -d kai_metadata -At -c "select ctx||'|'||encode(blob,'hex') from kai.drill_secret" | docker run --rm -i --network none -v kai-drill-keys:/k:ro --entrypoint python $MCP -c '
import hashlib,json,sys,uuid; from credential_crypto import BindingContext,decrypt_password,load_keyring
ctx,h=sys.stdin.read().strip().split("|"); p,u,v,b,t=json.loads(ctx)
pw=decrypt_password(bytes.fromhex(h),BindingContext(uuid.UUID(p),u,v,uuid.UUID(b),t),load_keyring("/k"))
print(hashlib.sha256(pw.encode()).hexdigest()[:16])')
[ "$GOT" = "$WANT" ] && echo "KEYRING DRILL OK: restored ciphertext decrypts with escrow keyring" || { echo "KEYRING DRILL FAIL"; exit 1; }
# 6. negative: a wrong keyring must not decrypt
docker volume create kai-drill-badkeys >/dev/null
docker run --rm --network none --entrypoint sh -v kai-drill-badkeys:/k $PG -c 'head -c32 /dev/urandom >/k/1.key; chmod 600 /k/1.key; chmod 700 /k; chown -R 10001:10001 /k'
docker exec -e PGPASSWORD=$PW kai-drill-pg2 psql -U postgres -d kai_metadata -At -c "select ctx||'|'||encode(blob,'hex') from kai.drill_secret" | docker run --rm -i --network none -v kai-drill-badkeys:/k:ro --entrypoint python $MCP -c '
import json,sys,uuid; from credential_crypto import BindingContext,decrypt_password,load_keyring,CredentialError
ctx,h=sys.stdin.read().strip().split("|"); p,u,v,b,t=json.loads(ctx)
try: decrypt_password(bytes.fromhex(h),BindingContext(uuid.UUID(p),u,v,uuid.UUID(b),t),load_keyring("/k")); print("WRONG KEY DECRYPTED - FAIL"); sys.exit(1)
except CredentialError: print("wrong keyring refused: OK")'
docker volume rm kai-drill-badkeys >/dev/null
