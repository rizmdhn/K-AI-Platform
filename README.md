# kAI

Platform chat AI internal berbasis [LibreChat](https://github.com/LibreChat-AI/LibreChat) v0.8.8, dengan akses data ClickHouse per pengguna melalui gateway MCP read-only dan pencarian dokumen pribadi memakai embedding lokal.

## Fitur

- **Dua agent terkelola:** K-AI Asisten (chat umum dan dokumen pribadi) dan K-AI Analis Data (query ClickHouse). Pengguna tidak memilih model atau membuat agent.
- **Akses warehouse per pengguna:** setiap pengguna memakai akun ClickHouse-nya sendiri. Password disimpan terenkripsi (AES-256-GCM) dan dipilih di server, tidak pernah dari input model.
- **Read-only:** `readonly=1` di setiap query, validasi SQL (write, DDL, dan multi-statement ditolak), serta batas waktu, memori, dan hasil.
- **Fungsi AI atas data:** ringkasan, sentimen, klasifikasi, pencarian baris berdasarkan makna, draf `COMMENT` tabel untuk DBA, dan klasifikasi massal opsional.
- **Dokumen pribadi:** RAG lokal (pgvector dan bge-m3 di CPU). Dokumen hanya bisa diakses pemiliknya.
- **Admin Panel:** kelola pengguna, aktivasi akun, kredensial ClickHouse, akses agent, dan panel *Perlu perhatian*.
- **Operasional:** audit log harian, metrik privat, backup/restore, dan pencarian riwayat chat.

## Arsitektur

| Layanan | Peran |
|---|---|
| `api` | LibreChat (UI dan API), dengan modul platform di `services/platform-api` |
| `admin-panel` | Admin Panel LibreChat dengan overlay di `services/admin-panel` |
| `clickhouse-mcp` | Gateway MCP read-only ke ClickHouse (`services/clickhouse-mcp`) |
| `kai-metadata` | PostgreSQL untuk binding kredensial dan metadata |
| `mongodb`, `meilisearch` | Data aplikasi dan pencarian chat |
| `rag_api`, `vectordb`, `embeddings` | RAG dokumen dengan embedding lokal (profile `documents`) |
| `rustfs` | Penyimpanan objek untuk file dokumen |

Hanya `api` (`127.0.0.1:3080`) dan `admin-panel` (`127.0.0.1:3081`) yang membuka port ke host. Layanan lain berada di jaringan internal Docker.

## Prasyarat

- Docker dengan Compose **2.24.4+**
- Python **3.10+** dan Make
- Node.js **22+** (hanya untuk tes)

## Mulai cepat

```sh
cp -n .env.example .env
make setup          # membuat secret yang masih kosong, .env mode 0600
make preflight      # cek prasyarat dan konfigurasi
make admin-up       # build dan jalankan api + admin panel
make admin-create   # buat akun administrator pertama
make agents-setup   # siapkan kedua agent terkelola
```

- Chat: http://localhost:3080
- Admin Panel: http://localhost:3081

Untuk warehouse, isi `KAI_MCP_ENABLED=true` dan `CLICKHOUSE_HOST` di `.env`, lalu jalankan `make setup` dan `make mcp-up`. Untuk dokumen, jalankan `make documents-up`.

## Konfigurasi

Seluruh konfigurasi ada di satu file, `.env`. Lihat `.env.example` untuk daftar lengkap beserta penjelasannya.

| Variabel | Fungsi |
|---|---|
| `OLLAMA_API_KEY` | Key provider model default. Bisa juga diatur dari Admin Panel |
| `KAI_GENERAL_AGENT_MODEL`, `KAI_CH_AGENT_MODEL` | Model awal kedua agent. Daftar model di Admin Panel bisa meng-override-nya |
| `KAI_MCP_ENABLED`, `CLICKHOUSE_HOST`, `CLICKHOUSE_PORT` | Target ClickHouse (wajib HTTPS) |
| `CLICKHOUSE_CA_CERT` | CA privat ClickHouse (opsional) |
| `KAI_BATCH_ENABLED` | Klasifikasi massal. Default `false` |
| `SEARCH` | Pencarian riwayat chat. Default `true` |
| `KAI_LOG_RETENTION_DAYS` | Retensi audit log. Default 90 hari |

Secret (`CREDS_KEY`, `JWT_*`, token MCP) dibuat oleh `make setup`. Simpan salinan `.env` dan keyring binding di tempat aman. Tanpa keduanya, kredensial tersimpan tidak bisa dibuka setelah restore.

## Perintah

| Perintah | Fungsi |
|---|---|
| `make up` / `make down` | Jalankan / hentikan stack (volume tetap ada) |
| `make ps`, `make logs` | Status dan log container |
| `make check` | Validasi konfigurasi Compose tanpa mencetak secret |
| `make agents-check` | Tampilkan provider, model, dan tool kedua agent |
| `make backup` | Backup Mongo, PostgreSQL, uploads, dokumen, dan audit log |
| `make metrics-up`, `make metrics` | Aktifkan dan baca metrik privat MCP |

## Tes

```sh
make test                     # tes Python dan Node
make mcp-test                 # tes gateway MCP di dalam image, tanpa jaringan
make browser-warehouse-test   # siklus kredensial di browser, ClickHouse lokal TLS
make evals                    # validasi set evaluasi sintetis
```

Tes tidak memanggil provider model maupun ClickHouse eksternal. Tes browser memerlukan Playwright (`NODE_PATH`, `PLAYWRIGHT_BROWSERS_PATH`). Script load test ada di `tests/load/`.

## Struktur

```
deploy/      Compose, librechat.yaml, branding, proxy TLS, Ansible
services/    platform-api, admin-panel, clickhouse-mcp, metadata-worker
db/          migrasi dan grant PostgreSQL
scripts/     setup, preflight, backup, rilis
tests/       tes unit, smoke, browser, dan load
evals/       set evaluasi sintetis
```

## Upstream dan lisensi

- `deploy/upstream/docker-compose.yml` adalah salinan Compose LibreChat v0.8.8 tanpa perubahan. Perubahan kAI ada di `deploy/docker-compose.override.yml`. Commit dan checksum sumbernya tercatat di `deploy/upstream/lock.json`.
- Image upstream dikunci dengan digest.
- LibreChat berlisensi MIT (`deploy/upstream/LICENSE`). Admin Panel adalah turunan dari [librechat-admin-panel](https://github.com/ClickHouse/librechat-admin-panel); kode sumber dan lisensi upstream-nya ikut disertakan di dalam image.
