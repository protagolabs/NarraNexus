/**
 * TeamFilterBar — sidebar header showing team chips for filtering AgentList.
 *
 * Subproject 1: All / <team chips...> / Untagged.
 * Click "All" to show all agents, click a team chip to scope, click the gear
 * icon to open TeamManagementModal.
 */

import { useEffect, useMemo, useState } from 'react';
import { Settings2 } from 'lucide-react';
import { useTeamsStore, useConfigStore } from '@/stores';
import { TeamManagementModal } from '@/components/teams/TeamManagementModal';
import { cn } from '@/lib/utils';

interface Props {
  selectedFilter: string;            // 'all' | 'untagged' | <team_id>
  onChange: (filter: string) => void;
}

export function TeamFilterBar({ selectedFilter, onChange }: Props) {
  const { teams, refresh, loaded } = useTeamsStore();
  const { agents } = useConfigStore();
  const [openMgmt, setOpenMgmt] = useState(false);

  useEffect(() => {
    if (!loaded) refresh();
  }, [loaded, refresh]);

  const untaggedCount = useMemo(() => {
    const memberSet = new Set<string>();
    teams.forEach((t) => t.member_agent_ids.forEach((id) => memberSet.add(id)));
    return agents.filter((a) => !memberSet.has(a.agent_id)).length;
  }, [teams, agents]);

  return (
    <div className="px-3 pt-3 pb-2 border-b border-[var(--border-subtle)] space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-semibold text-[var(--text-tertiary)] uppercase tracking-[0.15em] font-[family-name:var(--font-mono)]">
          Teams
        </span>
        <button
          onClick={() => setOpenMgmt(true)}
          className="p-1 hover:bg-[var(--bg-tertiary)]"
          title="Manage teams"
        >
          <Settings2 className="w-3.5 h-3.5 text-[var(--text-tertiary)]" />
        </button>
      </div>
      <div className="flex flex-wrap gap-1">
        <Chip
          label="All"
          count={agents.length}
          active={selectedFilter === 'all'}
          onClick={() => onChange('all')}
        />
        {teams.map((t) => (
          <Chip
            key={t.team.team_id}
            label={t.team.name}
            count={t.member_agent_ids.length}
            active={selectedFilter === t.team.team_id}
            color={t.team.color}
            onClick={() => onChange(t.team.team_id)}
          />
        ))}
        <Chip
          label="Untagged"
          count={untaggedCount}
          active={selectedFilter === 'untagged'}
          onClick={() => onChange('untagged')}
        />
      </div>
      <TeamManagementModal open={openMgmt} onClose={() => setOpenMgmt(false)} />
    </div>
  );
}

function Chip({ label, count, active, color, onClick }: {
  label: string;
  count: number;
  active: boolean;
  color?: string | null;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'flex items-center gap-1 px-2 py-1 text-[11px] font-mono border transition-colors',
        active
          ? 'bg-[var(--bg-elevated)] border-[var(--border-strong)] text-[var(--text-primary)]'
          : 'border-[var(--border-subtle)] text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)]'
      )}
    >
      {color && <span className="w-2 h-2 rounded-full" style={{ backgroundColor: color }} />}
      <span>{label}</span>
      <span className="text-[var(--text-tertiary)]">({count})</span>
    </button>
  );
}
