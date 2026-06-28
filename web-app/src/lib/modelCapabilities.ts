import type { modelInfo } from '@janhq/core'

// local-ai-stack isReranking helper
export const isReranking = (model: any): boolean => {
  const value = model ?? {}
  const metadata = value.metadata ?? value.meta ?? {}
  const lower = (input: unknown): string => String(input ?? '').toLowerCase()
  const hasCap = (capabilities: unknown): boolean => {
    if (!Array.isArray(capabilities)) return false
    return capabilities.some((cap) => {
      const name = lower(cap)
      return name === 'rerank' || name === 'reranking' || name === 'rank'
    })
  }

  return Boolean(
    value.reranking === true ||
      value.isReranking === true ||
      value.is_reranking === true ||
      metadata.reranking === true ||
      metadata.isReranking === true ||
      metadata.is_reranking === true ||
      lower(value.type) === 'reranker' ||
      lower(value.model_type) === 'reranker' ||
      lower(metadata.type) === 'reranker' ||
      lower(metadata.model_type) === 'reranker' ||
      lower(value.id).includes('rerank') ||
      lower(value.name).includes('rerank') ||
      lower(value.model).includes('rerank') ||
      hasCap(value.capabilities) ||
      hasCap(metadata.capabilities)
  )
}

type MaybeSetting = { controller_props?: { value?: unknown }; controllerProps?: { value?: unknown }; value?: unknown }
type MaybeModel = Partial<modelInfo> & { settings?: Record<string, MaybeSetting>; capabilities?: Record<string, boolean> | string[]; embedding?: boolean; reranking?: boolean }

function settingValue(model: MaybeModel, key: string): unknown {
  const setting = model.settings?.[key]
  return setting?.controller_props?.value ?? setting?.controllerProps?.value ?? setting?.value
}

function capability(model: MaybeModel, key: string): boolean {
  const caps = model.capabilities
  if (Array.isArray(caps)) return caps.includes(key)
  if (caps && typeof caps === 'object') return caps[key] === true
  return false
}

export function isEmbeddingModel(model: MaybeModel): boolean {
  return model.embedding === true || settingValue(model, 'embedding') === true || capability(model, 'embedding')
}

export function isRerankingModel(model: MaybeModel): boolean {
  return model.reranking === true || settingValue(model, 'reranking') === true || capability(model, 'rerank')
}

export function isUtilityOnlyModel(model: MaybeModel): boolean {
  return isEmbeddingModel(model) || isRerankingModel(model)
}

export function isChatCapableModel(model: MaybeModel): boolean {
  return !isUtilityOnlyModel(model) && capability(model, 'chat') !== false
}
