"""P6-07: bge-m3 embedding throughput on the local llama.cpp server (document ingestion capacity).

Run inside a container on embed_private (the MCP has it), with synthetic 1500-character chunks (rag_api CHUNK_SIZE):
  docker exec -i kai-clickhouse-mcp-1 python - <concurrency> <seconds> < tests/load/load_embeddings.py
"""
import json
import sys
import threading
import time
import urllib.request

CONCURRENCY, SECONDS = (int(a) for a in (sys.argv[1:] + ["4", "30"])[:2])
CHUNK = ("Laporan penjualan wilayah timur kuartal ketiga menunjukkan kenaikan pada produk minuman, "
         "sementara biaya distribusi naik karena jarak gudang. ") * 11  # ~1500 characters, like a rag_api chunk
latencies, errors, lock = [], [0], threading.Lock()


def worker(until):
    while time.monotonic() < until:
        started = time.monotonic()
        try:
            request = urllib.request.Request("http://embeddings:8080/v1/embeddings", method="POST",
                                             data=json.dumps({"model": "bge-m3", "input": CHUNK[:1500]}).encode(),
                                             headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=120) as response:
                assert len(json.load(response)["data"][0]["embedding"]) == 1024
            with lock:
                latencies.append(time.monotonic() - started)
        except Exception:
            with lock:
                errors[0] += 1


until = time.monotonic() + SECONDS
threads = [threading.Thread(target=worker, args=(until,)) for _ in range(CONCURRENCY)]
for t in threads:
    t.start()
for t in threads:
    t.join()
latencies.sort()
pick = lambda p: latencies[min(len(latencies) - 1, int(len(latencies) * p))] * 1000 if latencies else float("nan")
print(f"concurrency={CONCURRENCY} chunks={len(latencies)} chunks/s={len(latencies) / SECONDS:.2f} "
      f"p50={pick(0.5):.0f}ms p95={pick(0.95):.0f}ms errors={errors[0]}")
