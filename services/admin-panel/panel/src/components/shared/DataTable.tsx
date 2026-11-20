import type * as t from '@/types';
import { Pagination } from './Pagination';
import { cn } from '@/utils';
const whole = new Intl.NumberFormat('id-ID');
const SIZES = [10, 25, 50, 100] as const;
const ARROW = { asc: '↑', desc: '↓' } as const;
const ARIA_SORT = { asc: 'ascending', desc: 'descending' } as const;
/**
 * Server-paged table: the caller fetches one page (page, size, sort) and passes `total` for every match.
 * Optional row selection (checkbox column, select-all for the visible page) and row click for a detail view.
 */
export function DataTable<T>({
  caption, columns, rows, rowKey, total, page, size, sizes = SIZES, onPage, onSize, sort, dir = 'asc', onSort,
  selected, onSelect, onRowClick, activeKey, loading, empty,
}: t.DataTableProps<T>) {
  const pages = Math.max(1, Math.ceil(total / size));
  const keys = rows.map(rowKey);
  const allPicked = !!selected && keys.length > 0 && keys.every(k => selected.has(k));
  const toggle = (key: string) => {
    if (!selected || !onSelect) return;
    const next = new Set(selected);
    if (next.has(key)) next.delete(key); else next.add(key);
    onSelect(next);
  };
  const toggleAll = () => {
    if (!selected || !onSelect) return;
    const next = new Set(selected);
    for (const key of keys) if (allPicked) next.delete(key); else next.add(key);
    onSelect(next);
  };
  const header = (column: t.DataTableColumn<T>) => {
    if (!column.sortKey || !onSort) return column.label;
    const active = sort === column.sortKey;
    return <button type="button" className="inline-flex items-center gap-1 font-medium hover:text-(--cui-color-text-default)"
      onClick={() => onSort(column.sortKey!, active && dir === 'asc' ? 'desc' : 'asc')}>
      {column.label}<span aria-hidden="true" className={active ? '' : 'opacity-30'}>{active ? ARROW[dir] : '↕'}</span>
    </button>;
  };
  const first = total ? (page - 1) * size + 1 : 0;
  return <div className="flex flex-col gap-2">
    {/* Rows scroll inside the box (header stays), so a 100-row page never pushes the pager off screen. */}
    <div className={cn('max-h-[65vh] overflow-auto rounded-md border border-(--cui-color-stroke-default) transition-opacity', loading && 'opacity-60')}>
      <table className="w-full text-left text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead className="sticky top-0 z-10 bg-(--cui-color-background-muted) text-xs text-(--cui-color-text-muted)"><tr>
          {selected && <th className="w-8 px-3 py-2"><input type="checkbox" aria-label="Pilih semua di halaman ini" checked={allPicked} onChange={toggleAll} disabled={!rows.length} /></th>}
          {columns.map(c => <th key={c.key} scope="col" aria-sort={sort === c.sortKey ? ARIA_SORT[dir] : undefined}
            className={cn('whitespace-nowrap px-3 py-2 font-medium', c.align === 'right' && 'text-right', c.className)}>{header(c)}</th>)}
        </tr></thead>
        <tbody>
          {!rows.length && <tr><td colSpan={columns.length + (selected ? 1 : 0)} className="px-3 py-6 text-center text-(--cui-color-text-muted)">{loading ? 'Memuat…' : empty}</td></tr>}
          {rows.map(row => {
            const key = rowKey(row);
            return <tr key={key} onClick={onRowClick ? () => onRowClick(row) : undefined}
              className={cn('border-t border-(--cui-color-stroke-default) align-top', onRowClick && 'cursor-pointer hover:bg-(--cui-color-background-muted)',
                activeKey === key && 'bg-(--cui-color-background-muted)')}>
              {selected && <td className="px-3 py-2" onClick={e => e.stopPropagation()}>
                <input type="checkbox" aria-label="Pilih baris" checked={selected.has(key)} onChange={() => toggle(key)} /></td>}
              {columns.map(c => <td key={c.key} className={cn('px-3 py-2', c.align === 'right' && 'text-right tabular-nums', c.className)}>{c.render(row)}</td>)}
            </tr>;
          })}
        </tbody>
      </table>
    </div>
    <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-(--cui-color-text-muted)">
      <label className="flex items-center gap-2">Baris per halaman
        <select className="rounded-md border border-(--cui-color-stroke-default) bg-(--cui-color-background-default) px-2 py-1 text-(--cui-color-text-default)"
          value={size} onChange={e => onSize(Number(e.target.value))}>{sizes.map(s => <option key={s} value={s}>{s}</option>)}</select>
      </label>
      <span className="tabular-nums" aria-live="polite">{whole.format(first)}–{whole.format(Math.min(total, page * size))} dari {whole.format(total)}{selected?.size ? `, ${whole.format(selected.size)} dipilih` : ''}</span>
      <Pagination currentPage={Math.min(page, pages)} totalPages={pages} onPageChange={onPage} />
    </div>
  </div>;
}
