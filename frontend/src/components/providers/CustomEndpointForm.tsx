/**
 * CustomEndpointForm — the "bring your own endpoint" add-provider method:
 * pick a protocol, fill in name/auth/base_url/api_key/models, test, save.
 * Extracted from ProviderSettings.tsx's "custom" tab so the Create Agent
 * wizard's API-Key step can offer the same power-user path (self-hosted /
 * third-party endpoints) that Settings already does, through one shared
 * implementation instead of two.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { MODEL_SUGGESTION_GROUPS } from '@/lib/agentFramework'
import { ModelBubbleInput } from './ModelBubbleInput'
import { addProvider, testProviderConfig } from './providerApi'

interface CustomEndpointFormProps {
  onComplete: () => void
}

export function CustomEndpointForm({ onComplete }: CustomEndpointFormProps) {
  const { t } = useTranslation()
  const [protocol, setProtocol] = useState<'anthropic' | 'openai' | null>(null)
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [key, setKey] = useState('')
  const [auth, setAuth] = useState<'api_key' | 'bearer_token'>('api_key')
  const [models, setModels] = useState<string[]>([])
  const [adding, setAdding] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<{ ok: boolean; msg: string } | null>(null)
  const [error, setError] = useState('')

  const openProtocol = (p: 'anthropic' | 'openai') => {
    setProtocol(p)
    setName('')
    setUrl(p === 'anthropic' ? 'https://api.anthropic.com' : 'https://api.openai.com/v1')
    setKey('')
    setAuth('api_key')
    setModels([])
    setError('')
    setTesting(false)
    setTestResult(null)
  }

  const handleTest = async () => {
    if (!protocol || !key.trim()) { setError(t('settings.provider.enterApiKeyShort')); return }
    setTesting(true)
    setTestResult(null)
    const res = await testProviderConfig({
      card_type: protocol,
      api_key: key.trim(),
      base_url: url.trim(),
      auth_type: auth,
      models,
    })
    setTestResult({ ok: res.ok, msg: res.msg || t('settings.provider.networkError') })
    setTesting(false)
  }

  const handleSubmit = async () => {
    if (!protocol || !key.trim()) { setError(t('settings.provider.enterApiKeyShort')); return }
    setAdding(true)
    setError('')
    const res = await addProvider({
      card_type: protocol,
      name: name.trim() || undefined,
      api_key: key.trim(),
      base_url: url.trim(),
      auth_type: auth,
      models,
    })
    if (res.ok) {
      setProtocol(null); setName(''); setUrl(''); setKey(''); setAuth('api_key'); setModels([])
      setTestResult(null)
      onComplete()
    } else {
      setError(res.detail || t('settings.provider.failed'))
    }
    setAdding(false)
  }

  return (
    <div className="space-y-4">
      <div>
        <label className="block text-sm text-[var(--text-tertiary)] mb-1">
          {t('settings.provider.protocolLabel')}
        </label>
        <select
          aria-label={t('settings.provider.protocolLabel')}
          value={protocol || ''}
          onChange={(e) => {
            const v = e.target.value
            if (!v) setProtocol(null)
            else openProtocol(v as 'anthropic' | 'openai')
          }}
          className="w-full px-3 py-2 text-sm rounded-[var(--radius-lg)] border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
        >
          <option value="">{t('settings.provider.selectProtocol')}</option>
          <option value="openai">{t('settings.provider.protocolOpenai')}</option>
          <option value="anthropic">{t('settings.provider.protocolAnthropic')}</option>
        </select>
      </div>

      {protocol && (
        <div className="p-4 rounded-[var(--radius-xl)] border border-[var(--border-default)] bg-[var(--bg-tertiary)] space-y-3">
          <p className="text-sm text-[var(--text-tertiary)]">
            {protocol === 'anthropic' ? t('settings.provider.anthropicEndpointHint') : t('settings.provider.openaiEndpointHint')}
          </p>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-sm text-[var(--text-tertiary)] mb-1">{t('settings.provider.providerNameLabel')}</label>
              <input type="text" value={name} onChange={(e) => setName(e.target.value)}
                placeholder={protocol === 'anthropic' ? t('settings.provider.providerNameEgAnthropic') : t('settings.provider.providerNameEgOpenai')}
                className="w-full px-3 py-2 text-sm rounded-[var(--radius-lg)] border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]" />
            </div>
            {protocol === 'anthropic' ? (
              <div>
                <label className="block text-sm text-[var(--text-tertiary)] mb-1">{t('settings.provider.authType')}</label>
                <select value={auth} onChange={(e) => { setAuth(e.target.value as 'api_key' | 'bearer_token'); setTestResult(null) }}
                  className="w-full px-3 py-2 text-sm rounded-[var(--radius-lg)] border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none">
                  <option value="api_key">{t('settings.provider.authApiKey')}</option>
                  <option value="bearer_token">{t('settings.provider.authBearerToken')}</option>
                </select>
              </div>
            ) : <div />}
          </div>
          <div>
            <label className="block text-sm text-[var(--text-tertiary)] mb-1">{t('settings.provider.baseUrl')}</label>
            <input type="text" value={url} onChange={(e) => { setUrl(e.target.value); setTestResult(null) }}
              placeholder={t('settings.provider.baseUrl')}
              className="w-full px-3 py-2 text-sm rounded-[var(--radius-lg)] border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]" />
          </div>
          <div>
            <label className="block text-sm text-[var(--text-tertiary)] mb-1">{t('settings.provider.apiKeyLabel')}</label>
            <input type="password" value={key} onChange={(e) => { setKey(e.target.value); setTestResult(null) }}
              placeholder={t('settings.provider.yourApiKey')}
              className="w-full px-3 py-2 text-sm rounded-[var(--radius-lg)] border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]" />
          </div>
          <div>
            <label className="block text-sm text-[var(--text-tertiary)] mb-1">{t('settings.provider.availableModels')}</label>
            <ModelBubbleInput
              models={models}
              onChange={(m) => { setModels(m); setTestResult(null) }}
              suggestions={MODEL_SUGGESTION_GROUPS}
            />
          </div>
          {testResult && (
            <p className={cn('text-sm', testResult.ok ? 'text-[var(--color-success)]' : 'text-[var(--color-error)]')}>
              {testResult.msg}
            </p>
          )}
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <div className="flex gap-2">
            <button onClick={handleTest} disabled={testing || adding || !key.trim()}
              className="px-4 py-2.5 text-sm font-medium rounded-[var(--radius-lg)] border border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--nm-paper-warm)] disabled:opacity-40 transition-colors">
              {testing ? '...' : t('settings.provider.testConnection')}
            </button>
            <button onClick={handleSubmit} disabled={adding || !key.trim()}
              className="flex-1 py-2.5 text-sm font-medium rounded-[var(--radius-lg)] bg-[var(--text-primary)] text-[var(--text-inverse)] hover:opacity-90 disabled:opacity-40 transition-colors">
              {adding ? t('settings.provider.adding') : t('settings.provider.addProvider')}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
