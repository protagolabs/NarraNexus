/**
 * ModelBubbleInput — tag-style model-id input with autocomplete
 * suggestions. Shared by every "connect a provider with a custom model
 * list" surface: Settings' CustomEndpointForm/edit-models dialog, and
 * (via CustomEndpointForm) the Create Agent wizard's provider step.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import type { ModelSuggestionGroup } from '@/lib/agentFramework'

export function ModelBubbleInput({
  models, onChange, placeholder, suggestions
}: {
  models: string[]
  onChange: (m: string[]) => void
  placeholder?: string
  suggestions?: ModelSuggestionGroup[]
}) {
  const { t } = useTranslation()
  const resolvedPlaceholder = placeholder ?? t('settings.provider.modelNamePlaceholder')
  const [input, setInput] = useState('')
  const hasPending = input.trim().length > 0
  const addModel = () => {
    const v = input.trim()
    if (v && !models.includes(v)) onChange([...models, v])
    setInput('')
  }
  const addSuggestion = (m: string) => {
    if (!models.includes(m)) onChange([...models, m])
  }
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-1.5">
        {models.map((m) => (
          <span key={m} className="inline-flex items-center gap-1.5 px-2 py-1 text-[12px] font-[family-name:var(--font-mono)] bg-[var(--bg-secondary)] text-[var(--text-primary)] border border-[var(--border-subtle)] whitespace-nowrap">
            {m}
            <button
              onClick={() => onChange(models.filter((x) => x !== m))}
              className="text-[var(--text-tertiary)] hover:text-[var(--color-error)] transition-colors"
              aria-label={t('settings.provider.removeModel', { model: m })}
            >
              ×
          </button>
        </span>
        ))}
        <span className="inline-flex items-center gap-1">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addModel() } }}
            placeholder={resolvedPlaceholder}
            style={{ width: Math.max(100, (input.length + 1) * 8) }}
            className={cn(
              'px-2 py-1 text-[12px] font-[family-name:var(--font-mono)] border bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none',
              hasPending
                ? 'border-[var(--color-warning)] focus:border-[var(--color-warning)]'
                : 'border-[var(--rule)] focus:border-[var(--text-primary)]'
            )}
          />
          <button
            onClick={addModel}
            disabled={!hasPending}
            className={cn(
              'px-2 py-1 text-[12px] font-[family-name:var(--font-mono)] border transition-all disabled:opacity-30',
              hasPending
                ? 'bg-[var(--text-primary)] text-[var(--text-inverse)] border-[var(--text-primary)] hover:opacity-90 animate-pulse'
                : 'border-[var(--rule)] text-[var(--text-tertiary)] hover:text-[var(--text-primary)]'
            )}
            aria-label={t('settings.provider.addModel')}
          >
            +
          </button>
        </span>
      </div>
      {hasPending && (
        <p className="text-xs text-[var(--color-warning)]">
          {t('settings.provider.pendingHint', { model: input.trim() })}
        </p>
      )}
      {suggestions && suggestions.length > 0 && (
        <ModelSuggestionChips
          groups={suggestions}
          selected={models}
          onPick={addSuggestion}
        />
      )}
    </div>
  )
}

export function ModelSuggestionChips({
  groups, selected, onPick
}: {
  groups: ModelSuggestionGroup[]
  selected: string[]
  onPick: (m: string) => void
}) {
  const { t } = useTranslation()
  const visibleGroups = groups
    .map((g) => ({ ...g, models: g.models.filter((m) => !selected.includes(m)) }))
    .filter((g) => g.models.length > 0)
  if (visibleGroups.length === 0) return null
  return (
    <div className="pt-2 border-t border-[var(--border-subtle)] space-y-2">
      <p className="text-xs text-[var(--text-tertiary)]">
        {t('settings.provider.suggestionsHint')}
      </p>
      {visibleGroups.map((g) => (
        <div key={g.label} className="space-y-1">
          <span className="text-[10px] uppercase tracking-wider text-[var(--text-tertiary)] font-medium">
            {g.label}
          </span>
          <div className="flex flex-wrap gap-1.5">
            {g.models.map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => onPick(m)}
                className="inline-flex items-center gap-1 px-2.5 py-1 text-sm rounded-full border border-dashed border-[var(--border-default)] bg-[var(--bg-tertiary)]/50 text-[var(--text-tertiary)] opacity-70 hover:opacity-100 hover:bg-[var(--accent-primary)]/10 hover:text-[var(--accent-primary)] hover:border-[var(--accent-primary)]/50 transition-all whitespace-nowrap"
                title={t('settings.provider.addModelTitle', { model: m })}
              >
                <span className="text-[var(--text-tertiary)]">+</span>
                {m}
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
