import type { AdminUserSearchResult } from '@librechat/data-schemas';
import type { ReactNode, RefObject } from 'react';
import type { MenuIconName } from './scope';

export interface ReorderableListHandle {
  discard: () => void;
  save: () => void;
}

export interface ReorderableListProps<T extends { id: string }> {
  items: T[];
  isLoading: boolean;
  filterFn: (item: T, query: string) => boolean;
  renderItemContent: (item: T) => ReactNode;
  searchPlaceholder: string;
  emptyMessage: string;
  reorderHint: string;
  onSaveOrder: (orderedIds: string[]) => void;
  onDirtyChange?: (isDirty: boolean) => void;
  actionRef?: RefObject<ReorderableListHandle | null>;
  headerAction: ReactNode;
  children?: ReactNode;
  reorderDisabled?: boolean;
}

export interface KebabMenuItem {
  label: string;
  icon?: MenuIconName;
  danger?: boolean;
  onClick: () => void;
}

export interface KebabMenuProps {
  items: KebabMenuItem[];
  ariaLabel?: string;
}

export interface AvatarProps {
  name: string;
  size?: 'sm' | 'md';
  className?: string;
}

export interface EditButtonProps {
  onClick: () => void;
  ariaLabel: string;
  size?: 'xs' | 'sm';
  disabled?: boolean;
}

export interface EmptyStateProps {
  message: string;
  className?: string;
}

export interface FormDialogProps {
  open: boolean;
  title: string;
  submitLabel: string;
  submitDisabled?: boolean;
  saving?: boolean;
  error?: string;
  size?: 'sm' | 'lg';
  onSubmit: () => void;
  onClose: () => void;
  children: ReactNode;
}

export interface LoadingStateProps {
  className?: string;
}

export interface ScreenReaderAnnouncerProps {
  message: string;
}

export interface SearchInputProps {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  className?: string;
  ariaLabel?: string;
}

export interface SelectedMemberListProps {
  users: AdminUserSearchResult[];
  onRemove: (userId: string) => void;
  disabled?: boolean;
}

export interface StickyActionBarProps {
  discardLabel: string;
  saveLabel: string;
  onDiscard: () => void;
  onSave: () => void;
  message?: string;
}

export interface StatusToggleProps {
  id: string;
  isActive: boolean;
  onChange: (isActive: boolean) => void;
  disabled?: boolean;
}

export interface TrashButtonProps {
  onClick: () => void;
  ariaLabel: string;
  size?: 'xs' | 'sm';
  disabled?: boolean;
}

export interface PaginationProps {
  currentPage: number;
  totalPages: number;
  onPageChange: (page: number) => void;
}

/** One column of a server-paged DataTable; `sortKey` makes the header clickable. */
export interface DataTableColumn<T> {
  key: string;
  label: string;
  render: (row: T) => ReactNode;
  sortKey?: string;
  align?: 'left' | 'right';
  className?: string;
}

export interface DataTableProps<T> {
  caption: string;
  columns: DataTableColumn<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  total: number;
  page: number;
  size: number;
  sizes?: readonly number[];
  onPage: (page: number) => void;
  onSize: (size: number) => void;
  sort?: string;
  dir?: 'asc' | 'desc';
  onSort?: (sort: string, dir: 'asc' | 'desc') => void;
  selected?: ReadonlySet<string>;
  onSelect?: (next: Set<string>) => void;
  onRowClick?: (row: T) => void;
  activeKey?: string | null;
  loading?: boolean;
  empty: string;
}

export interface UserSearchInlineProps {
  existingIds: string[];
  onAdd: (user: AdminUserSearchResult) => void;
  listboxId?: string;
  disabled?: boolean;
}
