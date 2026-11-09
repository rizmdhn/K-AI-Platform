#!/bin/bash
# P2-11 harness: a disposable ClickHouse with TLS from a throwaway CA, and the local MCP pointed at it, so the
# credential lifecycle can be tested without ClickHouse Cloud. `down` removes it and restores the normal MCP.
# Run through make: make browser-warehouse-test (uses the Makefile COMPOSE). Never use on a shared server.
set -euo pipefail
: "${COMPOSE:?run through make browser-warehouse-test}"
DIR=${KAI_TLS_DIR:-/tmp/kai-test-tls}
CH_IMAGE=clickhouse/clickhouse-server@sha256:0152dd511befe6a2c2ef53e930726179669b08116da78500b37c51c96ff5ee77
NAME=kai-test-ch

down() {
  docker rm -f $NAME >/dev/null 2>&1 || true
  $COMPOSE up -d --wait --wait-timeout 120 clickhouse-mcp >/dev/null  # back to the .env warehouse target
  rm -rf "$DIR"
  echo "Local TLS warehouse removed; MCP restored to the configured target."
}

up() {
  rm -rf "$DIR" && mkdir -p "$DIR" && chmod 700 "$DIR"
  openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj "/CN=kai-test-ca" -keyout "$DIR/ca.key" -out "$DIR/ca.crt" 2>/dev/null
  openssl req -newkey rsa:2048 -nodes -subj "/CN=$NAME" -keyout "$DIR/server.key" -out "$DIR/server.csr" 2>/dev/null
  printf "subjectAltName=DNS:%s\n" $NAME > "$DIR/san.ext"
  openssl x509 -req -in "$DIR/server.csr" -CA "$DIR/ca.crt" -CAkey "$DIR/ca.key" -CAcreateserial -days 1 -extfile "$DIR/san.ext" -out "$DIR/server.crt" 2>/dev/null
  chmod 644 "$DIR"/*.crt "$DIR/server.key"; chmod 755 "$DIR"
  cat > "$DIR/tls.xml" <<XML
<clickhouse><https_port>8443</https_port><openSSL><server><certificateFile>/tls/server.crt</certificateFile>
<privateKeyFile>/tls/server.key</privateKeyFile><verificationMode>none</verificationMode><loadDefaultCAFile>false</loadDefaultCAFile>
</server></openSSL></clickhouse>
XML
  PW=$(openssl rand -hex 16)
  docker rm -f $NAME >/dev/null 2>&1 || true
  docker run -d --name $NAME --network kai_tool_egress -e CLICKHOUSE_USER=kai_e2e -e CLICKHOUSE_PASSWORD="$PW" -e CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1 \
    -v "$DIR:/tls:ro" -v "$DIR/tls.xml:/etc/clickhouse-server/config.d/tls.xml:ro" $CH_IMAGE >/dev/null
  for _ in $(seq 60); do docker exec $NAME clickhouse-client --user kai_e2e --password "$PW" -q "SELECT 1" >/dev/null 2>&1 && break; sleep 1; done
  cat > "$DIR/mcp.yml" <<YML
services:
  clickhouse-mcp:
    environment:
      CLICKHOUSE_HOST: $NAME
      CLICKHOUSE_PORT: "8443"
      CLICKHOUSE_CA_CERT: /run/kai-test-tls/ca.crt
    volumes:
      - $DIR/ca.crt:/run/kai-test-tls/ca.crt:ro
YML
  $COMPOSE -f "$DIR/mcp.yml" up -d --wait --wait-timeout 120 clickhouse-mcp >/dev/null
  # Ready = the MCP itself reaches ClickHouse over HTTPS with the test CA (DNS + TLS + port), not just a healthy container.
  for i in $(seq 60); do
    docker exec kai-clickhouse-mcp-1 python -c "import httpx2 as h,warehouse_enrollment as e;h.get('https://$NAME:8443/ping',verify=e._tls(),trust_env=False,timeout=3).raise_for_status()" >/dev/null 2>&1 && break
    [ "$i" = 60 ] && { echo "Local TLS warehouse not reachable from MCP" >&2; exit 1; }
    sleep 1
  done
  printf %s "$PW" > "$DIR/password" && chmod 600 "$DIR/password"
  echo "Local TLS warehouse up: $NAME:8443, admin user kai_e2e (password in $DIR/password); tests create their own CH users."
}

"${1:-up}"
