#!/usr/bin/env bash
# Consistent-enough data backup for kAI (P6-02). Run via `make backup`, which supplies COMPOSE.
# Includes: Mongo (chats, users, agents, config overrides), the kai-metadata Postgres cluster
# (bindings, approvals, catalog, DB logins) when it runs locally, the uploads volume and the RustFS
# document bucket.
# Excludes on purpose: .env, CREDS_KEY and the binding keyring (escrow them separately; the dumps
# hold ciphertext that is useless without them) and Meilisearch (rebuilt from Mongo, see runbook).
set -euo pipefail
: "${COMPOSE:?run through make backup}"
out="${KAI_BACKUP_DIR:-backups}/$(date -u +%Y%m%dT%H%M%SZ)"
umask 077
mkdir -p "$out"
running() { $COMPOSE ps --status running --services | grep -qx "$1"; }

$COMPOSE exec -T mongodb mongodump --quiet --archive --gzip > "$out/mongo.archive.gz"
if running kai-metadata; then
  $COMPOSE exec -T kai-metadata sh -c \
    'PGPASSWORD="$POSTGRES_PASSWORD" pg_dumpall -h /var/run/postgresql -U postgres' | gzip > "$out/postgres.sql.gz"
else
  echo "kai-metadata not running here; back up the external Postgres with its own tooling." >&2
fi
$COMPOSE exec -T api tar -C /app/uploads -czf - . > "$out/uploads.tar.gz"
# Uploaded documents (P6-12). ponytail: tar of the live RustFS data dir is crash-consistent only;
# switch to an S3-level copy (rclone/mc mirror) or a volume snapshot when uploads are busy.
if running rustfs; then
  $COMPOSE exec -T rustfs tar -C /data -czf - . > "$out/rustfs.tar.gz"
fi
# RAG chunks and embeddings (documents profile). Rebuilding them means re-embedding every document.
if running vectordb; then
  $COMPOSE exec -T vectordb sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' | gzip > "$out/vectordb.sql.gz"
fi
# Audit trail (P6-11): daily audit.jsonl files kept KAI_LOG_RETENTION_DAYS on the audit-logs volume.
if running clickhouse-mcp; then
  $COMPOSE exec -T clickhouse-mcp tar -C /var/log/kai -czf - . > "$out/audit-logs.tar.gz"
fi

for f in "$out"/*.gz; do gzip -t "$f"; done
(cd "$out" && shasum -a 256 ./*.gz > SHA256SUMS)
echo "$out"
