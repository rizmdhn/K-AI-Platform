"""Local model manager for semantic layer drafts: curated catalog, download, activate, delete (Admin Panel).

Files live in the shared local-llm-models volume (KAI_LOCAL_MODELS_DIR, /models): finished models in gguf/, partial
downloads in downloads/. The local-llm container (llama.cpp router mode) serves gguf/ and restarts its process on
its own when that folder changes; loading into memory is a router call. Only catalog models are downloaded, from
huggingface.co, size and SHA-256 checked against the Hub's own metadata before the file is moved into gguf/. Files
copied in by hand (closed networks) show up as "manual". The active model is recorded in /models/active.
ponytail: download state lives in process memory (one download at a time); a restart keeps the partial file, and
"Lanjutkan" resumes it with an HTTP Range request.
"""

import asyncio
import hashlib
import os
import re
import shutil
import time

import httpx2 as httpx

ROUTER = os.getenv("KAI_DRAFT_MODEL_URL", "").rstrip("/").removesuffix("/v1")
ROOT = os.getenv("KAI_LOCAL_MODELS_DIR", "/models")
HUB = "https://huggingface.co"
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
SPARE_BYTES = 1 << 30  # keep 1 GB free after a download
CHUNK = 1 << 20
# id = file name without .gguf (llama.cpp router mode names models after the file).
CATALOG = [
    {"id": "Qwen2.5-Coder-3B-Instruct-Q4_K_M", "label": "Qwen2.5-Coder 3B", "repo": "bartowski/Qwen2.5-Coder-3B-Instruct-GGUF",
     "size": 1929903360, "ram_gb": 3, "note": "Paling cepat, kualitas cukup untuk skema sederhana."},
    {"id": "Qwen2.5-Coder-7B-Instruct-Q4_K_M", "label": "Qwen2.5-Coder 7B", "repo": "bartowski/Qwen2.5-Coder-7B-Instruct-GGUF",
     "size": 4683074336, "ram_gb": 6, "note": "Rekomendasi. Diuji di kai-dev: sekitar 2 tabel per 3,5 menit dengan 4 thread CPU."},
    {"id": "Qwen2.5-Coder-14B-Instruct-Q4_K_M", "label": "Qwen2.5-Coder 14B", "repo": "bartowski/Qwen2.5-Coder-14B-Instruct-GGUF",
     "size": 8988111072, "ram_gb": 11, "note": "Lebih akurat, sekitar dua kali lebih lambat dari 7B. Butuh RAM besar."},
]
_download = {}
_task = None
_cancel = asyncio.Event()


class ModelError(ValueError):
    """Message is safe to show to the admin."""


def _paths():
    return os.path.join(ROOT, "gguf"), os.path.join(ROOT, "downloads"), os.path.join(ROOT, "active")


def _catalog(model_id):
    return next((m for m in CATALOG if m["id"] == model_id), None)


def _valid(model_id):
    if not isinstance(model_id, str) or not MODEL_ID.fullmatch(model_id) or ".." in model_id:
        raise ModelError("Model tidak dikenal.")
    return model_id


def installed():
    gguf, _, _ = _paths()
    try:
        names = [n for n in os.listdir(gguf) if n.endswith(".gguf") and not n.startswith(".")]
    except FileNotFoundError:
        return {}
    return {n[:-5]: os.path.getsize(os.path.join(gguf, n)) for n in names if MODEL_ID.fullmatch(n[:-5])}


def partial(model_id):
    _, downloads, _ = _paths()
    path = os.path.join(downloads, f"{model_id}.gguf.part")
    return os.path.getsize(path) if os.path.exists(path) else 0


def active():
    _, _, path = _paths()
    try:
        model_id = open(path).read().strip()
    except FileNotFoundError:
        return None
    return model_id if model_id in installed() else None


async def router_models():
    """{id: {"status": unloaded|loading|loaded|failed, "progress": 0..1|None}}; None when the router is down."""
    if not ROUTER:
        return None
    try:
        async with httpx.AsyncClient(timeout=3, trust_env=False, follow_redirects=False) as client:
            response = await client.get(f"{ROUTER}/models")
        response.raise_for_status()
        result = {}
        for model in response.json().get("data", []):
            status = model.get("status") or {}
            progress = (status.get("progress") or {}).get("value")
            result[model["id"]] = {"status": status.get("value", "unknown"), "progress": progress}
        return result
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None


async def _router_post(path, model_id):
    async with httpx.AsyncClient(timeout=10, trust_env=False, follow_redirects=False) as client:
        response = await client.post(f"{ROUTER}{path}", json={"model": model_id})
    return response.status_code < 400


async def state():
    router = await router_models()
    files, current = installed(), active()
    disk = shutil.disk_usage(ROOT) if os.path.isdir(ROOT) else None
    models = []
    for entry in CATALOG + [{"id": i, "label": i, "repo": None, "size": s, "ram_gb": None, "note": "Disalin manual ke server."}
                            for i, s in files.items() if not _catalog(i)]:
        model_id = entry["id"]
        served = (router or {}).get(model_id)
        models.append({**{k: entry[k] for k in ("id", "label", "size", "ram_gb", "note")},
                       "source": "catalog" if entry["repo"] else "manual", "installed": model_id in files,
                       "partial_bytes": partial(model_id), "active": model_id == current,
                       "router_status": served["status"] if served else None,
                       "load_progress": served["progress"] if served else None})
    return {"models": models, "active": current, "router": "ready" if router is not None else ("unavailable" if ROUTER else "not_configured"),
            "download": dict(_download) or None,
            "disk": {"free": disk.free, "total": disk.total} if disk else None}


async def _hub_file(entry):
    """(size, sha256) of the catalog file according to the Hub, which the download is checked against."""
    async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=True) as client:
        response = await client.get(f"{HUB}/api/models/{entry['repo']}/tree/main")
    response.raise_for_status()
    for item in response.json():
        if item.get("path") == f"{entry['id']}.gguf" and item.get("lfs"):
            return int(item["lfs"]["size"]), item["lfs"]["oid"]
    raise ModelError("File model tidak ditemukan di Hugging Face.")


async def _fetch(entry, job):
    gguf, downloads, _ = _paths()
    os.makedirs(downloads, exist_ok=True)
    part = os.path.join(downloads, f"{entry['id']}.gguf.part")
    size, sha = await _hub_file(entry)
    have = os.path.getsize(part) if os.path.exists(part) else 0
    if have > size:
        os.remove(part)
        have = 0
    needed = size - have + SPARE_BYTES
    if shutil.disk_usage(ROOT).free < needed:
        raise ModelError(f"Ruang disk tidak cukup: butuh {needed / 1e9:.1f} GB kosong, tersedia "
                         f"{shutil.disk_usage(ROOT).free / 1e9:.1f} GB. Hapus model lain atau kosongkan disk server.")
    digest = hashlib.sha256()
    if have:
        job["phase"] = "Memeriksa bagian yang sudah terunduh"
        with open(part, "rb") as handle:
            for block in iter(lambda: handle.read(CHUNK), b""):
                digest.update(block)
                await asyncio.sleep(0)
    job.update(phase="Mengunduh", total=size, bytes=have)
    started, base = time.monotonic(), have
    headers = {"Range": f"bytes={have}-"} if have else {}
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=120), trust_env=False, follow_redirects=True) as client:
        async with client.stream("GET", f"{HUB}/{entry['repo']}/resolve/main/{entry['id']}.gguf", headers=headers) as response:
            if have and response.status_code != 206:
                raise ModelError("Server unduhan tidak mendukung lanjutan; hapus unduhan sebagian lalu mulai ulang.")
            response.raise_for_status()
            with open(part, "ab") as handle:
                async for block in response.aiter_bytes(CHUNK):
                    if _cancel.is_set():
                        job.update(status="paused", phase=None,
                                   message="Dijeda. Bagian yang sudah terunduh disimpan; klik Lanjutkan untuk meneruskan.")
                        return
                    handle.write(block)
                    digest.update(block)
                    job["bytes"] += len(block)
                    elapsed = time.monotonic() - started
                    if elapsed > 1:
                        speed = (job["bytes"] - base) / elapsed
                        job["speed"] = round(speed)
                        job["eta_seconds"] = round((size - job["bytes"]) / speed) if speed else None
    job.update(phase="Memverifikasi checksum SHA-256", eta_seconds=None)
    if job["bytes"] != size or digest.hexdigest() != sha:
        os.remove(part)
        raise ModelError("Checksum tidak cocok dengan Hugging Face; file dibuang. Coba unduh ulang.")
    os.makedirs(gguf, exist_ok=True)
    os.replace(part, os.path.join(gguf, f"{entry['id']}.gguf"))
    job.update(status="done", phase=None, finished_at=time.time(),
               message="Unduhan selesai dan checksum cocok. Server model memperbarui daftarnya otomatis dalam beberapa "
                       "detik; setelah itu klik Aktifkan.")


async def _run(entry, job):
    try:
        await _fetch(entry, job)
    except ModelError as error:
        job.update(status="failed", phase=None, message=str(error))
    except httpx.HTTPError as error:
        job.update(status="failed", phase=None,
                   message=f"Unduhan terputus ({type(error).__name__}). Bagian yang sudah terunduh disimpan; klik Lanjutkan. "
                           "Bila server tanpa akses internet, salin file .gguf ke volume kai_local-llm-models (folder gguf).")
    except OSError as error:
        job.update(status="failed", phase=None, message=f"File model tidak bisa ditulis ({error.strerror}).")


def start_download(model_id):
    global _task
    entry = _catalog(_valid(model_id))
    if not entry:
        raise ModelError("Hanya model dari daftar yang bisa diunduh.")
    if _download.get("status") == "running":
        raise ModelError("Unduhan lain sedang berjalan. Tunggu atau jeda dulu.")
    if model_id in installed():
        raise ModelError("Model ini sudah terpasang.")
    _cancel.clear()
    _download.clear()
    _download.update(model=model_id, label=entry["label"], status="running", phase="Menghubungi Hugging Face",
                     bytes=partial(model_id), total=entry["size"], speed=None, eta_seconds=None,
                     started_at=time.time(), finished_at=None, message=None)
    _task = asyncio.create_task(_run(entry, _download))


def pause_download():
    if _download.get("status") == "running":
        _cancel.set()
        _download["phase"] = "Menjeda"


async def activate(model_id):
    _valid(model_id)
    if model_id not in installed():
        raise ModelError("Model belum terpasang.")
    _, _, path = _paths()
    with open(path, "w") as handle:
        handle.write(model_id)
    router = await router_models()
    if router is None:
        raise ModelError("Model dipilih, tetapi server model tidak berjalan. Jalankan make llm-up di server.")
    if model_id not in router:
        return "Model dipilih. Server model sedang memperbarui daftarnya; model dimuat otomatis saat job pertama berjalan."
    await _router_post("/models/load", model_id)
    return "Model dipilih dan sedang dimuat ke memori."


async def delete(model_id, job_running=False):
    _valid(model_id)
    if job_running and model_id == active():
        raise ModelError("Model ini sedang dipakai job draf. Hentikan job dulu.")
    if _download.get("model") == model_id and _download.get("status") == "running":
        raise ModelError("Model ini sedang diunduh. Jeda unduhannya dulu.")
    gguf, downloads, path = _paths()
    removed = False
    if model_id in installed():
        if model_id in (await router_models() or {}):
            await _router_post("/models/unload", model_id)
        os.remove(os.path.join(gguf, f"{model_id}.gguf"))
        removed = True
    part = os.path.join(downloads, f"{model_id}.gguf.part")
    if os.path.exists(part):
        os.remove(part)
        removed = True
    if not removed:
        raise ModelError("Tidak ada file untuk dihapus.")
    if os.path.exists(path) and open(path).read().strip() == model_id:
        os.remove(path)
    if _download.get("model") == model_id:
        _download.clear()
    return "Model dihapus dan ruang disk dikembalikan."
