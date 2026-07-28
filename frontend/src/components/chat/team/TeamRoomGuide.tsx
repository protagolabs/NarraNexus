/**
 * @file_name: TeamRoomGuide.tsx
 * @author:
 * @date: 2026-07-28
 * @description: "How this room works" affordance for a team group chat.
 *
 * A team room has addressing rules a 1:1 chat does not, and the rules changed:
 * an un-addressed message is no longer dropped — it goes to the room's default
 * responder (lead, else earliest-joined member). The old guidance was a single
 * grey placeholder line that also said the opposite ("@mention a teammate to
 * start the conversation"), and it disappeared for good once the room had one
 * message in it.
 *
 * So: state the three addressing modes explicitly, name who answers when you
 * address nobody, and keep it reachable forever — expanded on a first visit,
 * a one-line "how this room works" toggle after the user folds it. The fold is
 * remembered per team in localStorage, so dismissing it is a decision the user
 * makes once, not a banner they re-close on every visit.
 */

import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AtSign, ChevronDown, ChevronRight, HelpCircle, Megaphone, MessageSquare } from 'lucide-react';

const STORAGE_PREFIX = 'nx.team.guide.';

function storageKey(teamId: string): string {
  return `${STORAGE_PREFIX}${teamId}`;
}

function readDismissed(teamId: string): boolean {
  try {
    return localStorage.getItem(storageKey(teamId)) === 'dismissed';
  } catch {
    // Private mode / storage disabled — show the guide rather than blow up.
    return false;
  }
}

export function TeamRoomGuide({
  teamId,
  leadName,
}: {
  teamId: string;
  /** Display name of the default responder; null when the team has no members. */
  leadName: string | null;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(() => !readDismissed(teamId));

  const toggle = useCallback(() => {
    setOpen((wasOpen) => {
      try {
        localStorage.setItem(storageKey(teamId), wasOpen ? 'dismissed' : 'open');
      } catch {
        // Preference is a nicety; never let it break the toggle.
      }
      return !wasOpen;
    });
  }, [teamId]);

  const rules: Array<{ icon: typeof AtSign; text: string }> = [
    {
      icon: MessageSquare,
      text: leadName
        ? t('chat.team.guide.plainWithLead', { name: leadName })
        : t('chat.team.guide.plain'),
    },
    { icon: AtSign, text: t('chat.team.guide.mention') },
    { icon: Megaphone, text: t('chat.team.guide.broadcast') },
  ];

  return (
    <div className="shrink-0 border-b border-[var(--rule)] px-3 py-1.5">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        className="flex w-full items-center gap-2 text-left"
      >
        <HelpCircle className="h-3 w-3 shrink-0 text-[var(--text-tertiary)]" />
        <span className="min-w-0 flex-1 truncate text-[11px] text-[var(--text-secondary)]">
          {t('chat.team.guide.title')}
        </span>
        {open ? (
          <ChevronDown className="h-3 w-3 shrink-0 text-[var(--text-tertiary)]" />
        ) : (
          <ChevronRight className="h-3 w-3 shrink-0 text-[var(--text-tertiary)]" />
        )}
      </button>

      {open && (
        <div className="mt-1 space-y-1 pl-5">
          {rules.map(({ icon: Icon, text }) => (
            <div key={text} className="flex items-baseline gap-2 text-[11px] leading-relaxed">
              <Icon className="mt-[3px] h-3 w-3 shrink-0 self-start text-[var(--text-tertiary)]" />
              <span className="text-[var(--text-secondary)]">{text}</span>
            </div>
          ))}
          <p className="pt-0.5 text-[10px] leading-relaxed text-[var(--text-tertiary)]">
            {t('chat.team.guide.relay')}
          </p>
        </div>
      )}
    </div>
  );
}
