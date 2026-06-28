import type { modelInfo } from '@janhq/core'

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
