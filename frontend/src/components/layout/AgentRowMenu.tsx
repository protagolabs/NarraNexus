/**
 * @file_name: AgentRowMenu.tsx
 * @author:
 * @date: 2026-06-10
 * @description: Kebab (⋯) context menu for a single sidebar agent row —
 * Rename, Model & framework, Delete.
 *
 * OWNER-REQUIRED entry (2026-09-11): #383 deleted this menu as a duplicate of
 * the agent profile page, and the Owner asked for it back ("the three dots
 * behind each agent are gone, restore them: rename / delete / setting
 * model"). Do not remove it as redundant — the profile page is a second door,
 * not a replacement.
 *
 * Inline absolute panel (no portal) so it works inside the sidebar scroll
 * container, same as TeamRowMenu. The host renders it only for agents the
 * viewer owns (every action is owner-only server-side).
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { MoreVertical, Pencil, SlidersHorizontal, Trash2 } from 'lucide-react';
import { useDismissOnOutside } from '@/hooks';
import { cn } from '@/lib/utils';

export interface AgentRowMenuProps {
  /** Start the row's inline rename. */
  onRename: () => void;
  /** Open the agent's model & framework panel (AgentLlmConfigPanel). */
  onOpenModelConfig: () => void;
  /** Delete the agent (the host confirms first). */
  onDelete: () => void;
  /** Fired on open/close so the host row can lift its z-index above the rows
   *  below (each row is its own stacking context — animate-slide-up retains a
   *  transform — so the panel's own z-index cannot escape it). */
  onOpenChange?: (open: boolean) => void;
}

export function AgentRowMenu({ onRename, onOpenModelConfig, onDelete, onOpenChange }: AgentRowMenuProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  // Notify the parent from the event handler (NOT inside a setState updater —
  // that runs during render and triggers a cross-component setState warning).
  const setOpenAndNotify = (next: boolean) => {
    setOpen(next);
    onOpenChange?.(next);
  };
  const containerRef = useDismissOnOutside<HTMLDivElement>(open, () => setOpenAndNotify(false));

  // Every click stops here: the row's own onClick selects the agent, and a
  // menu action must not do that as a side effect.
  const handleItem = (handler: () => void) => (e: React.MouseEvent) => {
    e.stopPropagation();
    setOpenAndNotify(false);
    handler();
  };

  return (
    <div ref={containerRef} className="relative inline-flex" onClick={(e) => e.stopPropagation()}>
      <button
        type="button"
        aria-label={t('layout.agentRowMenu.options')}
        aria-expanded={open}
        onClick={(e) => { e.stopPropagation(); setOpenAndNotify(!open); }}
        className={cn(
          'p-1 rounded-[var(--radius-xs)] transition-colors',
          'hover:bg-[var(--nm-paper-warm)]',
          open && 'bg-[var(--nm-paper-warm)]',
        )}
      >
        <MoreVertical className="w-3 h-3" style={{ color: 'var(--nm-ink50)' }} />
      </button>

      {open && (
        <div
          className={cn(
            'absolute right-0 top-full mt-0.5 z-50',
            'min-w-[150px] py-0.5',
            'rounded-[var(--radius-sm)] border shadow-md',
            'bg-[var(--nm-paper)] border-[var(--nm-hairline)]',
          )}
        >
          <MenuItem
            icon={<Pencil className="w-3 h-3" />}
            label={t('layout.agentRowMenu.rename')}
            onClick={handleItem(onRename)}
          />
          <MenuItem
            icon={<SlidersHorizontal className="w-3 h-3" />}
            label={t('layout.agentRowMenu.modelFramework')}
            onClick={handleItem(onOpenModelConfig)}
          />
          <MenuItem
            icon={<Trash2 className="w-3 h-3" />}
            label={t('layout.agentRowMenu.delete')}
            danger
            onClick={handleItem(onDelete)}
          />
        </div>
      )}
    </div>
  );
}

function MenuItem({
  icon,
  label,
  danger,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  danger?: boolean;
  onClick: (e: React.MouseEvent) => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'w-full flex items-center gap-2 px-3 py-1.5 text-xs text-left',
        'transition-colors',
        danger
          ? 'text-[var(--color-error)] hover:bg-[var(--color-error)]/10'
          : 'text-[var(--nm-ink)] hover:bg-[var(--nm-paper-warm)]',
      )}
    >
      {icon}
      <span>{label}</span>
    </button>
  );
}
