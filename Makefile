MCP_OVERLAY := $(shell python3 scripts/bootstrap.py --mcp-overlay || echo __KAI_OVERLAY_ERROR__)
ifneq ($(filter __KAI_OVERLAY_ERROR__,$(MCP_OVERLAY)),)
$(error Konfigurasi overlay ditolak; periksa .env secara privat sebelum melanjutkan)
endif
COMPOSE = docker compose --project-name kai --project-directory deploy --env-file .env -f deploy/upstream/docker-compose.yml -f deploy/docker-compose.override.yml $(MCP_OVERLAY)

.PHONY: setup preflight check up down logs ps test mcp-test mcp-image admin-create backup embeddings-up documents-up mcp-up admin-up managed-chat metrics-up metrics binding-check agents-check agents-setup warehouse-local-check warehouse-local-setup evals smoke-config browser-warehouse-test worker-test schema-test rag-acl-test

setup:
	python3 scripts/bootstrap.py

# Host readiness before the first start (tools, versions, disk, memory, ports, egress, CH, .env).
preflight:
	python3 scripts/preflight.py

check: setup
	$(COMPOSE) config --quiet

up: check
	$(COMPOSE) up -d --build --wait --wait-timeout 180
	$(MAKE) managed-chat

mcp-up: check
	@test -n "$(MCP_OVERLAY)" || (echo 'Set KAI_MCP_ENABLED=true and CLICKHOUSE_HOST in .env, then run make setup.' >&2; exit 1)
	$(COMPOSE) up -d --build --wait --wait-timeout 180 clickhouse-mcp api
	$(MAKE) managed-chat

admin-up: check
	$(COMPOSE) --profile admin up -d --build --wait --wait-timeout 180 api admin-panel
	$(MAKE) managed-chat

# Signup stays closed: the first account created here becomes ADMIN, later ones are USER (prefer Admin Panel → Users).
# Prompts for email, name, username and password, so the password never lands in shell history.
admin-create:
	$(COMPOSE) exec api node config/create-user.js

backup:
	COMPOSE="$(COMPOSE)" bash scripts/backup.sh  # release archives drop the exec bit

# Local CPU embedding server (documents profile); the first start downloads the model (~635 MB) once.
embeddings-up: check
	$(COMPOSE) --profile documents up -d --wait --wait-timeout 900 embeddings

# Local RAG stack: embeddings + pgvector + rag_api. Set KAI_RAG_API_URL=http://rag_api:8000 in .env so api uses it.
documents-up: embeddings-up
	$(COMPOSE) --profile documents up -d --wait --wait-timeout 300 vectordb rag_api

managed-chat:
	$(COMPOSE) exec -T api node config/kai-managed-chat.cjs
	$(COMPOSE) restart api
	$(COMPOSE) up -d --wait --wait-timeout 180 --no-deps api

metrics-up: check
	@test -n "$(MCP_OVERLAY)" || (echo 'Enable the private MCP service in .env first.' >&2; exit 1)
	python3 scripts/configure_metrics.py
	$(MAKE) mcp-up

metrics:
	$(COMPOSE) exec -T clickhouse-mcp python -m gateway_metrics

# Local configuration/key checks only; never contacts ClickHouse or PostgreSQL.
binding-check:
	@test -n "$(MCP_OVERLAY)" || (echo 'Aktifkan MCP dan siapkan konfigurasi binding terlebih dahulu.' >&2; exit 1)
	$(COMPOSE) exec -T clickhouse-mcp python -m binding_preflight

# Operator commands: metadata/ACL/config only; never calls a model or CH tool.
agents-check:
	$(COMPOSE) exec -T api node /app/kai-platform/setup_agents.cjs --check $(if $(AGENT_OWNER_ID),--owner "$(AGENT_OWNER_ID)",)

agents-setup:
	$(COMPOSE) exec -T api node /app/kai-platform/setup_agents.cjs --apply $(if $(AGENT_OWNER_ID),--owner "$(AGENT_OWNER_ID)",)
	$(COMPOSE) restart api
	$(COMPOSE) up -d --no-build --pull never --wait --wait-timeout 120 --no-deps api

# The local metadata check/setup run the MCP image, so build it first (a fresh machine has none).
mcp-image: check
	@test -n "$(MCP_OVERLAY)" || (echo 'Set KAI_MCP_ENABLED=true and CLICKHOUSE_HOST in .env, then run make setup.' >&2; exit 1)
	$(COMPOSE) build clickhouse-mcp

warehouse-local-check: mcp-image
	python3 scripts/setup_local_metadata.py --check

warehouse-local-setup: mcp-image
	python3 scripts/setup_local_metadata.py --apply
	$(MAKE) mcp-up

evals:
	python3 evals/validate.py

down:
	$(COMPOSE) --profile admin down

logs:
	$(COMPOSE) logs --tail 100 -f api

ps:
	$(COMPOSE) ps

test: setup
	python3 -m unittest discover -s tests -v
	python3 -m unittest discover -s services/metadata-worker/tests -p 'test_*.py' -v
	node --test tests/test_*.cjs tests/test_*.mjs

# MCP memakai Python 3.12 dan dependensi yang sama dengan runtime, tanpa jaringan.
mcp-test:
	docker run --rm --pull never --network none --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges --mount type=bind,src=$(CURDIR)/services/clickhouse-mcp,dst=/app,readonly --entrypoint python kai-clickhouse-mcp:0.7.0 -m unittest discover -s tests -v


# P2-11: credential lifecycle in the browser against a throwaway local TLS ClickHouse (never CH Cloud).
# Needs Playwright (NODE_PATH, PLAYWRIGHT_BROWSERS_PATH); the MCP is restored to the .env target afterwards.
browser-warehouse-test:
	COMPOSE="$(COMPOSE)" bash tests/warehouse_local_tls.sh up
	node tests/browser_warehouse_lifecycle.cjs; status=$$?; COMPOSE="$(COMPOSE)" bash tests/warehouse_local_tls.sh down; exit $$status

smoke-config:
	python3 tests/smoke_unified_env.py

worker-test:
	python3 -m unittest discover -s services/metadata-worker/tests -p test_worker.py -v
	KAI_WORKER_TEST_IMAGE=kai-clickhouse-mcp:0.7.0 python3 -m unittest discover -s db/tests -p test_worker_queue.py -v

schema-test:
	python3 -m unittest discover -s services/metadata-worker/tests -p test_schema_snapshot.py -v
	KAI_SCHEMA_TEST_IMAGE=kai-clickhouse-mcp:0.7.0 python3 -m unittest discover -s tests -p test_schema_snapshot_integration.py -v

rag-acl-test:
	docker run --rm --pull never --network none --read-only --tmpfs /tmp --tmpfs /app/logs:rw,mode=1777 --cap-drop ALL --security-opt no-new-privileges --entrypoint node --mount type=bind,src=$(CURDIR)/tests/probe_native_file_acl.cjs,dst=/app/probe_native_file_acl.cjs,readonly kai-librechat:0.2.0 --test /app/probe_native_file_acl.cjs
