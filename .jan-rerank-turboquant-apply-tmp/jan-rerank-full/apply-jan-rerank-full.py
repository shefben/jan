#!/usr/bin/env python3
from pathlib import Path
import shutil
import sys

ROOT = Path.cwd()
SELF = Path(__file__).resolve().parent


def p(*parts):
    return ROOT.joinpath(*parts)


def read(path):
    return Path(path).read_text(encoding='utf-8')


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding='utf-8')


def require(path):
    if not Path(path).exists():
        raise SystemExit(f"Missing {path}. Run this from the Jan repository root.")


def already_has(s, marker):
    return marker and marker in s


def replace_once(file, needle, replacement, label, marker=None):
    file = Path(file)
    s = read(file)
    if marker and marker in s:
        return
    if needle not in s:
        if replacement.strip()[:80] in s:
            return
        raise SystemExit(f"Could not find patch target in {file}: {label}")
    write(file, s.replace(needle, replacement, 1))


def insert_after(file, needle, insertion, label, marker=None):
    file = Path(file)
    s = read(file)
    if (marker and marker in s) or insertion.strip()[:80] in s:
        return
    idx = s.find(needle)
    if idx < 0:
        raise SystemExit(f"Could not find insertion point in {file}: {label}")
    idx += len(needle)
    write(file, s[:idx] + insertion + s[idx:])


def insert_before(file, needle, insertion, label, marker=None):
    file = Path(file)
    s = read(file)
    if (marker and marker in s) or insertion.strip()[:80] in s:
        return
    idx = s.find(needle)
    if idx < 0:
        raise SystemExit(f"Could not find insertion point in {file}: {label}")
    write(file, s[:idx] + insertion + s[idx:])


required = [
    'extensions/llamacpp-extension/src/util.ts',
    'extensions/llamacpp-extension/src/index.ts',
    'extensions/llamacpp-extension/src/preset.ts',
    'extensions/rag-extension/src/index.ts',
    'src-tauri/plugins/tauri-plugin-llamacpp/guest-js/types.ts',
    'src-tauri/src/core/server/proxy.rs',
    'src-tauri/src/core/server/mod.rs',
]
for rel in required:
    require(p(rel))

# Copy new helper modules.
for rel in [
    'extensions/llamacpp-extension/src/rerank.ts',
    'src-tauri/src/core/server/rerank.rs',
]:
    src = SELF / 'files' / rel
    if not src.exists():
        raise SystemExit(f'Missing bundled file: {src}')
    shutil.copyfile(src, p(rel))

# util.ts
util = p('extensions/llamacpp-extension/src/util.ts')
insert_after(util, '''export function setDefaultEmbeddingModelId(provider: string, modelId: string) {
  try {
    const raw = localStorage.getItem('default-embedding-model')
    const parsed = raw ? JSON.parse(raw) : { state: {}, version: 0 }
    const state = parsed.state ?? {}
    const map = state.defaultByProvider ?? {}
    map[provider] = modelId
    parsed.state = { ...state, defaultByProvider: map }
    if (parsed.version === undefined) parsed.version = 0
    localStorage.setItem('default-embedding-model', JSON.stringify(parsed))
  } catch {
    /* localStorage write failed; non-fatal */
  }
}
''', '''
export function getDefaultRerankingModelId(
  provider: string = 'llamacpp'
): string | undefined {
  try {
    const raw = localStorage.getItem('default-reranking-model')
    if (!raw) return undefined
    const parsed = JSON.parse(raw)
    const map = parsed?.state?.defaultByProvider
    const id = map && map[provider]
    return typeof id === 'string' && id.length > 0 ? id : undefined
  } catch {
    return undefined
  }
}

export function setDefaultRerankingModelId(provider: string, modelId: string) {
  try {
    const raw = localStorage.getItem('default-reranking-model')
    const parsed = raw ? JSON.parse(raw) : { state: {}, version: 0 }
    const state = parsed.state ?? {}
    const map = state.defaultByProvider ?? {}
    map[provider] = modelId
    parsed.state = { ...state, defaultByProvider: map }
    if (parsed.version === undefined) parsed.version = 0
    localStorage.setItem('default-reranking-model', JSON.stringify(parsed))
  } catch {
    /* localStorage write failed; non-fatal */
  }
}
''', 'reranking default helpers', 'getDefaultRerankingModelId')
insert_after(util, '''export function detectEmbeddingFromGgufMeta(
  meta: Record<string, unknown> | undefined
): boolean {
  if (!meta) return false
  const arch = meta['general.architecture']
  if (typeof arch !== 'string') return false
  if (EMBEDDING_GGUF_ARCHS.has(arch)) return true
  if (arch.toLowerCase().includes('embed')) return true
  const raw = meta[`${arch}.pooling_type`]
  const n =
    typeof raw === 'number'
      ? raw
      : typeof raw === 'string' && raw.length > 0
        ? Number(raw)
        : NaN
  return Number.isFinite(n) && n > 0
}
''', r'''

const RERANKING_NAME_RE =
  /(^|[\s._\-/])(?:rerank|reranker|cross[\s._\-]?encoder|bge[\s._\-]?reranker|jina[\s._\-]?reranker|qwen3[\s._\-]?reranker|mxbai[\s._\-]?rerank)([\s._\-/]|$)/i

function asMetaString(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function metaStringHaystack(
  meta: Record<string, unknown> | undefined,
  modelId: string
): string {
  if (!meta) return modelId
  const keys = [
    'general.name',
    'general.basename',
    'general.description',
    'general.source.url',
    'general.url',
    'general.repo_url',
    'tokenizer.ggml.model',
  ]
  return [modelId, ...keys.map((key) => asMetaString(meta[key]))]
    .filter(Boolean)
    .join(' ')
}

function hasExplicitRankPooling(
  meta: Record<string, unknown> | undefined
): boolean {
  if (!meta) return false
  const arch = meta['general.architecture']
  const keys = typeof arch === 'string' ? [`${arch}.pooling_type`] : []
  for (const key of Object.keys(meta)) {
    if (key.endsWith('.pooling_type') && !keys.includes(key)) keys.push(key)
  }
  for (const key of keys) {
    const raw = meta[key]
    if (typeof raw === 'string' && raw.toLowerCase().includes('rank')) return true
  }
  return false
}

export function detectRerankingFromGgufMeta(
  meta: Record<string, unknown> | undefined,
  modelId: string = ''
): boolean {
  const haystack = metaStringHaystack(meta, modelId)
  if (RERANKING_NAME_RE.test(haystack)) return true
  if (hasExplicitRankPooling(meta)) return true
  return false
}
''', 'reranking metadata detection', 'detectRerankingFromGgufMeta')

# types.ts
types = p('src-tauri/plugins/tauri-plugin-llamacpp/guest-js/types.ts')
replace_once(types, '''  is_embedding: boolean
  api_key: string
''', '''  is_embedding: boolean
  is_reranking?: boolean
  api_key: string
''', 'SessionInfo is_reranking', 'is_reranking')
replace_once(types, '''  embedding?: boolean
}

export interface EmbeddingResponse {
''', '''  embedding?: boolean
  reranking?: boolean
  reranking_check_v?: number
  capabilities?: {
    chat?: boolean
    embedding?: boolean
    rerank?: boolean
    [key: string]: boolean | undefined
  }
  preferred_for?: string[]
  score_normalization?: 'none' | 'sigmoid' | 'auto'
  max_tokens_per_doc?: number
  pooling?: 'none' | 'mean' | 'cls' | 'last' | 'rank'
  ubatch_size?: number
  batch_size?: number
}

export type RerankDocument =
  | string
  | {
      text?: string
      content?: string
      page_content?: string
      body?: string
      id?: string
      metadata?: Record<string, unknown>
      [key: string]: unknown
    }

export interface RerankRequest {
  model?: string
  query: string
  documents?: RerankDocument[]
  texts?: RerankDocument[]
  top_n?: number
  top_k?: number
  return_documents?: boolean
  normalize?: boolean
  normalize_scores?: boolean
  raw_scores?: boolean
  max_tokens_per_doc?: number
  min_relevance_score?: number
  evidence_mode?: 'off' | 'top_n' | 'all'
  profile?: 'default' | 'code' | 'multilingual' | 'long'
  [key: string]: unknown
}

export interface RerankResult {
  index: number
  relevance_score: number
  raw_relevance_score?: number
  document?: RerankDocument
  evidence?: string
  contribution?: string
  [key: string]: unknown
}

export interface RerankResponse {
  object?: string
  model: string
  results: RerankResult[]
  usage?: Record<string, number>
  meta?: Record<string, unknown>
  [key: string]: unknown
}

export interface EmbeddingResponse {
''', 'ModelConfig rerank types', 'RerankRequest')

# preset.ts
preset = p('extensions/llamacpp-extension/src/preset.ts')
replace_once(preset, '''  pooling?: 'none' | 'mean' | 'cls' | 'last' | 'rank'
  ubatch_size?: number
''', '''  pooling?: 'none' | 'mean' | 'cls' | 'last' | 'rank'
  reranking?: boolean
  capabilities?: { embedding?: boolean; rerank?: boolean; chat?: boolean }
  preferred_for?: string[]
  score_normalization?: 'none' | 'sigmoid' | 'auto'
  max_tokens_per_doc?: number
  ubatch_size?: number
''', 'ModelYaml rerank fields', 'preferred_for?: string[]')
replace_once(preset, '''): Promise<{ path: string; embeddingCount: number }> {
''', '''): Promise<{ path: string; embeddingCount: number; rerankingCount: number }> {
''', 'preset return type', 'rerankingCount: number')
replace_once(preset, '''  let embeddingCount = 0
  for (const { modelId, configPath } of modelEntries) {
''', '''  let embeddingCount = 0
  let rerankingCount = 0
  for (const { modelId, configPath } of modelEntries) {
''', 'preset reranking count', 'let rerankingCount = 0')
replace_once(preset, '''    if (mc.embedding === true) {
      embeddingCount++
      lines.push('embeddings = true')
      const pooling =
        typeof mc.pooling === 'string' && mc.pooling.length > 0
          ? mc.pooling
          : 'mean'
      lines.push(`pooling = ${escapeIniValue(pooling)}`)
      const ubatch =
        typeof mc.ubatch_size === 'number' && mc.ubatch_size > 0
          ? mc.ubatch_size
          : DEFAULT_EMBEDDING_UBATCH
      const batch =
        typeof mc.batch_size === 'number' && mc.batch_size >= ubatch
          ? mc.batch_size
          : ubatch
      lines.push(`ubatch-size = ${ubatch}`)
      lines.push(`batch-size = ${batch}`)
    }
''', '''    const capabilityRerank = mc.capabilities?.rerank === true
    const isReranker = mc.reranking === true || capabilityRerank
    const needsEmbeddingMode = mc.embedding === true || mc.capabilities?.embedding === true || isReranker

    if (isReranker) rerankingCount++
    else if (mc.embedding === true || mc.capabilities?.embedding === true) embeddingCount++

    if (needsEmbeddingMode) {
      lines.push('embeddings = true')
      const pooling = isReranker
        ? 'rank'
        : typeof mc.pooling === 'string' && mc.pooling.length > 0
          ? mc.pooling
          : 'mean'
      lines.push(`pooling = ${escapeIniValue(pooling)}`)
      if (isReranker) {
        lines.push('reranking = true')
      }
      const ubatch =
        typeof mc.ubatch_size === 'number' && mc.ubatch_size > 0
          ? mc.ubatch_size
          : DEFAULT_EMBEDDING_UBATCH
      const batch =
        typeof mc.batch_size === 'number' && mc.batch_size >= ubatch
          ? mc.batch_size
          : ubatch
      lines.push(`ubatch-size = ${ubatch}`)
      lines.push(`batch-size = ${batch}`)
    }
''', 'preset embedding/reranking section', 'const capabilityRerank')
replace_once(preset, '''  return { path: outPath, embeddingCount }
''', '''  return { path: outPath, embeddingCount, rerankingCount }
''', 'preset return counts', 'return { path: outPath, embeddingCount, rerankingCount }')

# index.ts
index = p('extensions/llamacpp-extension/src/index.ts')
replace_once(index, '''  detectEmbeddingFromGgufMeta,
  detectMtpLayersFromGgufMeta,
  getDefaultEmbeddingModelId,
  setDefaultEmbeddingModelId,
  type EmbedBatchResult,
''', '''  detectEmbeddingFromGgufMeta,
  detectRerankingFromGgufMeta,
  detectMtpLayersFromGgufMeta,
  getDefaultEmbeddingModelId,
  setDefaultEmbeddingModelId,
  getDefaultRerankingModelId,
  setDefaultRerankingModelId,
  type EmbedBatchResult,
''', 'index util imports', 'detectRerankingFromGgufMeta')
insert_after(index, '''} from './preset'
''', '''import {
  normalizeRerankRequest,
  postprocessRerankResponse,
  scoreRerankingModel,
  type RerankProfileName,
} from './rerank'
''', 'index rerank module import', "from './rerank'")
replace_once(index, '''  EmbeddingResponse,
  ModelProps,
''', '''  EmbeddingResponse,
  RerankRequest,
  RerankResponse,
  ModelProps,
''', 'index rerank type imports', 'RerankRequest')
insert_after(index, '''const MTP_CHECK_VERSION = 1
''', '''const RERANKING_CHECK_VERSION = 1
''', 'reranking check version', 'RERANKING_CHECK_VERSION')
replace_once(index, '''    const { path: presetPath, embeddingCount } = await generatePreset(
''', '''    const { path: presetPath, embeddingCount, rerankingCount } = await generatePreset(
''', 'startRouter preset counts', 'rerankingCount } = await generatePreset')
replace_once(index, '''    const embeddingSlotBonus = embeddingCount > 0 ? 1 : 0
    if (modelsMax > 0 && embeddingSlotBonus > 0) {
      modelsMax += embeddingSlotBonus
    }
''', '''    const embeddingSlotBonus = embeddingCount > 0 ? 1 : 0
    const rerankingSlotBonus = rerankingCount > 0 ? 1 : 0
    const utilitySlotBonus = embeddingSlotBonus + rerankingSlotBonus
    if (modelsMax > 0 && utilitySlotBonus > 0) {
      modelsMax += utilitySlotBonus
    }
''', 'utility slot bonus', 'rerankingSlotBonus')
replace_once(index, '''      `Router started on port ${info.port} (pid ${info.pid}, models_max=${modelsMax} [user=${userModelsMax}, +${embeddingSlotBonus} embedding, ${embeddingCount} installed], preset=${presetPath})`
''', '''      `Router started on port ${info.port} (pid ${info.pid}, models_max=${modelsMax} [user=${userModelsMax}, +${embeddingSlotBonus} embedding, ${embeddingCount} installed, +${rerankingSlotBonus} reranking, ${rerankingCount} installed], preset=${presetPath})`
''', 'router log line', '+${rerankingSlotBonus} reranking')
replace_once(index, '''    const isEmbedding = await this.resolveEmbeddingConfig(modelId, modelConfig)
    await this.resolveMtpLayersConfig(modelId, modelConfig)

    return {
''', '''    const isEmbedding = await this.resolveEmbeddingConfig(modelId, modelConfig)
    const isReranking = await this.resolveRerankingConfig(modelId, modelConfig)
    await this.resolveMtpLayersConfig(modelId, modelConfig)

    const capabilities: string[] = []
    if (isEmbedding || isReranking) capabilities.push('embedding')
    if (isReranking) capabilities.push('rerank')

    return {
''', 'get model reranking', 'const isReranking = await this.resolveRerankingConfig(modelId, modelConfig)')
replace_once(index, '''      embedding: isEmbedding,
    } as modelInfo
  }
''', '''      embedding: isEmbedding || isReranking,
      reranking: isReranking,
      capabilities: capabilities.length > 0 ? capabilities : undefined,
    } as modelInfo
  }
''', 'get model capabilities', 'reranking: isReranking')
insert_before(index, '''  private async resolveMtpLayersConfig(
''', '''  private async resolveRerankingConfig(
    modelId: string,
    modelConfig: ModelConfig
  ): Promise<boolean> {
    const cfg = modelConfig as ModelConfig & {
      reranking?: boolean
      reranking_check_v?: number
      capabilities?: { embedding?: boolean; rerank?: boolean; chat?: boolean }
      pooling?: string
      preferred_for?: string[]
      score_normalization?: string
      max_tokens_per_doc?: number
      ubatch_size?: number
      batch_size?: number
    }

    const hasFlag = typeof cfg.reranking === 'boolean' || cfg.capabilities?.rerank === true
    const upToDate = cfg.reranking_check_v === RERANKING_CHECK_VERSION
    if (hasFlag && upToDate) return cfg.reranking === true || cfg.capabilities?.rerank === true
    if (cfg.reranking === true || cfg.capabilities?.rerank === true) return true

    let isReranking = false
    try {
      const janDataFolderPath = await getJanDataFolderPath()
      const fullModelPath = await joinPath([janDataFolderPath, modelConfig.model_path])
      if (await fs.existsSync(fullModelPath)) {
        const metadata = await readGgufMetadata(fullModelPath)
        if (detectRerankingFromGgufMeta(metadata.metadata, modelId)) isReranking = true
      }
    } catch (e) {
      logger.warn(`Failed to check reranking metadata for ${modelId}`, e)
      return cfg.reranking === true || cfg.capabilities?.rerank === true
    }

    try {
      const configPath = await joinPath([await this.getProviderPath(), 'models', modelId, 'model.yml'])
      cfg.reranking = isReranking
      cfg.reranking_check_v = RERANKING_CHECK_VERSION
      cfg.capabilities = { ...(cfg.capabilities ?? {}), ...(isReranking ? { embedding: true, rerank: true } : {}) }
      if (isReranking) {
        cfg.embedding = true
        cfg.pooling = 'rank'
        if (!cfg.ubatch_size) cfg.ubatch_size = 2048
        if (!cfg.batch_size) cfg.batch_size = 2048
      }
      await invoke<void>('write_yaml', { data: cfg, savePath: configPath })
    } catch (e) {
      logger.warn(`Failed to update reranking config for ${modelId}`, e)
    }

    return isReranking
  }

''', 'resolveRerankingConfig', 'private async resolveRerankingConfig')
replace_once(index, '''        const isEmbedding = await this.resolveEmbeddingConfig(
          modelId,
          modelConfig
        )

        const capabilities: string[] = []
''', '''        const isEmbedding = await this.resolveEmbeddingConfig(
          modelId,
          modelConfig
        )
        const isReranking = await this.resolveRerankingConfig(
          modelId,
          modelConfig
        )

        const capabilities: string[] = []
        if (isEmbedding || isReranking) capabilities.push('embedding')
        if (isReranking) capabilities.push('rerank')
''', 'list reranking detection', 'const isReranking = await this.resolveRerankingConfig(\n          modelId,\n          modelConfig\n        )')
replace_once(index, '''          embedding: isEmbedding,
          imported: isAbsolute,
''', '''          embedding: isEmbedding || isReranking,
          reranking: isReranking,
          imported: isAbsolute,
''', 'list model reranking fields', 'reranking: isReranking')
replace_once(index, '''    let isEmbedding = false
    let mtpLayers = 0
''', '''    let isEmbedding = false
    let isReranking = false
    let mtpLayers = 0
''', 'import isReranking var', 'let isReranking = false')
replace_once(index, '''      if (detectEmbeddingFromGgufMeta(modelMetadata.metadata)) {
        isEmbedding = true
      }
      mtpLayers = detectMtpLayersFromGgufMeta(modelMetadata.metadata)
''', '''      if (detectEmbeddingFromGgufMeta(modelMetadata.metadata)) {
        isEmbedding = true
      }
      if (detectRerankingFromGgufMeta(modelMetadata.metadata, modelId)) {
        isReranking = true
        isEmbedding = true
      }
      mtpLayers = detectMtpLayersFromGgufMeta(modelMetadata.metadata)
''', 'import detect reranking', 'isReranking = true')
replace_once(index, '''      embedding: isEmbedding,
      embedding_check_v: EMBEDDING_CHECK_VERSION,
      mtp_layers: mtpLayers,
''', '''      embedding: isEmbedding,
      reranking: isReranking,
      capabilities: isReranking ? { embedding: true, rerank: true } : isEmbedding ? { embedding: true } : undefined,
      embedding_check_v: EMBEDDING_CHECK_VERSION,
      reranking_check_v: RERANKING_CHECK_VERSION,
      mtp_layers: mtpLayers,
''', 'import model config reranking', 'reranking_check_v')
replace_once(index, '''      ...(isEmbedding
        ? { pooling: 'mean', ubatch_size: 2048, batch_size: 2048 }
        : {}),
''', '''      ...(isReranking
        ? { pooling: 'rank', ubatch_size: 2048, batch_size: 2048, preferred_for: ['default'] }
        : isEmbedding
          ? { pooling: 'mean', ubatch_size: 2048, batch_size: 2048 }
          : {}),
''', 'import pooling', "pooling: 'rank'")
insert_before(index, '''  /**
   * Check if a tool is supported by the model
''', '''  private async installedRerankingModels(): Promise<modelInfo[]> {
    const downloadedModelList = await this.list()
    return downloadedModelList.filter(
      (m) =>
        (m as any).reranking === true ||
        (Array.isArray((m as any).capabilities) &&
          (m as any).capabilities.includes('rerank'))
    )
  }

  private async selectRerankingModel(req: ReturnType<typeof normalizeRerankRequest>): Promise<modelInfo> {
    const installed = await this.installedRerankingModels()
    const loadedIds = new Set(await this.getLoadedModels().catch(() => [] as string[]))
    const explicitModel = req.model && req.model !== 'auto' ? req.model : undefined
    const storedDefault = getDefaultRerankingModelId('llamacpp')
    const preferred = explicitModel ?? storedDefault

    if (explicitModel) {
      const match = installed.find((m) => m.id === explicitModel)
      if (!match) throw new Error(`Requested reranking model "${explicitModel}" is not installed or is not marked reranking=true`)
      return match
    }

    if (!storedDefault && installed.length === 1) {
      setDefaultRerankingModelId('llamacpp', installed[0].id)
      logger.info(`Auto-promoted "${installed[0].id}" as default reranking model (single installed reranker)`)
    }

    if (installed.length === 0) {
      throw new Error('No reranking model is installed. Import a GGUF reranker or set reranking: true with pooling: rank in model.yml.')
    }

    const profile = req.profile as RerankProfileName
    return installed
      .map((m) => ({ model: m, score: scoreRerankingModel(m, profile, loadedIds, preferred) }))
      .sort((a, b) => b.score - a.score)[0].model
  }

  async rerank(req: RerankRequest): Promise<RerankResponse> {
    const started = performance.now()
    const normalized = normalizeRerankRequest(req)
    const model = await this.selectRerankingModel(normalized)
    const targetModelId = model.id

    let modelLoadMs = 0
    let sInfo = await this.findSessionByModel(targetModelId)
    const cacheHit = !!sInfo
    if (!sInfo) {
      const loadStarted = performance.now()
      sInfo = await this.load(targetModelId, undefined, true)
      modelLoadMs = Math.round(performance.now() - loadStarted)
    }

    const response = await fetch(`http://localhost:${sInfo.port}/v1/rerank`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${sInfo.api_key}`,
      },
      body: JSON.stringify({
        ...req,
        model: targetModelId,
        documents: normalized.documents,
        top_n: normalized.top_n,
        return_documents: false,
      }),
    })

    if (!response.ok) {
      const errorData = await response.json().catch(() => null)
      throw new Error(`Rerank request failed with status ${response.status}: ${JSON.stringify(errorData)}`)
    }

    const raw = await response.json()
    return postprocessRerankResponse(raw, normalized, {
      model: targetModelId,
      provider: 'local_gguf',
      fallback_used: false,
      latency_ms: Math.round(performance.now() - started),
      model_load_ms: modelLoadMs,
      cache_hit: cacheHit,
    }) as RerankResponse
  }

  async getRerankStatus(): Promise<Record<string, unknown>> {
    const installed = await this.installedRerankingModels()
    const loaded = new Set(await this.getLoadedModels().catch(() => [] as string[]))
    return {
      enabled: installed.length > 0,
      selected_model: getDefaultRerankingModelId('llamacpp') ?? 'auto',
      available_models: installed.map((m) => ({
        id: m.id,
        name: m.name,
        loaded: loaded.has(m.id),
        sizeBytes: m.sizeBytes,
        capabilities: (m as any).capabilities,
      })),
    }
  }

''', 'rerank methods', 'async rerank(req: RerankRequest)')

# server mod.rs
modrs = p('src-tauri/src/core/server/mod.rs')
insert_after(modrs, 'pub mod proxy;\n', 'pub mod rerank;\n', 'server rerank mod', 'pub mod rerank;')

# proxy.rs
proxy = p('src-tauri/src/core/server/proxy.rs')
insert_after(proxy, '''use crate::core::{
    mcp::models::McpSettings,
    state::{ProviderConfig, ServerHandle, SharedMcpServers},
};
''', '''use crate::core::server::rerank::{
    build_rerank_status_json, is_rerank_status_path, postprocess_rerank_response,
    prepare_rerank_request, record_rerank_observation, rerank_error_json,
};
''', 'proxy rerank imports', 'build_rerank_status_json')
insert_after(proxy, '''    match (method.clone(), destination_path.as_str()) {
''', '''        (hyper::Method::GET, path) if is_rerank_status_path(path) => {
            let status_json = build_rerank_status_json(&jan_data_folder, &llama_state, &client).await;
            let mut response_builder = Response::builder()
                .status(StatusCode::OK)
                .header(hyper::header::CONTENT_TYPE, "application/json");
            response_builder = add_cors_headers_with_host_and_origin(
                response_builder,
                &host_header,
                &origin_header,
                &config.trusted_hosts,
            );
            return Ok(response_builder.body(full(status_json.to_string())).unwrap());
        }
        (hyper::Method::POST, "/rerank") | (hyper::Method::POST, "/reranking") => {
            let started = std::time::Instant::now();
            let body_bytes = match body.collect().await {
                Ok(c) => c.to_bytes(),
                Err(_) => {
                    let mut error_response = Response::builder().status(StatusCode::INTERNAL_SERVER_ERROR);
                    error_response = add_cors_headers_with_host_and_origin(
                        error_response,
                        &host_header,
                        &origin_header,
                        &config.trusted_hosts,
                    );
                    return Ok(error_response.body(full("Failed to read request body")).unwrap());
                }
            };
            let prepared = match prepare_rerank_request(body_bytes, &jan_data_folder, &client, &llama_state).await {
                Ok(v) => v,
                Err(e) => {
                    let mut error_response = Response::builder().status(e.status);
                    error_response = add_cors_headers_with_host_and_origin(
                        error_response,
                        &host_header,
                        &origin_header,
                        &config.trusted_hosts,
                    );
                    return Ok(error_response
                        .header(hyper::header::CONTENT_TYPE, "application/json")
                        .body(full(rerank_error_json(&e.kind, &e.message)))
                        .unwrap());
                }
            };
            let (upstream_url, api_key) = if let Some(ext) = prepared.external.clone() {
                let base = ext.base_url.trim_end_matches('/').to_string();
                (format!("{base}/rerank"), ext.api_key)
            } else if let Some((url, key)) = router_upstream(&llama_state, destination_path.as_str()).await {
                (url, Some(key))
            } else {
                let mut error_response = Response::builder().status(StatusCode::SERVICE_UNAVAILABLE);
                error_response = add_cors_headers_with_host_and_origin(
                    error_response,
                    &host_header,
                    &origin_header,
                    &config.trusted_hosts,
                );
                return Ok(error_response
                    .header(hyper::header::CONTENT_TYPE, "application/json")
                    .body(full(rerank_error_json("router_unavailable", "llama.cpp router is not running")))
                    .unwrap());
            };
            let mut req_out = client
                .post(&upstream_url)
                .header("Content-Type", "application/json")
                .body(prepared.body.clone());
            if let Some(key) = api_key {
                req_out = req_out.header("Authorization", format!("Bearer {key}"));
            }
            let upstream = match req_out.send().await {
                Ok(v) => v,
                Err(e) => {
                    let mut error_response = Response::builder().status(StatusCode::BAD_GATEWAY);
                    error_response = add_cors_headers_with_host_and_origin(
                        error_response,
                        &host_header,
                        &origin_header,
                        &config.trusted_hosts,
                    );
                    return Ok(error_response
                        .header(hyper::header::CONTENT_TYPE, "application/json")
                        .body(full(rerank_error_json("upstream_error", &format!("Rerank upstream request failed: {e}"))))
                        .unwrap());
                }
            };
            let status = upstream.status();
            let text = upstream.text().await.unwrap_or_default();
            if !status.is_success() {
                let mut error_response = Response::builder().status(status);
                error_response = add_cors_headers_with_host_and_origin(
                    error_response,
                    &host_header,
                    &origin_header,
                    &config.trusted_hosts,
                );
                return Ok(error_response
                    .header(hyper::header::CONTENT_TYPE, "application/json")
                    .body(full(text))
                    .unwrap());
            }
            let raw_json: serde_json::Value = match serde_json::from_str(&text) {
                Ok(v) => v,
                Err(e) => {
                    let mut error_response = Response::builder().status(StatusCode::BAD_GATEWAY);
                    error_response = add_cors_headers_with_host_and_origin(
                        error_response,
                        &host_header,
                        &origin_header,
                        &config.trusted_hosts,
                    );
                    return Ok(error_response
                        .header(hyper::header::CONTENT_TYPE, "application/json")
                        .body(full(rerank_error_json("invalid_upstream_response", &format!("Invalid rerank JSON from upstream: {e}"))))
                        .unwrap());
                }
            };
            let out = postprocess_rerank_response(raw_json, prepared.trace, started.elapsed().as_millis() as u64);
            if let Some(meta) = out.get("meta") {
                record_rerank_observation(meta.clone()).await;
            }
            let mut response_builder = Response::builder()
                .status(StatusCode::OK)
                .header(hyper::header::CONTENT_TYPE, "application/json");
            response_builder = add_cors_headers_with_host_and_origin(
                response_builder,
                &host_header,
                &origin_header,
                &config.trusted_hosts,
            );
            return Ok(response_builder.body(full(out.to_string())).unwrap());
        }
''', 'proxy rerank routes', 'is_rerank_status_path(path)')

# RAG extension
rag = p('extensions/rag-extension/src/index.ts')
replace_once(rag, '''    autoInlineContextRatio: 0.75,
  }
''', '''    autoInlineContextRatio: 0.75,
    rerankingMode: 'auto' as 'auto' | 'off' | 'model',
    rerankingModel: 'auto',
    rerankTopKBefore: 60,
    rerankTopNAfter: 8,
    rerankMinRelevanceScore: 0,
    rerankMaxTokensPerDoc: 4096,
    rerankEvidenceMode: 'off' as 'off' | 'top_n' | 'all',
  }
''', 'rag config', 'rerankingMode')
insert_after(rag, '''    this.config.autoInlineContextRatio = await this.getSetting(
      'auto_inline_context_ratio',
      this.config.autoInlineContextRatio
    )
''', '''    this.config.rerankingMode = await this.getSetting('reranking_mode', this.config.rerankingMode)
    this.config.rerankingModel = await this.getSetting('reranking_model', this.config.rerankingModel)
    this.config.rerankTopKBefore = await this.getSetting('rerank_top_k_before', this.config.rerankTopKBefore)
    this.config.rerankTopNAfter = await this.getSetting('rerank_top_n_after', this.config.rerankTopNAfter)
    this.config.rerankMinRelevanceScore = await this.getSetting('rerank_min_relevance_score', this.config.rerankMinRelevanceScore)
    this.config.rerankMaxTokensPerDoc = await this.getSetting('rerank_max_tokens_per_doc', this.config.rerankMaxTokensPerDoc)
    this.config.rerankEvidenceMode = await this.getSetting('rerank_evidence_mode', this.config.rerankEvidenceMode)
''', 'rag configure reranking', 'rerank_top_k_before')
replace_once(rag, '''    const topK = (args['top_k'] as number) || s.retrievalLimit || 3
    const threshold = s.retrievalThreshold ?? 0.3
''', '''    const requestedTopK = (args['top_k'] as number) || s.retrievalLimit || 3
    const shouldRerank = s.rerankingMode !== 'off'
    const topK = shouldRerank ? Math.max(requestedTopK, s.rerankTopKBefore || 60) : requestedTopK
    const threshold = shouldRerank ? Math.min(s.retrievalThreshold ?? 0.3, 0.05) : (s.retrievalThreshold ?? 0.3)
''', 'rag topK reranking', 'const shouldRerank')
replace_once(rag, '''      const payload = {
        thread_id: threadId,
        project_id: projectId,
        scope,
        query,
        citations:
          results?.map((r: any) => ({
            id: r.id,
            text: r.text,
            score: r.score,
            file_id: r.file_id,
            chunk_file_order: r.chunk_file_order,
          })) ?? [],
        mode,
      }
''', '''      let citations =
        results?.map((r: any) => ({
          id: r.id,
          text: r.text,
          score: r.score,
          file_id: r.file_id,
          chunk_file_order: r.chunk_file_order,
        })) ?? []
      let reranking: Record<string, unknown> = { enabled: false }
      if (shouldRerank && citations.length > 1) {
        const reranked = await this.rerankCitations(query, citations, requestedTopK)
        citations = reranked.citations
        reranking = reranked.meta
      } else {
        citations = citations.slice(0, requestedTopK)
      }
      const payload = {
        thread_id: threadId,
        project_id: projectId,
        scope,
        query,
        citations,
        mode,
        reranking,
      }
''', 'rag payload reranking', 'let reranking: Record<string, unknown>')
insert_before(rag, '''  onSettingUpdate<T>(key: string, value: T): void {
''', '''  private async rerankCitations(
    query: string,
    citations: Array<Record<string, unknown>>,
    requestedTopK: number
  ): Promise<{ citations: Array<Record<string, unknown>>; meta: Record<string, unknown> }> {
    const llm = window.core?.extensionManager.getByName(
      '@janhq/llamacpp-extension'
    ) as AIEngine & {
      rerank?: (req: any) => Promise<{ results: Array<{ index: number; relevance_score: number; evidence?: string; contribution?: string }>; meta?: Record<string, unknown> }>
    }
    if (!llm?.rerank) return { citations: citations.slice(0, requestedTopK), meta: { enabled: false, reason: 'llamacpp extension has no rerank method' } }
    try {
      const topN = Math.max(1, Math.min(citations.length, this.config.rerankTopNAfter || requestedTopK))
      const response = await llm.rerank({
        model: this.config.rerankingMode === 'model' ? this.config.rerankingModel : 'auto',
        query,
        documents: citations.map((c, index) => ({
          text: String(c.text ?? ''),
          metadata: {
            index,
            id: c.id,
            file_id: c.file_id,
            chunk_file_order: c.chunk_file_order,
            vector_score: c.score,
          },
        })),
        top_n: topN,
        return_documents: true,
        min_relevance_score: this.config.rerankMinRelevanceScore || undefined,
        max_tokens_per_doc: this.config.rerankMaxTokensPerDoc || 4096,
        evidence_mode: this.config.rerankEvidenceMode,
      })
      const reranked = response.results
        .map((r) => ({
          ...citations[r.index],
          rerank_score: r.relevance_score,
          evidence: r.evidence,
          contribution: r.contribution,
        }))
        .filter((c) => c.text)
      return { citations: reranked, meta: { enabled: true, ...(response.meta ?? {}) } }
    } catch (e) {
      console.warn('[RAG] Reranking failed, falling back to vector order:', e)
      return {
        citations: citations.slice(0, requestedTopK),
        meta: {
          enabled: false,
          fallback_used: true,
          error: e instanceof Error ? e.message : String(e),
        },
      }
    }
  }

''', 'rag rerankCitations method', 'private async rerankCitations')
insert_after(rag, '''      case 'parse_mode':
        this.config.parseMode = String(value) as
          | 'auto'
          | 'inline'
          | 'embeddings'
          | 'prompt'
        break
''', '''      case 'reranking_mode':
        this.config.rerankingMode = String(value) as 'auto' | 'off' | 'model'
        break
      case 'reranking_model':
        this.config.rerankingModel = String(value)
        break
      case 'rerank_top_k_before':
        this.config.rerankTopKBefore = Number(value)
        break
      case 'rerank_top_n_after':
        this.config.rerankTopNAfter = Number(value)
        break
      case 'rerank_min_relevance_score':
        this.config.rerankMinRelevanceScore = Number(value)
        break
      case 'rerank_max_tokens_per_doc':
        this.config.rerankMaxTokensPerDoc = Number(value)
        break
      case 'rerank_evidence_mode':
        this.config.rerankEvidenceMode = String(value) as 'off' | 'top_n' | 'all'
        break
''', 'rag onSettingUpdate reranking', 'case \'reranking_mode\'')

print('Jan reranking full implementation applied. Run typecheck/build/tests now; software will not validate itself out of pity.')
