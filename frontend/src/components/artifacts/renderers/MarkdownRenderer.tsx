/**
 * @file_name: MarkdownRenderer.tsx
 * @description: Renderer for text/markdown artifacts — a block editor
 * (Milkdown Crepe): the rendered surface itself takes the cursor, no
 * view/edit mode anywhere (no-mode framework, spec A §1).
 *
 * Loss guards (spec A §1.3, refined after the 2026-08-19 round-trip spike):
 *  - frontmatter is split off BEFORE the editor sees the text (Crepe would
 *    destroy it) and re-attached verbatim on save;
 *  - after mount the editor's own serialization is AST-compared against the
 *    loaded body — structure loss (raw html blocks, math, …) falls back to
 *    the read-only ReactMarkdown view with a guard banner; pure style
 *    normalization (bullet chars, table dashes) passes and lands on the
 *    first real save.
 *
 * Save semantics: debounced autosave (typing pause), quiet 409 banner with
 * the shared two-choice. Dirty state mirrors into the artifact store for the
 * tab dot; the localStorage draft layer lives in useArtifactEditor.
 */

import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Crepe } from '@milkdown/crepe';
import '@milkdown/crepe/theme/common/style.css';
import '@milkdown/crepe/theme/frame.css';
import type { Artifact } from '@/types/artifact';
import { useArtifactRawUrl } from '@/hooks/useArtifactRawUrl';
import { useArtifactHeal } from '@/hooks/useArtifactHeal';
import { useArtifactEditor, type ArtifactEditorState } from '@/hooks/useArtifactEditor';
import { useArtifactStore } from '@/stores/artifactStore';
import { extractFrontmatter, mdAstEqual } from '@/lib/mdEditSafety';
import ArtifactHealModal from '../ArtifactHealModal';
import { ConflictBanner, DraftRestoredBanner } from './editorBanners';

const AUTOSAVE_IDLE_MS = 2000;

interface Props {
  artifact: Artifact;
}

export default function MarkdownRenderer({ artifact }: Props) {
  const { url, error: urlError, reload } = useArtifactRawUrl(
    artifact.agent_id,
    artifact.artifact_id,
    artifact.updated_at,
  );
  const editor = useArtifactEditor(artifact, url);
  const setEditorDirty = useArtifactStore((s) => s.setEditorDirty);
  const heal = useArtifactHeal(artifact.agent_id, artifact.artifact_id);
  const attemptRef = useRef(heal.attempt);
  useEffect(() => {
    attemptRef.current = heal.attempt;
  }, [heal.attempt]);
  useEffect(() => {
    if (heal.recoveryVersion > 0) reload();
  }, [heal.recoveryVersion, reload]);
  useEffect(() => {
    if (editor.status === 'error' && editor.error?.includes('fetch failed: 410')) {
      attemptRef.current();
    }
  }, [editor.status, editor.error]);

  useEffect(() => {
    setEditorDirty(artifact.artifact_id, editor.dirty);
    return () => setEditorDirty(artifact.artifact_id, false);
  }, [editor.dirty, artifact.artifact_id, setEditorDirty]);

  // The document the surface was mounted from. Typing changes editor.text but
  // NOT docBase (no remount per keystroke); an external reload while clean —
  // or a conflict discard — replaces docBase and remounts the surface.
  // Render-time adjustment (React's "adjusting state when a prop changes"
  // pattern), not an effect: the re-render restarts immediately.
  const [docBase, setDocBase] = useState<string | null>(null);
  if (
    editor.status === 'ready' &&
    (docBase === null || (!editor.dirty && editor.text !== docBase))
  ) {
    setDocBase(editor.text);
  }

  if (urlError) return <div className="p-4 text-red-400">Failed to load: {urlError}</div>;
  if (editor.status === 'error') {
    return <div className="p-4 text-red-400">Failed to load: {editor.error}</div>;
  }
  if (editor.status === 'loading' || docBase === null) {
    return <div className="p-4 opacity-60">Loading…</div>;
  }

  const { frontmatter, body } = extractFrontmatter(docBase);

  return (
    <>
      {editor.draftRestored && <DraftRestoredBanner />}
      {editor.conflict && (
        <ConflictBanner
          onOverwrite={() => void editor.overwriteConflict()}
          onDiscard={() => void editor.discardConflict()}
        />
      )}
      <MdEditSurface
        key={docBase}
        body={body}
        frontmatter={frontmatter}
        editor={editor}
      />
      <ArtifactHealModal
        open={heal.modalOpen}
        artifactTitle={artifact.title}
        candidates={heal.candidates}
        message={heal.message}
        busy={heal.busy}
        onPick={(workspacePath) => heal.attempt(workspacePath)}
        onDismiss={heal.dismiss}
      />
    </>
  );
}

function MdEditSurface({
  body,
  frontmatter,
  editor,
}: {
  body: string;
  frontmatter: string;
  editor: ArtifactEditorState;
}) {
  const { t } = useTranslation();
  const rootRef = useRef<HTMLDivElement>(null);
  // null = probing (editor mounting), true = editable, false = guard fallback
  const [editable, setEditable] = useState<boolean | null>(null);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // The editor callbacks live for the Crepe instance's whole life; going
  // through refs keeps them reading CURRENT state without re-mounting Crepe.
  // Assigned in an effect (after every render), never during render.
  const editorRef = useRef(editor);
  const frontmatterRef = useRef(frontmatter);
  useEffect(() => {
    editorRef.current = editor;
    frontmatterRef.current = frontmatter;
  });

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    let destroyed = false;
    const crepe = new Crepe({ root, defaultValue: body });

    crepe.on((listener) => {
      listener.markdownUpdated((_ctx, markdown, prevMarkdown) => {
        if (markdown === prevMarkdown) return;
        const ed = editorRef.current;
        ed.setText(frontmatterRef.current + markdown);
        if (saveTimer.current) clearTimeout(saveTimer.current);
        saveTimer.current = setTimeout(() => {
          const now = editorRef.current;
          // A pending conflict pauses autosave — re-sending the stale base
          // would just 409 again; the banner owns the next move.
          if (now.dirty && !now.saving && !now.conflict) void now.save();
        }, AUTOSAVE_IDLE_MS);
      });
    });

    void crepe.create().then(() => {
      if (destroyed) return;
      // Editability probe: what would the editor write back for the UNEDITED
      // document? Structure loss (html blocks, math, …) → guard fallback.
      const out = crepe.getMarkdown();
      setEditable(mdAstEqual(body, out));
    });

    return () => {
      destroyed = true;
      if (saveTimer.current) clearTimeout(saveTimer.current);
      void crepe.destroy();
    };
    // body is intentionally the ONLY content dependency: the parent remounts
    // this surface (key=docBase) whenever the document base changes.
  }, [body]);

  return (
    <div className="markdown-content max-w-none">
      {editable === false && (
        <div className="px-3 py-1.5 text-xs bg-amber-500/10 border-b border-amber-500/30">
          {t('artifacts.editor.mdGuardDisabled')}
        </div>
      )}
      {/* Crepe mounts into this div; kept in the tree during probing so the
          probe and the editable surface are the same instance. Padding lives
          on the ProseMirror override in index.css (narrow-column fix) — no
          wrapper padding on top of it. */}
      <div ref={rootRef} className={editable === false ? 'hidden' : ''} />
      {editable === false && (
        <div className="p-4">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{body}</ReactMarkdown>
        </div>
      )}
    </div>
  );
}
