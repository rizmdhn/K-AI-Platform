import { useState } from 'react';
import { Badge } from '@clickhouse/click-ui';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { usageFn, type UsageSummary, type UsageTotals } from '@/server/kaiAccounts';
import { Notice, Select } from './AccountFields';
type Days = 1 | 7 | 30 | 90;
type Pricing = UsageSummary['pricing'];
const PERIODS: { value: Days; label: string }[] = [{ value: 1, label: '24 jam terakhir' }, { value: 7, label: '7 hari' }, { value: 30, label: '30 hari' }, { value: 90, label: '90 hari' }];
const compact = new Intl.NumberFormat('id-ID', { notation: 'compact', maximumFractionDigits: 1 });
const whole = new Intl.NumberFormat('id-ID');
const usd = (value: number) => `US$${value.toLocaleString('id-ID', { minimumFractionDigits: 2, maximumFractionDigits: value < 1 ? 4 : 2 })}`;
const idr = (value: number) => `Rp${Math.round(value).toLocaleString('id-ID')}`;
/** Rupiah first when a rate is saved, US$ otherwise; em dash until prices are set. */
function cost(value: number | null, pricing: Pricing) {
  if (value == null) return '—';
  return pricing.usd_to_idr ? `${idr(value * pricing.usd_to_idr)} (${usd(value)})` : usd(value);
}
/** Headline cost tile: Rupiah with US$ underneath, or US$ with a hint about what is missing. */
function costStat(total: number | null, pricing: Pricing): { value: string; hint: string } {
  if (total == null) return { value: '—', hint: 'Harga belum diatur' };
  if (!pricing.usd_to_idr) return { value: usd(total), hint: 'Kurs Rupiah belum diatur' };
  return { value: idr(total * pricing.usd_to_idr), hint: usd(total) };
}
/** Change vs the previous window of the same length, neutral ink: more usage is neither good nor bad. */
function delta(current: number, previous: number, period: string) {
  if (!previous) return current ? `Tidak ada data ${period} sebelumnya` : undefined;
  const change = (current - previous) / previous;
  if (Math.abs(change) < 0.005) return `Tidak berubah dibanding ${period} sebelumnya`;
  return `${change > 0 ? '▲' : '▼'} ${Math.abs(change * 100).toLocaleString('id-ID', { maximumFractionDigits: change > 9.99 ? 0 : 1 })}% dibanding ${period} sebelumnya`;
}
function Stat({ label, value, hint, change }: { label: string; value: string; hint?: string; change?: string }) {
  return <div className="flex flex-col gap-1 rounded-md border border-(--cui-color-stroke-default) p-3">
    <span className="text-xs text-(--cui-color-text-muted)">{label}</span>
    <span className="text-xl font-medium tabular-nums">{value}</span>
    {change && <span className="text-xs tabular-nums text-(--cui-color-text-default)">{change}</span>}
    {hint && <span className="text-xs text-(--cui-color-text-muted)">{hint}</span>}
  </div>;
}
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'Mei', 'Jun', 'Jul', 'Agu', 'Sep', 'Okt', 'Nov', 'Des'];
/** Bucket keys are already wall-clock time in the server zone ("2026-11-17" or "2026-11-17 08"): format without re-zoning. */
function bucketLabel(key: string, hourly: boolean, long = false) {
  const [date, hour] = key.split(' ');
  const [, month, day] = date.split('-').map(Number);
  const dayText = `${day} ${MONTHS[month - 1]}`;
  if (!hourly) return dayText;
  const next = String((Number(hour) + 1) % 24).padStart(2, '0');
  return long ? `${dayText}, ${hour}.00–${next}.00` : `${hour}.00`;
}
/** Categorical slots 1 and 2 (validated light #fcfcfb and dark #1a1a19 surfaces, CVD ΔE ≥ 24). */
const SERIES = [
  { key: 'prompt', label: 'Token masuk', fill: 'bg-[#2a78d6] dark:bg-[#3987e5]' },
  { key: 'completion', label: 'Token keluar', fill: 'bg-[#eb6834] dark:bg-[#d95926]' },
] as const;
type Bucket = UsageSummary['daily'][number];
function bucketCost(d: Bucket, pricing: Pricing) {
  if (!pricing.priced || pricing.input_usd_per_million == null || pricing.output_usd_per_million == null) return null;
  return (d.prompt * pricing.input_usd_per_million + d.completion * pricing.output_usd_per_million) / 1e6;
}
function Tooltip({ d, hourly, pricing, index, count }: { d: Bucket; hourly: boolean; pricing: Pricing; index: number; count: number }) {
  const left = ((index + 0.5) / count) * 100;
  let shift = '-50%';
  if (left < 15) shift = '0%';
  else if (left > 85) shift = '-100%';
  const value = bucketCost(d, pricing);
  return <div role="tooltip" className="pointer-events-none absolute bottom-full z-10 mb-2 flex min-w-44 flex-col gap-1 rounded-md border border-(--cui-color-stroke-default) bg-(--cui-color-background-panel) p-2.5 text-xs shadow-md"
    style={{ left: `${left}%`, transform: `translateX(${shift})` }}>
    <span className="text-(--cui-color-text-muted)">{bucketLabel(d.day, hourly, true)}</span>
    {SERIES.map(series => <span key={series.key} className="flex items-center gap-2">
      <span aria-hidden="true" className={`h-0.5 w-3 rounded-full ${series.fill}`} />
      <span className="text-(--cui-color-text-muted)">{series.label}</span>
      <b className="ml-auto font-medium tabular-nums text-(--cui-color-text-default)">{whole.format(d[series.key])}</b>
    </span>)}
    <span className="flex gap-2 border-t border-(--cui-color-stroke-default) pt-1">
      <span className="text-(--cui-color-text-muted)">Panggilan model</span><b className="ml-auto font-medium tabular-nums">{whole.format(d.calls)}</b>
    </span>
    {value != null && <span className="flex gap-2">
      <span className="text-(--cui-color-text-muted)">Estimasi biaya</span><b className="ml-auto font-medium tabular-nums">{cost(value, pricing)}</b>
    </span>}
  </div>;
}
function Trend({ daily, hourly, pricing }: { daily: Bucket[]; hourly: boolean; pricing: Pricing }) {
  const [active, setActive] = useState<number | null>(null);
  const max = Math.max(1, ...daily.map(d => d.prompt + d.completion));
  const middle = daily[Math.floor((daily.length - 1) / 2)];
  return <figure className="flex flex-col gap-2">
    <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
      <figcaption className="text-(--cui-color-text-muted)">Jumlah token per {hourly ? 'jam' : 'hari'}</figcaption>
      <div className="flex items-center gap-3">{SERIES.map(series => <span key={series.key} className="flex items-center gap-1.5 text-(--cui-color-text-default)">
        <span aria-hidden="true" className={`size-2.5 rounded-sm ${series.fill}`} />{series.label}
      </span>)}</div>
    </div>
    <div className="relative" onMouseLeave={() => setActive(null)}>
      <span className="absolute -top-0.5 left-0 text-xs tabular-nums text-(--cui-color-text-muted)">{compact.format(max)}</span>
      <div className="flex h-36 items-stretch gap-0.5 border-b border-(--cui-color-stroke-default) pt-4">
        {daily.map((d, i) => {
          const total = d.prompt + d.completion;
          return <button key={d.day} type="button"
            aria-label={`${bucketLabel(d.day, hourly, true)}: ${whole.format(d.prompt)} token masuk, ${whole.format(d.completion)} token keluar, ${whole.format(d.calls)} panggilan`}
            onMouseEnter={() => setActive(i)} onFocus={() => setActive(i)} onClick={() => setActive(i)} onBlur={() => setActive(null)}
            className={`flex min-w-0 flex-1 cursor-default flex-col justify-end gap-0.5 rounded-t-sm outline-offset-2 transition-colors ${active === i ? 'bg-(--cui-color-background-hover)' : ''}`}>
            {total > 0 && <span className="flex w-full flex-col gap-0.5" style={{ height: `${Math.max(3, (total / max) * 100)}%` }}>
              {d.completion > 0 && <span className={`w-full rounded-t-sm ${SERIES[1].fill}`} style={{ flexGrow: d.completion }} />}
              {d.prompt > 0 && <span className={`w-full ${d.completion > 0 ? '' : 'rounded-t-sm'} ${SERIES[0].fill}`} style={{ flexGrow: d.prompt }} />}
            </span>}
          </button>;
        })}
      </div>
      {active != null && <Tooltip d={daily[active]} hourly={hourly} pricing={pricing} index={active} count={daily.length} />}
    </div>
    {/* Three labels pinned to the edges and centre: per-bar slots are too narrow and the last label overflowed. */}
    <div className="flex justify-between gap-2 text-xs whitespace-nowrap text-(--cui-color-text-muted)" aria-hidden="true">
      <span>{bucketLabel(daily[0].day, hourly)}</span><span>{bucketLabel(middle.day, hourly)}</span><span>{bucketLabel(daily[daily.length - 1].day, hourly)}</span>
    </div>
  </figure>;
}
type Row = UsageTotals & { key: string; label: string; sub?: string };
/** Rupiah on top, US$ underneath (two short lines instead of one long one that overflows on phones). */
function CostCell({ value, pricing }: { value: number | null; pricing: Pricing }) {
  if (value == null) return <>—</>;
  if (!pricing.usd_to_idr) return <>{usd(value)}</>;
  return <><div>{idr(value * pricing.usd_to_idr)}</div><div className="text-xs text-(--cui-color-text-muted)">{usd(value)}</div></>;
}
function Rows({ title, rows, pricing }: { title: string; rows: Row[]; pricing: Pricing }) {
  return <div className="overflow-x-auto"><table className="w-full text-sm">
    <caption className="pb-1 text-left text-xs text-(--cui-color-text-muted)">{title}</caption>
    <thead><tr className="text-left text-xs text-(--cui-color-text-muted)">
      <th className="py-1 font-normal">Nama</th><th className="py-1 text-right font-normal">Masuk</th><th className="py-1 text-right font-normal">Keluar</th>
      <th className="hidden py-1 text-right font-normal sm:table-cell">Panggilan</th><th className="py-1 pl-3 text-right font-normal">Estimasi biaya</th>
    </tr></thead>
    <tbody>{rows.map(r => <tr key={r.key} className="border-t border-(--cui-color-stroke-default)">
      <td className="py-1.5 pr-3"><div className="wrap-break-word">{r.label}</div>{r.sub && <div className="text-xs break-all text-(--cui-color-text-muted)">{r.sub}</div>}</td>
      <td className="py-1.5 text-right tabular-nums">{compact.format(r.prompt)}</td>
      <td className="py-1.5 text-right tabular-nums">{compact.format(r.completion)}</td>
      <td className="hidden py-1.5 text-right tabular-nums sm:table-cell">{whole.format(r.calls)}</td>
      <td className="py-1.5 pl-3 text-right tabular-nums"><CostCell value={r.usd} pricing={pricing} /></td>
    </tr>)}</tbody>
  </table></div>;
}
export function UsagePanel() {
  const [days, setDays] = useState<Days>(30);
  const query = useQuery({ queryKey: ['kaiUsage', days], queryFn: () => usageFn({ data: { days } }), retry: false, placeholderData: keepPreviousData });
  const data = query.data;
  const total = data?.total.usd ?? null;
  const periodText = days === 1 ? '24 jam' : `${days} hari`;
  return <section aria-label="Pemakaian token" className="flex flex-col gap-4 rounded-lg border border-(--cui-color-stroke-default) p-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h2 className="font-medium">Pemakaian token {query.isPending && <Badge text="Memuat" state="neutral" />}</h2>
      <label className="flex items-center gap-2 text-sm"><span className="text-(--cui-color-text-muted)">Periode</span>
        <Select value={days} onChange={event => setDays(Number(event.target.value) as Days)}>
          {PERIODS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
        </Select>
      </label>
    </div>
    {query.isError && <Notice tone="error">Data pemakaian token gagal dimuat. Silakan coba kembali.</Notice>}
    {data && <div className={`flex flex-col gap-4 transition-opacity ${query.isPlaceholderData ? 'opacity-60' : ''}`}>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Token masuk (prompt)" value={compact.format(data.total.prompt)} change={delta(data.total.prompt, data.previous.prompt, periodText)} hint="Termasuk riwayat percakapan dan keluaran tool" />
        <Stat label="Token keluar (jawaban)" value={compact.format(data.total.completion)} change={delta(data.total.completion, data.previous.completion, periodText)} />
        <Stat label="Panggilan model" value={whole.format(data.total.calls)} change={delta(data.total.calls, data.previous.calls, periodText)} hint={`${whole.format(data.user_count)} pengguna`} />
        <Stat label="Estimasi biaya" {...costStat(total, data.pricing)}
          change={total != null && data.previous.usd != null ? delta(total, data.previous.usd, periodText) : undefined} />
      </div>
      {!data.pricing.priced && <Notice tone="info">Estimasi biaya akan ditampilkan setelah tarif per 1 juta token diisi pada menu <Link to="/integrations" className="underline">Integrasi → Harga model</Link>.</Notice>}
      <Trend daily={data.daily} hourly={data.bucket === 'hour'} pricing={data.pricing} />
      {data.users.length
        ? <Rows title={`Pengguna dengan pemakaian terbesar${data.user_count > data.users.length ? ` (${data.users.length} teratas)` : ''}`} pricing={data.pricing}
          rows={data.users.map(u => ({ ...u, key: u.id, label: u.name || u.email || 'Pengguna terhapus', sub: u.name ? u.email ?? undefined : undefined }))} />
        : <p className="text-sm text-(--cui-color-text-muted)">Tidak ada pemakaian pada periode ini.</p>}
      {data.models.length > 1 && <Rows title="Pemakaian per model" pricing={data.pricing} rows={data.models.map(m => ({ ...m, key: m.model, label: m.model }))} />}
      <p className="text-xs text-(--cui-color-text-muted)">Sumber data: catatan pemakaian token LibreChat. Estimasi biaya dihitung dari harga pada menu Integrasi dan bukan merupakan tagihan resmi penyedia model.</p>
    </div>}
  </section>;
}
