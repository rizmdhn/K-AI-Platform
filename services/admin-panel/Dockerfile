# Upstream source and AGPL license are retained in /app alongside the derivative.
FROM registry.librechat.ai/clickhouse/librechat-admin-panel@sha256:a395465d1daa3c11495810a8c224df86675985d50de42e98e36eeedbd8aecb94
USER root
ADD --checksum=sha256:88a58326cefe2a3875298fbfa0859d628de238d0f13cb5473bd6bf5beed4b59a https://codeload.github.com/ClickHouse/librechat-admin-panel/tar.gz/fce9596f823e78f991c7ef60bb97d0ce15d72934 /tmp/panel-source.tar.gz
RUN tar -xzf /tmp/panel-source.tar.gz --strip-components=1 -C /app && rm /tmp/panel-source.tar.gz
RUN rm -rf node_modules && bun install --frozen-lockfile
COPY overlays/ /app/kai-overlays/
COPY apply.mjs /app/kai-apply.mjs
COPY --from=kai_branding logo.svg icon.svg /app/kai-branding/
RUN node /app/kai-apply.mjs && rm -rf kai-overlays dist .tanstack node_modules/.vite \
    && node_modules/.bin/tsc --noEmit && bun run build
