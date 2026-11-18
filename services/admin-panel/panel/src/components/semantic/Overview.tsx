import { useState } from 'react';
import type { SemanticDatabase, SemanticDefinition, SemanticStatus } from '@/server/kaiAccounts';
import type { Tone } from './Editor';
import { Select } from '../users/AccountFields';
import { TONE, databaseOf, toneOf } from './Editor';
const whole = new Intl.NumberFormat('id-ID');

function Tile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return <div className="flex flex-col gap-1 rounded-md border border-(--cui-color-stroke-default) p-3">
    <span className="text-xs text-(--cui-color-text-muted)">{label}</span>
    <span className="text-2xl font-medium tabular-nums">{value}</span>
    {hint && <span className="text-xs text-(--cui-color-text-muted)">{hint}</span>}
  </div>;
}
function Legend({ tones }: { tones: Tone[] }) {
  return <div className="flex flex-wrap gap-3 text-xs">{tones.map(t =>
    <span key={t} className="flex items-center gap-1.5"><span aria-hidden="true" className={`size-2.5 rounded-full ${TONE[t].dot}`} />{TONE[t].label}</span>)}</div>;
}
interface Coverage { name: string; tables: number | null; covered: number; approved: number }
/** Tables with at least one definition, per database; totals come from the admin's own ClickHouse view when available. */
function Bars({ rows }: { rows: Coverage[] }) {
  return <figure className="flex flex-col gap-2">
    <figcaption className="text-sm font-medium">Cakupan per database</figcaption>
    <span className="flex flex-wrap gap-3 text-xs"><span className="flex items-center gap-1.5"><span aria-hidden="true" className={`size-2.5 rounded-full ${TONE.approved.dot}`} />Disetujui</span>
      <span className="flex items-center gap-1.5"><span aria-hidden="true" className="size-2.5 rounded-full bg-zinc-400" />Masih draf</span></span>
    <p className="text-xs text-(--cui-color-text-muted)">Tabel yang sudah punya definisi (disetujui atau draf) dibanding seluruh tabel yang bisa dibaca akun ClickHouse Anda.</p>
    <ul className="flex flex-col gap-2">{rows.map(r => <li key={r.name} className="grid grid-cols-[minmax(6rem,10rem)_1fr_auto] items-center gap-3 text-sm">
      <span className="truncate" title={r.name}>{r.name}</span>
      {r.tables == null
        ? <span className="text-xs text-(--cui-color-text-muted)">Jumlah tabel belum diketahui (butuh akses ClickHouse akun Anda)</span>
        : <span className="relative h-3 overflow-hidden rounded-full bg-(--cui-color-stroke-default)" role="img"
          aria-label={`${r.name}: ${r.covered} dari ${r.tables} tabel tercakup, ${r.approved} disetujui`}>
          <span className={`absolute inset-y-0 left-0 bg-zinc-400`} style={{ width: `${(r.covered / Math.max(r.tables, 1)) * 100}%` }} />
          <span className={`absolute inset-y-0 left-0 ${TONE.approved.dot}`} style={{ width: `${(r.approved / Math.max(r.tables, 1)) * 100}%` }} />
        </span>}
      <span className="tabular-nums text-(--cui-color-text-muted)">{whole.format(r.covered)}{r.tables == null ? ' tabel' : ` / ${whole.format(r.tables)}`}</span>
    </li>)}</ul>
  </figure>;
}
/** Per database: each table with its metrics, and a matrix of which tables every verified query reads. */
function DefinitionMap({ rows, databases }: { rows: SemanticDefinition[]; databases: string[] }) {
  const [database, setDatabase] = useState(databases[0] ?? '');
  const current = databases.includes(database) ? database : databases[0] ?? '';
  const inDb = rows.filter(r => r.tables.some(t => databaseOf(t) === current));
  const metrics = inDb.filter(r => r.kind === 'metric');
  const queries = inDb.filter(r => r.kind === 'verified_query');
  const tables = [...new Set(inDb.flatMap(r => r.tables))].sort();
  const byTable = new Map(tables.map(t => [t, metrics.filter(m => m.tables[0] === t)]));
  return <figure className="flex flex-col gap-3">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <figcaption className="text-sm font-medium">Peta definisi</figcaption>
      <label className="flex items-center gap-2 text-sm"><span className="text-(--cui-color-text-muted)">Database</span>
        <Select value={current} onChange={e => setDatabase(e.target.value)}>{databases.map(d => <option key={d} value={d}>{d}</option>)}</Select>
      </label>
    </div>
    <Legend tones={(['approved', 'draft_ai', 'draft_manual'] as Tone[]).filter(t => inDb.some(r => toneOf(r) === t))} />
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{tables.map(table => <section key={table} className="flex flex-col gap-2 rounded-md border border-(--cui-color-stroke-default) p-3">
      <h4 className="truncate font-mono text-sm" title={table}>{table.slice(current.length + 1)}</h4>
      {byTable.get(table)?.length
        ? <ul className="flex flex-col gap-1.5">{byTable.get(table)!.map(m => <li key={m.definition_id} className="flex items-start gap-2 text-sm">
          <span aria-hidden="true" className={`mt-1.5 size-2 shrink-0 rounded-full ${TONE[toneOf(m)].dot}`} />
          <span className="min-w-0"><span className="font-medium">{m.name}</span> <span className="sr-only">({TONE[toneOf(m)].label})</span>
            <span className="block truncate font-mono text-xs text-(--cui-color-text-muted)" title={m.definition.expression}>{m.definition.expression}{m.definition.unit ? `, ${m.definition.unit}` : ''}</span></span>
        </li>)}</ul>
        : <p className="text-xs text-(--cui-color-text-muted)">Belum ada metrik; dipakai oleh verified query.</p>}
    </section>)}</div>
    {queries.length > 0 && <div className="overflow-x-auto">
      <table className="text-sm">
        <caption className="pb-1 text-left text-xs text-(--cui-color-text-muted)">Verified query dan tabel yang dibacanya</caption>
        <thead><tr className="text-xs text-(--cui-color-text-muted)"><th className="py-1 pr-4 text-left font-normal">Verified query</th>
          {tables.map(t => <th key={t} className="px-2 py-1 font-mono font-normal" title={t}>{t.slice(current.length + 1)}</th>)}</tr></thead>
        <tbody>{queries.map(q => <tr key={q.definition_id} className="border-t border-(--cui-color-stroke-default)">
          <td className="py-1.5 pr-4"><span className="flex items-center gap-2"><span aria-hidden="true" className={`size-2 rounded-full ${TONE[toneOf(q)].dot}`} />
            <span title={q.definition.question}>{q.name}</span><span className="sr-only">({TONE[toneOf(q)].label})</span></span></td>
          {tables.map(t => <td key={t} className="px-2 text-center">{q.tables.includes(t) ? <span aria-label="dibaca">●</span> : <span className="text-(--cui-color-text-muted)" aria-hidden="true">·</span>}</td>)}
        </tr>)}</tbody>
      </table>
    </div>}
    {!tables.length && <p className="text-sm text-(--cui-color-text-muted)">Belum ada definisi untuk database ini.</p>}
  </figure>;
}
export function Overview({ rows, databases }: { rows: SemanticDefinition[]; databases: SemanticDatabase[] | null }) {
  const live = rows.filter(r => r.status !== 'rejected');
  const count = (status: SemanticStatus) => rows.filter(r => r.status === status).length;
  const counts = { approved: count('approved'), draft: count('draft'), rejected: count('rejected') };
  const coverage = new Map<string, Coverage>();
  for (const d of databases ?? []) coverage.set(d.name, { name: d.name, tables: d.tables, covered: 0, approved: 0 });
  const covered = new Map<string, Set<string>>(), approved = new Map<string, Set<string>>();
  for (const r of live) for (const t of r.tables) {
    const db = databaseOf(t);
    if (!coverage.has(db)) coverage.set(db, { name: db, tables: null, covered: 0, approved: 0 });
    (covered.get(db) ?? covered.set(db, new Set()).get(db)!).add(t);
    if (r.status === 'approved') (approved.get(db) ?? approved.set(db, new Set()).get(db)!).add(t);
  }
  const bars = [...coverage.values()].map(c => ({ ...c, covered: covered.get(c.name)?.size ?? 0, approved: approved.get(c.name)?.size ?? 0 }))
    .sort((a, b) => b.covered - a.covered || a.name.localeCompare(b.name));
  const totalTables = databases?.reduce((sum, d) => sum + d.tables, 0) ?? null;
  const coveredTables = bars.reduce((sum, b) => sum + b.covered, 0);
  const share = totalTables ? `${Math.round((coveredTables / totalTables) * 100)}%` : whole.format(coveredTables);
  return <div className="flex flex-col gap-6">
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      <Tile label="Disetujui" value={whole.format(counts.approved)} hint="Dipakai agent" />
      <Tile label="Draf menunggu" value={whole.format(counts.draft)} hint="Perlu ditinjau" />
      <Tile label="Ditolak" value={whole.format(counts.rejected)} hint="Tidak diusulkan ulang" />
      <Tile label="Tabel tercakup" value={share} hint={totalTables ? `${whole.format(coveredTables)} dari ${whole.format(totalTables)} tabel` : 'tabel dengan definisi'} />
    </div>
    {bars.length > 0 && <Bars rows={bars} />}
    {live.length > 0
      ? <DefinitionMap rows={live} databases={bars.filter(b => b.covered).map(b => b.name)} />
      : <p className="text-sm text-(--cui-color-text-muted)">Belum ada definisi. Buka tab Buat draf AI untuk membuat draf dari skema database, atau Semua definisi untuk menambah manual.</p>}
  </div>;
}
