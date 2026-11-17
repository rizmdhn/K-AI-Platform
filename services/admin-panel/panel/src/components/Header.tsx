import type * as t from '@/types';
import { useLocalize } from '@/hooks';

export function Header({ title, onSearchClick, onMenuClick, children }: t.HeaderProps) {
  const localize = useLocalize();
  const isMac =
    typeof navigator !== 'undefined' && /Mac|iPod|iPhone|iPad/.test(navigator.userAgent);
  const shortcut = isMac ? '⌘K' : 'Ctrl+K';

  return (
    <header className="shrink-0 border-b border-(--cui-color-stroke-default) bg-(--cui-color-background-panel)">
      <div className="flex items-center gap-3 px-4 py-3">
        {onMenuClick && (
          <button
            type="button"
            onClick={onMenuClick}
            aria-label={localize('com_nav_expand_sidebar')}
            className="-ml-1 flex size-9 shrink-0 cursor-pointer items-center justify-center rounded-md border-0 bg-transparent text-(--cui-color-text-default) hover:bg-(--cui-color-background-hover) md:hidden"
          >
            <svg aria-hidden="true" viewBox="0 0 20 20" className="size-5"><path d="M3 5h14M3 10h14M3 15h14" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
          </button>
        )}
        <div className="flex min-w-0 shrink flex-col">
          {title ? (
            <h1 className="truncate text-base font-bold text-(--cui-color-text-default)">{title}</h1>
          ) : (
            <div />
          )}
        </div>
        <div className="flex min-w-0 flex-1 items-center justify-end gap-2">
          {children}
          <div id="header-actions-portal" className="contents" />
          {onSearchClick && (
            <button
              type="button"
              onClick={onSearchClick}
              aria-label={localize('com_cmdk_label')}
              className="flex shrink-0 cursor-pointer items-center gap-2 rounded-lg border border-(--cui-color-stroke-default) bg-transparent px-3 py-1.5 text-sm text-(--cui-color-text-muted) transition-colors hover:border-(--cui-color-stroke-intense) hover:text-(--cui-color-text-default)"
            >
              <span>{localize('com_ui_search')}</span>
              <kbd className="hidden rounded border md:inline border-(--cui-color-stroke-default) bg-(--cui-color-background-secondary) px-1.5 py-0.5 text-xs font-medium text-(--cui-color-text-default)">
                {shortcut}
              </kbd>
            </button>
          )}
        </div>
      </div>
    </header>
  );
}
