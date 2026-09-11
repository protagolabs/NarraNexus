/**
 * @file_name: JobPayloadEditDialog.tsx
 * @description: Dialog for editing a job's content fields (title,
 * description, payload/prompt). Mirrors JobScheduleEditDialog.tsx's shape —
 * same Dialog shell, same "only send the changed fields" contract, same
 * saving/error prop pattern — but for the seam route PUT /api/jobs/{job_id}
 * instead of the schedule route (GitHub #86: that route already existed
 * with no frontend edit UI at all).
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';
import { Dialog, DialogContent, DialogFooter, Input, Textarea, Button } from '@/components/ui';
import type { Job } from '@/types/api';

interface JobPayloadEditDialogProps {
  job: Job;
  isOpen: boolean;
  saving: boolean;
  onClose: () => void;
  onSave: (fields: { title?: string; description?: string; payload?: string }) => void;
}

export function JobPayloadEditDialog({ job, isOpen, saving, onClose, onSave }: JobPayloadEditDialogProps) {
  const { t } = useTranslation();
  const [title, setTitle] = useState(job.title || '');
  const [description, setDescription] = useState(job.description || '');
  const [payload, setPayload] = useState(job.payload || '');
  const [error, setError] = useState<string | null>(null);

  const handleSave = () => {
    setError(null);
    if (!title.trim()) {
      setError(t('jobs.editPayload.titleRequired'));
      return;
    }

    // Only send the fields that actually changed — matches the backend's
    // only-passed-fields-change semantics (JobUpdateFields: None = unchanged).
    const fields: { title?: string; description?: string; payload?: string } = {};
    if (title !== (job.title || '')) fields.title = title;
    if (description !== (job.description || '')) fields.description = description;
    if (payload !== (job.payload || '')) fields.payload = payload;

    if (Object.keys(fields).length === 0) { onClose(); return; }
    onSave(fields);
  };

  return (
    <Dialog isOpen={isOpen} onClose={onClose} title={t('jobs.editPayload.title')} size="md">
      <DialogContent>
        <div className="space-y-4 text-sm">
          <p className="text-xs text-[var(--text-tertiary)]">{t('jobs.editPayload.hint')}</p>

          <label className="block space-y-1">
            <span className="text-xs text-[var(--text-secondary)]">{t('jobs.editPayload.titleLabel')}</span>
            <Input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </label>

          <label className="block space-y-1">
            <span className="text-xs text-[var(--text-secondary)]">{t('jobs.editPayload.descriptionLabel')}</span>
            <Input
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </label>

          <label className="block space-y-1">
            <span className="text-xs text-[var(--text-secondary)]">{t('jobs.editPayload.payloadLabel')}</span>
            {/* Fixed-height form field: autoResize would grow the box past the
                md dialog as a long prompt is typed. */}
            <Textarea
              value={payload}
              onChange={(e) => setPayload(e.target.value)}
              rows={6}
              autoResize={false}
              className="font-mono"
            />
            <span className="text-[10px] text-[var(--text-tertiary)]">{t('jobs.editPayload.payloadHint')}</span>
          </label>

          {error && (
            <p className="text-xs text-[var(--color-error)]">{error}</p>
          )}
        </div>
      </DialogContent>
      <DialogFooter>
        <Button variant="ghost" size="sm" onClick={onClose} disabled={saving}>
          {t('jobs.editPayload.cancel')}
        </Button>
        <Button variant="default" size="sm" onClick={handleSave} disabled={saving}>
          {saving ? (
            <>
              <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" />
              {t('jobs.action.savingPayload')}
            </>
          ) : (
            t('jobs.editPayload.save')
          )}
        </Button>
      </DialogFooter>
    </Dialog>
  );
}
