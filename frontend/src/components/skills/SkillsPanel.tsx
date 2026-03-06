/**
 * @file_name: SkillsPanel.tsx
 * @author: Bin Liang
 * @date: 2026-02-03
 * @description: Skills management panel (TanStack Query powered)
 *
 * Features:
 * - Display user's installed Skills
 * - Support installing Skills from GitHub or zip files
 * - Support disabling/enabling/removing Skills
 * - Support Study feature: Agent automatically learns Skill documentation
 */

import { useState } from 'react';
import {
  Puzzle,
  RefreshCw,
  Github,
  FileArchive,
  Loader2,
  AlertCircle,
} from 'lucide-react';
import { Card, CardHeader, CardTitle, CardContent, Button, Badge } from '@/components/ui';
import { cn } from '@/lib/utils';
import {
  useSkillsList,
  useInstallFromGithub,
  useInstallFromZip,
  useToggleSkill,
  useRemoveSkill,
  useStudySkill,
  useStudyStatus,
} from '@/hooks/useSkills';
import type { SkillInfo } from '@/types/skills';
import { SkillCard } from './SkillCard';
import { InstallDialog } from './InstallDialog';
import type { InstallMode } from './InstallDialog';

export function SkillsPanel() {
  const [installMode, setInstallMode] = useState<InstallMode>(null);
  const [showDisabled, setShowDisabled] = useState(false);
  const [studyingSkillName, setStudyingSkillName] = useState<string | null>(null);

  // ── Queries ──────────────────────────────────────────────────────────────
  const { data: skills = [], isLoading, error, refetch } = useSkillsList(showDisabled);

  // Auto-detect skills being studied (resume polling after page load)
  const activeStudying = studyingSkillName
    ?? skills.find((s) => s.study_status === 'studying')?.name
    ?? null;
  useStudyStatus(activeStudying);

  // ── Mutations ────────────────────────────────────────────────────────────
  const installGithub = useInstallFromGithub();
  const installZip = useInstallFromZip();
  const toggleSkill = useToggleSkill();
  const removeSkill = useRemoveSkill();
  const studySkill = useStudySkill();

  // ── Handlers ─────────────────────────────────────────────────────────────
  const handleInstall = (data: { url?: string; branch?: string; file?: File }) => {
    if (installMode === 'github' && data.url) {
      installGithub.mutate(
        { url: data.url, branch: data.branch || 'main' },
        { onSuccess: () => setInstallMode(null) }
      );
    } else if (installMode === 'zip' && data.file) {
      installZip.mutate(data.file, { onSuccess: () => setInstallMode(null) });
    }
  };

  const handleToggle = (skill: SkillInfo) => {
    toggleSkill.mutate({ name: skill.name, disabled: skill.disabled });
  };

  const handleRemove = (skill: SkillInfo) => {
    if (!confirm(`Are you sure you want to remove "${skill.name}"? This action cannot be undone.`)) {
      return;
    }
    removeSkill.mutate(skill.name);
  };

  const handleStudy = (skill: SkillInfo) => {
    setStudyingSkillName(skill.name);
    studySkill.mutate(skill.name, {
      onError: () => setStudyingSkillName(null),
    });
  };

  const isInstalling = installGithub.isPending || installZip.isPending;

  return (
    <Card variant="glass" className="flex flex-col h-full">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-lg bg-[var(--accent-secondary)]/10 flex items-center justify-center">
            <Puzzle className="w-4 h-4 text-[var(--accent-secondary)]" />
          </div>
          <span>Skills</span>
        </CardTitle>
        <div className="flex items-center gap-2">
          <Badge variant={skills.length > 0 ? 'accent' : 'default'} className="font-mono">
            {skills.length}
          </Badge>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => refetch()}
            disabled={isLoading}
            title="Refresh"
            className="hover:bg-[var(--accent-glow)]"
          >
            <RefreshCw className={cn('w-4 h-4', isLoading && 'animate-spin')} />
          </Button>
        </div>
      </CardHeader>

      {/* Action bar */}
      <div className="px-4 pb-3 flex items-center justify-between gap-2">
        <div className="flex gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setInstallMode('github')}
            className="text-xs"
          >
            <Github className="w-3.5 h-3.5 mr-1.5" />
            From GitHub
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setInstallMode('zip')}
            className="text-xs"
          >
            <FileArchive className="w-3.5 h-3.5 mr-1.5" />
            From Zip
          </Button>
        </div>

        <label className="flex items-center gap-2 text-xs text-[var(--text-tertiary)] cursor-pointer">
          <input
            type="checkbox"
            checked={showDisabled}
            onChange={(e) => setShowDisabled(e.target.checked)}
            className="rounded border-[var(--border-default)]"
          />
          Show disabled
        </label>
      </div>

      <CardContent className="flex-1 overflow-hidden min-h-0">
        {error ? (
          <div className="h-full flex items-center justify-center">
            <div className="text-center p-8">
              <div className="w-14 h-14 rounded-2xl bg-[var(--color-error)]/10 mx-auto mb-4 flex items-center justify-center">
                <AlertCircle className="w-7 h-7 text-[var(--color-error)]" />
              </div>
              <p className="text-[var(--color-error)] text-sm font-medium mb-1">Error</p>
              <p className="text-[var(--text-tertiary)] text-xs">
                {error instanceof Error ? error.message : 'Failed to load skills'}
              </p>
              <Button variant="ghost" size="sm" onClick={() => refetch()} className="mt-4">
                <RefreshCw className="w-3 h-3 mr-1.5" />
                Retry
              </Button>
            </div>
          </div>
        ) : isLoading ? (
          <div className="h-full flex items-center justify-center">
            <Loader2 className="w-8 h-8 text-[var(--accent-primary)] animate-spin" />
          </div>
        ) : skills.length === 0 ? (
          <div className="h-full flex items-center justify-center">
            <div className="text-center p-8">
              <div className="w-14 h-14 rounded-2xl bg-[var(--accent-secondary)]/10 mx-auto mb-4 flex items-center justify-center">
                <Puzzle className="w-7 h-7 text-[var(--accent-secondary)]" />
              </div>
              <p className="text-[var(--text-secondary)] text-sm font-medium mb-1">
                No skills installed
              </p>
              <p className="text-[var(--text-tertiary)] text-xs">
                Install skills from GitHub or upload a zip file
              </p>
            </div>
          </div>
        ) : (
          <div className="h-full overflow-y-auto space-y-2 py-2">
            {skills.map((skill) => (
              <SkillCard
                key={skill.name}
                skill={skill}
                onToggle={handleToggle}
                onRemove={handleRemove}
                onStudy={handleStudy}
                isToggling={toggleSkill.isPending && toggleSkill.variables?.name === skill.name}
                isRemoving={removeSkill.isPending && removeSkill.variables === skill.name}
                isStudying={activeStudying === skill.name}
              />
            ))}
          </div>
        )}
      </CardContent>

      {/* Install dialog */}
      <InstallDialog
        mode={installMode}
        onClose={() => setInstallMode(null)}
        onInstall={handleInstall}
        isInstalling={isInstalling}
      />
    </Card>
  );
}

export default SkillsPanel;
