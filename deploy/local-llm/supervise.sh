#!/bin/sh
# local-llm entrypoint: llama.cpp in router mode, restarted whenever the model folder changes.
# Router mode reads --models-dir only at startup, so a finished download or a deleted model needs a fresh
# llama-server process. This restarts only that process (a few seconds), never the container; loading a model
# into memory stays on demand (POST /models/load). Downloads are written by the MCP (uid 10001) through the
# shared volume; this container has no internet route.
set -u
MODELS=/models/gguf
mkdir -p "$MODELS" /models/downloads
# The MCP also writes /models/active, so it owns the folder itself, not only the two subfolders.
chown "${KAI_MCP_UID:-10001}:${KAI_MCP_UID:-10001}" /models "$MODELS" /models/downloads
listing() { ls -l "$MODELS" 2>/dev/null | md5sum; }
stop() { kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null; exit 0; }
trap stop TERM INT
while true; do
  seen=$(listing)
  /app/llama-server --host 0.0.0.0 --port 8080 --models-dir "$MODELS" --models-max 1 \
    -t "${KAI_LOCAL_LLM_THREADS:-4}" -c "${KAI_LOCAL_LLM_CONTEXT:-8192}" &
  pid=$!
  while kill -0 "$pid" 2>/dev/null; do
    sleep 5
    if [ "$(listing)" != "$seen" ]; then
      echo "local-llm: model folder changed, restarting llama-server"
      kill "$pid"; wait "$pid" 2>/dev/null
      break
    fi
  done
  sleep 1
done
