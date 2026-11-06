# kAI

Platform AI internal perusahaan berbasis LibreChat. PRD dan TDD di folder eksternal [Docs/](../Docs/) menjelaskan produk secara lengkap; tahap pengembangan awal ini menyiapkan infrastruktur chat dengan penyimpanan persisten.

## Menjalankan secara lokal

Memerlukan Docker dengan Compose **2.24.4+**, Python **3.9+**, dan Make; pengujian unit lokal juga memakai Node.js **22+**. Docker Desktop harus berjalan. Jalankan perintah berikut dari `App/`.

```sh
cp -n .env.example .env
make setup
# Isi OLLAMA_API_KEY di .env melalui editor, lalu:
make admin-up
```

Buka **http://localhost:3080** dan daftarkan akun pengembangan. Akun pertama menjadi administrator LibreChat. Pendaftaran diaktifkan hanya pada lingkungan pengembangan yang terikat ke loopback ini; IAM produksi masih masuk D01.

Untuk chat, isi `OLLAMA_API_KEY` di `.env`, lalu jalankan kembali `make up` untuk membuat ulang API dengan variabel lingkungan baru. Setelah instalasi, kunci ini juga bisa diganti dari Admin Panel tanpa restart (lihat Docs/runbooks/admin-panel.md). Kandidat modelnya `gpt-oss:120b`, ditetapkan terpusat melalui anchor YAML `kai_model` yang dijelaskan di bawah. Setup dan tes bootstrap tidak mengirim permintaan ke model. Pemanggilan tool masih memerlukan pembuktian integrasi S0.

```sh
make check  # Validate merged Compose without printing secrets
make test   # Bootstrap and effective-deployment tests; no provider calls
make ps
make logs
make down   # Stop/remove this project's containers; named volumes remain
```

Jalankan `make admin-up` untuk mengaktifkan Admin Panel bawaan di **http://localhost:3081**. Login menggunakan akun administrator LibreChat yang sudah ada. Menu native **Users** membuat akun platform dan memberikan tautan aktivasi password sekali pakai. Referensi service/username ClickHouse bersifat opsional untuk mencatat akun yang dibuat manual; referensi ini tidak mengaktifkan akses warehouse. Role dan permission platform dapat dikelola di panel; permission warehouse tetap di ClickHouse. Lihat [pengaturan admin](../Docs/runbooks/admin-panel.md).

File utama `.env` dan contoh `.env.example` berada di root repo. Setup melengkapi secret yang kosong dengan nilai acak dan melindungi `.env` dengan mode 0600. Nilai yang sudah diisi dipertahankan, termasuk key model. Instalasi lama dimigrasikan dari lokasi sebelumnya tanpa mengganti key; jika kedua file berbeda, setup berhenti agar konflik diselesaikan secara privat. Simpan salinannya di tempat terlindungi: perubahan key enkripsi bisa membuat kredensial tersimpan tidak terbaca. Jangan jalankan `docker compose down -v` kecuali memang ingin menghapus data aplikasi.

Logo teks login, judul tab, dan ikon instalasi sekarang menggunakan identitas kAI untuk pengembangan. Browser yang sudah pernah membuka aplikasi mungkin memerlukan hard refresh agar logo login diperbarui. Lihat [pengaturan branding](../Docs/runbooks/client-branding.md).

## Layanan yang berjalan

- API/UI LibreChat dengan branding kAI, diturunkan dari digest image v0.8.8 yang dikunci.
- MongoDB dengan named volume persisten untuk akun dan percakapan.
- Meilisearch dengan named volume persisten untuk pencarian percakapan.
- Hanya aplikasi web yang membuka port host, pada `127.0.0.1:3080`.

[Compose upstream](deploy/upstream/docker-compose.yml) dipertahankan persis tanpa perubahan byte. Perubahan proyek berada di [override](deploy/docker-compose.override.yml). [Lock upstream](deploy/upstream/lock.json) mencatat commit sumber dan checksum yang tepat. Image layanan upstream, termasuk panel bawaan, dikunci berdasarkan digest. Image turunan branding API kAI dan image MCP opsional adalah build pengembangan lokal; promosi ke produksi masih menunggu. Upstream menandai v0.8.8 sebagai prarilis; versi ini menjadi kandidat pengembangan dan menunggu persetujuan produksi D07.

RAG/pgvector dan admin panel menggunakan profile Compose opsional. Pengambilan dokumen masih memerlukan penyedia embedding yang disetujui, ACL file yang terverifikasi, serta integrasi RAG yang diuji. User biasa mendapat asisten default **kAI** dengan Artifacts, tanpa memilih model atau membuat agent. Kontrol model, parameter, dan preset disembunyikan; model yang dikonfigurasi diwajibkan oleh server. Key penyedia tetap berada di server.

`make up`, `make admin-up`, dan `make mcp-up` menerapkan pengaturan dasar role native USER dan me-restart API untuk membersihkan cache permission. USER bisa chat tetapi tidak bisa membuat/membagikan agent, mengatur/menggunakan MCP, atau memulai percakapan paralel. ADMIN dan role kustom tetap dipertahankan. `make managed-chat` menerapkan ulang pengaturan dasar ini pada stack yang sudah berjalan; perintah tersebut memang mereset permission fitur USER yang disebutkan tadi. Perintah Compose langsung melewati langkah itu. Gunakan Admin Panel bawaan untuk pengelolaan role selanjutnya; penyediaan role bisnis dan identitas warehouse masih menjadi TEL-149. Lihat [pengaturan dan batasan chat terkelola](../Docs/runbooks/managed-chat.md).

Model default tetap `gpt-oss:120b`, sekarang didefinisikan satu kali pada anchor YAML `kai_model` di `deploy/librechat.yaml`. Pengaturan runtime lama `OLLAMA_MODEL` tidak lagi memilihnya: pemroses spesifikasi model LibreChat ini tidak melakukan interpolasi variabel lingkungan. Ubah spesifikasi/katalog terpusat melalui konfigurasi admin yang didukung, atau ubah anchor YAML tersebut lalu restart. Key Ollama tetap berada di lingkungan terlindungi.

**Pekerjaan identitas CH ditahan:** user akan menentukan penggunaan akun pribadi atau service account per role nanti. Jangan aktifkan kredensial warehouse atau menganggap implementasi akun individu saat ini sebagai pilihan akhir.

Backend di `services/clickhouse-mcp/` memakai server resmi yang dikunci versinya, dengan penggantian kredensial pribadi, validasi SQL, pembatasan hasil, pemeriksaan skema tabel, endpoint kesehatan, dan error aman yang bisa dikorelasikan. Tes integrasinya memakai instance ClickHouse sementara. Jika `KAI_MCP_ENABLED=true` pada `.env` root, Make menyertakan overlay MCP privat opsional; `make mcp-up` membangun/menjalankannya dan memuat token transport ke API. Chat default terkelola tidak memasang tool warehouse. Mode managed sekarang mencari binding pribadi yang disetujui di server dan mengabaikan header kredensial CH; field kredensial untuk user akhir sudah dihapus. Prasyarat penyimpanan binding/key/provisioning belum dikonfigurasi lokal, sehingga pemanggilan warehouse ditolak dengan aman. Mode manual eksplisit dibatasi untuk fixture terisolasi. Lihat [binding terkelola](../Docs/runbooks/managed-bindings.md). Pembatasan UI/fitur bukan batas otorisasi warehouse. Endpoint Cloud yang diberikan sebelumnya menutup TLS sebelum login; pemeriksaan lanjutan masih ditunda. Query dan chart Cloud dengan akun pribadi belum terverifikasi. [Backend persetujuan akun](../Docs/runbooks/account-approvals.md) privat sekarang memverifikasi autentikasi administrator native dan otoritas warehouse yang ditunjuk secara terpisah. Penyimpanan metadatanya sengaja belum dikonfigurasi. Form persetujuan tambahan sudah dihapus atas permintaan user; pendaftaran kredensial, penyediaan identitas, dan worker metadata belum selesai.

Gunakan `execute_readonly_sql` dengan `sql` dan `purpose` singkat untuk query data. Permintaan MCP memaksa `readonly=1`; konfigurasi yang mengizinkan write/drop ditolak saat startup, dan validasi SQL menolak write/DDL. `run_query` lama tetap mengikuti penerapan read-only yang sama. Lihat [kontrak tool](../Docs/runbooks/execute-readonly-sql.md).

Seluruh konfigurasi aplikasi, MCP dan metrics sekarang berada dalam `.env` root. File MCP/metrics lama dimigrasikan oleh `make setup`; field yang konflik tidak ditimpa. Instalasi baru default tanpa MCP. Untuk penghitung operasional privat setelah MCP aktif, jalankan `make metrics-up` satu kali, lalu `make metrics`. Token scrape terpisah tetap di server; tidak ada port metrics publik yang ditambahkan. Docker stdout/stderr sekarang dirotasi pada 10m × 3 file per layanan; log file aplikasi memiliki kebijakan terpisah. Collector eksternal dan retensi produksi masih menunggu. Lihat [metrics dan log](../Docs/runbooks/metrics-logs.md).

Jalankan `make evals` pada checkout controller ini untuk memvalidasi 21 kasus evaluasi SQL/metadata/dokumen sintetis tanpa permintaan ke model atau warehouse. Ini memeriksa fixture, bukan kualitas model atau otorisasi produksi. Lihat [panduan evaluasi](../Docs/runbooks/evaluation-sets.md).

Jalankan `make check test` untuk pemeriksaan deployment dan `make mcp-test` dari `App/` (image MCP lokal yang sudah dibangun, Python 3.12, jaringan dinonaktifkan; jangan memakai venv lama Python 3.10) untuk pemeriksaan backend. Tes integrasi opsional memerlukan `MCP_E2E_URL`. Chat sungguhan, rendering chart, dan pemeriksaan kepemilikan percakapan masih memerlukan akun pengembangan dan key penyedia. Peringatan startup LibreChat tentang RAG yang tidak terjangkau memang diperkirakan selama integrasi itu dinonaktifkan.

Uji instalasi bersih opsional: `make smoke-config` memakai paket sumber publik dan fixture Docker unik, tanpa key model/CH nyata. Ini memerlukan image lokal yang sudah dibangun dan membersihkan resource fixture setelah selesai.

Fondasi antrean worker metadata tersedia sebagai modul internal, belum sebagai layanan aktif. `make worker-test` menjalankan tes lease/retry/fencing pada PostgreSQL terisolasi. Lihat [panduan worker](../Docs/runbooks/metadata-worker.md); ekstraksi/model/search/ACL tetap belum aktif.

## Konteks proyek

[Panduan operasional](../Docs/runbooks/operations.md) menautkan deployment, upgrade/rollback, penanganan insiden, dan kesiapan rotasi key. Penerimaan pemulihan nyata/rotasi produksi masih menunggu.

- [PRD v0.2](../Docs/PRD_Internal_Enterprise_AI_Platform_v0.2.md)
- [TDD v0.1](../Docs/TDD_Internal_Enterprise_AI_Platform_v0.1.md)
- [MOP deployment ke server](../Docs/runbooks/mop-server-deployment.md)
- [Prosedur rilis/deployment pengembangan](../Docs/runbooks/application-deploy.md)
- [Prosedur upgrade upstream](../Docs/runbooks/upstream-upgrades.md)
- [Kemampuan agent dan status koneksi](../Docs/runbooks/agent-capabilities.md)
- [Pengaturan MCP privat](../Docs/runbooks/mcp-connection.md)
- [Kesehatan dan error MCP](../Docs/runbooks/mcp-health-errors.md)
- [Penyimpanan binding warehouse pribadi](../Docs/runbooks/binding-storage.md)
- [Proyek](.planning/PROJECT.md), [kebutuhan](.planning/REQUIREMENTS.md), [roadmap](.planning/ROADMAP.md), [status saat ini](.planning/STATE.md)
- Kan: **Telusur / kAI**, board `w9uhzpo5vdmx`. S0-01 dan S0-02 melacak bootstrap ini.

Dokumen produk dan rencana bootstrap berada di luar repositori ini, di `../Docs/`. Catatan lokal `.planning/` diabaikan Git dan bukan bagian dari repositori aplikasi.

## Sebelum produksi

Selesaikan D01–D11 dalam TDD dan buktikan S0 menggunakan dua user ClickHouse yang mewakili kebutuhan. Produksi memerlukan identitas per user yang tepercaya, pemetaan akun yang disetujui, penerapan read-only, kredensial terenkripsi, ACL pengambilan dokumen, TLS, provisioning, backup/restore, dan rangkaian tes keamanan. Startup lokal tidak memenuhi syarat-syarat tersebut.

Sumber upstream: [LibreChat v0.8.8](https://github.com/LibreChat-AI/LibreChat/tree/v0.8.8), [pengaturan Docker](https://www.librechat.ai/docs/local/docker), [Ollama Cloud](https://docs.ollama.com/cloud). Lisensi MIT LibreChat disertakan di `deploy/upstream/LICENSE`.
