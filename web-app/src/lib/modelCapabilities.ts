import type { modelInfo } from '@janhq/core'

type MaybeSetting = { controller_props?: { value?: unknown }; controllerProps?: { value?: unknown }; value?: unknown }
type CapabilityShape = Record<string, boolean> | string[] | undefined
type MaybeModel = Partial<modelInfo> & {
  settings?: Record<string, MaybeSetting>
  capabilities?: CapabilityShape
  embedding?: boolean
  reranking?: boolean
}

function settingValue(model: MaybeModel, key: string): unknown {
  const setting = model.settings?.[key]
  return setting?.controller_props?.value ?? setting?.controllerProps?.value ?? setting?.value
}

function boolSetting(model: MaybeModel, key: string): boolean {
  const value = settingValue(model, key)
  return value === true || value === 'true'
}

export function capability(model: MaybeModel, ...keys: string[]): boolean {
  const caps = model.capabilities
  if (Array.isArray(caps)) return keys.some((key) => caps.includes(key))
  if (caps && typeof caps === 'object') return keys.some((key) => caps[key] === true)
  return false
}

export function hasCapabilityDisabled(model: MaybeModel, key: string): boolean {
  const caps = model.capabilities
  return !!caps && !Array.isArray(caps) && caps[key] === false
}

export function normalizeCapabilityNames(capabilities: CapabilityShape): string[] {
  const out = new Set<string>()
  if (Array.isArray(capabilities)) {
    for (const cap of capabilities) {
      out.add(cap)
      if (cap === 'embedding') out.add('embeddings')
      if (cap === 'embeddings') out.add('embedding')
      if (cap === 'reranking') out.add('rerank')
      if (cap === 'rerank') out.add('reranking')
    }
  } else if (capabilities && typeof capabilities === 'object') {
    for (const [cap, enabled] of Object.entries(capabilities)) {
      if (!enabled) continue
      out.add(cap)
      if (cap === 'embedding') out.add('embeddings')
      if (cap === 'embeddings') out.add('embedding')
      if (cap === 'reranking') out.add('rerank')
      if (cap === 'rerank') out.add('reranking')
    }
  }
  return Array.from(out)
}

export function isEmbeddingModel(model: MaybeModel): boolean {
  return (
    model.embedding === true ||
    boolSetting(model, 'embedding') ||
    boolSetting(model, 'embeddings') ||
    capability(model, 'embedding', 'embeddings')
  )
}

export function isRerankingModel(model: MaybeModel): boolean {
  return (
    model.reranking === true ||
    boolSetting(model, 'reranking') ||
    boolSetting(model, 'rerank') ||
    capability(model, 'rerank', 'reranking')
  )
}

export function isUtilityOnlyModel(model: MaybeModel): boolean {
  return isEmbeddingModel(model) || isRerankingModel(model)
}

export function isChatCapableModel(model: MaybeModel): boolean {
  return !isUtilityOnlyModel(model) && capability(model, 'chat') !== false && !hasCapabilityDisabled(model, 'chat')
}
