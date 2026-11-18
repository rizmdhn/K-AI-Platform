import { useQuery } from '@tanstack/react-query';
import { useCapabilities } from '@/hooks';
import { SystemCapabilities } from '@/constants';
import { semanticDatabasesFn, semanticListFn } from '@/server/kaiAccounts';
import { Notice } from '../users/AccountFields';
import { Browser } from './Browser';
import { DraftJob } from './DraftJob';
import { LocalModels } from './LocalModels';
import { Overview } from './Overview';
export type SemanticTab = 'overview' | 'review' | 'all' | 'ai';
export const SEMANTIC_TABS: SemanticTab[] = ['overview', 'review', 'all', 'ai'];
/** The semantic layer as its own feature: overview with coverage and a definition map, review queue, catalog, AI drafts. */
export function SemanticPage({ tab, onTab }: { tab: SemanticTab; onTab: (tab: SemanticTab) => void }) {
  const { hasCapability } = useCapabilities();
  const canManage = hasCapability(SystemCapabilities.MANAGE_CONFIGS);
  const query = useQuery({ queryKey: ['kaiSemantic'], queryFn: () => semanticListFn(), retry: false });
  const databases = useQuery({ queryKey: ['kaiSemanticDatabases'], queryFn: () => semanticDatabasesFn(), retry: false, enabled: canManage });
  const rows = query.data?.definitions ?? [];
  const drafts = rows.filter(r => r.status === 'draft').length;
  const labels: Record<SemanticTab, string> = { overview: 'Ringkasan', review: `Tinjau draf${drafts ? ` (${drafts})` : ''}`, all: 'Semua definisi', ai: 'Buat draf AI' };
  return <div className="flex flex-col gap-4 p-4 md:p-6">
    <header className="flex flex-col gap-1">
      <h1 className="text-xl font-medium">Semantic layer</h1>
      <p className="max-w-3xl text-sm text-(--cui-color-text-muted)">Definisi resmi metrik (mis. pendapatan bersih = sum(pendapatan_rp)) dan verified query (pertanyaan umum beserta SQL yang sudah diperiksa). Bila pertanyaan pengguna cocok, K-AI Analis Data memakai definisi yang disetujui, jadi angkanya konsisten dan jawabannya lebih cepat. Query tetap berjalan dengan hak akses ClickHouse masing-masing pengguna.</p>
    </header>
    <div role="tablist" aria-label="Bagian semantic layer" className="flex flex-wrap gap-1 border-b border-(--cui-color-stroke-default)">
      {SEMANTIC_TABS.map(t => <button key={t} type="button" role="tab" aria-selected={tab === t} onClick={() => onTab(t)}
        className={`-mb-px border-b-2 px-3 py-2 text-sm ${tab === t ? 'border-(--cui-color-accent-default) font-medium' : 'border-transparent text-(--cui-color-text-muted) hover:text-(--cui-color-text-default)'}`}>{labels[t]}</button>)}
    </div>
    {query.isError && <Notice tone="error">Semantic layer tidak bisa dibaca. Coba muat ulang halaman.</Notice>}
    {query.data && !query.data.storage_available && <Notice tone="warning">Penyimpanan semantic layer belum disiapkan di server (jalankan make migrate).</Notice>}
    {query.isPending && <p role="status" className="text-sm text-(--cui-color-text-muted)">Memuat…</p>}
    {query.data?.storage_available && <div role="tabpanel" aria-label={labels[tab]}>
      {tab === 'overview' && <Overview rows={rows} databases={databases.data?.databases ?? null} />}
      {tab === 'review' && <Browser key="review" rows={rows} mode="review" canManage={canManage} />}
      {tab === 'all' && <Browser key="all" rows={rows} mode="all" canManage={canManage} />}
      {tab === 'ai' && <div className="grid gap-4 xl:grid-cols-2"><LocalModels canManage={canManage} /><DraftJob canManage={canManage} onReview={() => onTab('review')} /></div>}
    </div>}
  </div>;
}
