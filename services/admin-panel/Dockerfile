# K-AI Admin Panel: ClickHouse/librechat-admin-panel @ fce9596f823e78f991c7ef60bb97d0ce15d72934 with K-AI changes,
# kept as source in panel/ (AGPL-3.0, panel/LICENSE; how to update in UPSTREAM.md). The base image provides the runtime.
FROM registry.librechat.ai/clickhouse/librechat-admin-panel@sha256:a395465d1daa3c11495810a8c224df86675985d50de42e98e36eeedbd8aecb94
USER root
COPY panel/ /app/
RUN rm -rf node_modules dist .tanstack && bun install --frozen-lockfile \
    && node_modules/.bin/tsc --noEmit && bun run build
