FROM registry.librechat.ai/librechat-ai/librechat:v0.8.8@sha256:5b706bcee1ca154a1e2987303aaeca0eb27f97919cccee4bb604226c2f8abf61
USER root
COPY --from=kai_branding apply.mjs logo.svg icon.svg /app/kai-branding/
RUN node /app/kai-branding/apply.mjs
COPY *.cjs *.mjs /app/kai-platform/
RUN node /app/kai-platform/install.mjs
USER 1000:1000
