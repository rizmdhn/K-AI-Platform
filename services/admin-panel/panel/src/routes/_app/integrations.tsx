import { createFileRoute } from '@tanstack/react-router';
import { AccessDenied, PermissionsUnavailable } from '@/components/shared';
import { IntegrationsPanel } from '@/components/users/IntegrationsPanel';
import { SettingsSection } from '@/components/users/SettingsSection';
import { SystemCapabilities } from '@/constants';
import { useCapabilities } from '@/hooks';
export const Route = createFileRoute('/_app/integrations')({ component: IntegrationsRoute });
function IntegrationsRoute() {
  const { hasCapability, isLoading, isError } = useCapabilities();
  if (isLoading) return null;
  if (isError) return <PermissionsUnavailable />;
  if (!hasCapability(SystemCapabilities.READ_CONFIGS)) return <AccessDenied />;
  return <div className="flex flex-col gap-4 p-6">
    <h1 className="text-xl font-medium">Integrasi</h1>
    <IntegrationsPanel defaultOpen />
    <SettingsSection name="batch_model" title="Model AI untuk tugas batch"
      description={<div className="flex flex-col gap-2">
        <p>Model ini mengerjakan tugas otomatis yang memproses banyak data sekaligus. Model ini tidak dipakai untuk chat: asisten chat memakai modelnya sendiri.</p>
        <ul className="list-disc pl-5">
          <li><b>Pelabelan baris massal:</b> pengguna meminta asisten mengelompokkan ribuan baris dari ClickHouse, mis. &ldquo;kelompokkan keluhan pelanggan bulan ini menurut sentimen&rdquo;. Teks setiap baris dikirim ke model ini untuk diberi label, lalu hasilnya bisa diunduh.</li>
          <li><b>Draf deskripsi metadata:</b> worker menulis usulan deskripsi untuk tabel dan kolom yang masih kosong di OpenMetadata, untuk disetujui DBA (bagian &ldquo;Integrasi OpenMetadata&rdquo; di atas).</li>
        </ul>
        <p>Model ini dipisah dari chat karena volumenya besar: bisa memakai model yang lebih murah atau cepat, dan bisa dimatikan tanpa mengganggu chat.</p>
        <p><b>Data yang dikirim ke alamat model ini:</b> teks baris yang dilabeli, serta nama, tipe, dan deskripsi tabel/kolom. Pakai model yang disetujui pemilik data, sebaiknya yang di-host sendiri.</p>
      </div>}
      fields={[
        { key: 'enabled', label: 'Izinkan pengguna menjalankan pelabelan baris massal', type: 'checkbox', help: 'Draf metadata diaktifkan terpisah di bagian OpenMetadata, tetapi tetap memakai model ini.' },
        { key: 'base_url', label: 'Alamat API model', type: 'text', placeholder: 'https://ollama.com/v1', help: 'API yang kompatibel dengan OpenAI (mis. Ollama atau vLLM).' },
        { key: 'model', label: 'Nama model', type: 'text', placeholder: 'gemma3:27b' },
        { key: 'api_key', label: 'API key', type: 'secret', placeholder: 'Disimpan terenkripsi', help: 'Tidak ditampilkan lagi setelah disimpan.' },
        { key: 'jobs_per_day', label: 'Job pelabelan per pengguna per hari', type: 'number', min: 1, max: 1000 },
        { key: 'sample_rows', label: 'Ukuran sampel (baris)', type: 'number', min: 100, max: 5000, help: 'Di atas 5.000 baris, yang dilabeli adalah sampel acak sebanyak ini; hasilnya berupa perkiraan dengan margin kesalahan.' },
      ]} />
    <SettingsSection name="limits" title="Batas query dan log"
      description="Batas atas setiap query ClickHouse dari kAI. Profil pengguna di ClickHouse (DBA) tetap berlaku bila lebih ketat atau dikunci."
      fields={[
        { key: 'max_execution_seconds', label: 'Waktu query (detik)', type: 'number', min: 1, max: 29 },
        { key: 'max_memory_gb', label: 'Memori per query (GB)', type: 'number', min: 1, max: 1024 },
        { key: 'max_rows_million', label: 'Baris dibaca (juta)', type: 'number', min: 1, max: 1000000 },
        { key: 'max_read_gb', label: 'Data dibaca (GB)', type: 'number', min: 1, max: 100000 },
        { key: 'log_retention_days', label: 'Simpan log audit (hari)', type: 'number', min: 1, max: 3650 },
      ]} />
  </div>;
}
