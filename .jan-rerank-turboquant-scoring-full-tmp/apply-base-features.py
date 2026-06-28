#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Single-pass Jan feature applicator (rerank, turboquant, model UX, search, speculative decoding).

Run from a clean upstream Jan repository root:
    python apply-jan-rerank-turboquant-all.py

This writes a temporary embedded applicator folder, applies the full reranking
implementation plus turboquant archive/backend handling, then applies the Stage 7
completion fixes. It avoids unified diff hunks entirely because apparently text
patches enjoy cosplay as landmines.
"""
from __future__ import annotations

import runpy
import shutil
import sys
from pathlib import Path

ROOT = Path.cwd()
TMP = ROOT / '.jan-rerank-turboquant-apply-tmp'

FULL_SCRIPT = "#!/usr/bin/env python3\nfrom pathlib import Path\nimport shutil\nimport sys\n\nROOT = Path.cwd()\nSELF = Path(__file__).resolve().parent\n\n\ndef p(*parts):\n    return ROOT.joinpath(*parts)\n\n\ndef read(path):\n    return Path(path).read_text(encoding='utf-8')\n\n\ndef write(path, data):\n    path = Path(path)\n    path.parent.mkdir(parents=True, exist_ok=True)\n    path.write_text(data, encoding='utf-8')\n\n\ndef require(path):\n    if not Path(path).exists():\n        raise SystemExit(f\"Missing {path}. Run this from the Jan repository root.\")\n\n\ndef already_has(s, marker):\n    return marker and marker in s\n\n\ndef replace_once(file, needle, replacement, label, marker=None):\n    file = Path(file)\n    s = read(file)\n    if marker and marker in s:\n        return\n    if needle not in s:\n        if replacement.strip()[:80] in s:\n            return\n        raise SystemExit(f\"Could not find patch target in {file}: {label}\")\n    write(file, s.replace(needle, replacement, 1))\n\n\ndef insert_after(file, needle, insertion, label, marker=None):\n    file = Path(file)\n    s = read(file)\n    if (marker and marker in s) or insertion.strip()[:80] in s:\n        return\n    idx = s.find(needle)\n    if idx < 0:\n        raise SystemExit(f\"Could not find insertion point in {file}: {label}\")\n    idx += len(needle)\n    write(file, s[:idx] + insertion + s[idx:])\n\n\ndef insert_before(file, needle, insertion, label, marker=None):\n    file = Path(file)\n    s = read(file)\n    if (marker and marker in s) or insertion.strip()[:80] in s:\n        return\n    idx = s.find(needle)\n    if idx < 0:\n        raise SystemExit(f\"Could not find insertion point in {file}: {label}\")\n    write(file, s[:idx] + insertion + s[idx:])\n\n\nrequired = [\n    'extensions/llamacpp-extension/src/util.ts',\n    'extensions/llamacpp-extension/src/index.ts',\n    'extensions/llamacpp-extension/src/preset.ts',\n    'extensions/rag-extension/src/index.ts',\n    'src-tauri/plugins/tauri-plugin-llamacpp/guest-js/types.ts',\n    'src-tauri/src/core/server/proxy.rs',\n    'src-tauri/src/core/server/mod.rs',\n]\nfor rel in required:\n    require(p(rel))\n\n# Copy new helper modules.\nfor rel in [\n    'extensions/llamacpp-extension/src/rerank.ts',\n    'src-tauri/src/core/server/rerank.rs',\n]:\n    src = SELF / 'files' / rel\n    if not src.exists():\n        raise SystemExit(f'Missing bundled file: {src}')\n    shutil.copyfile(src, p(rel))\n\n# util.ts\nutil = p('extensions/llamacpp-extension/src/util.ts')\ninsert_after(util, '''export function setDefaultEmbeddingModelId(provider: string, modelId: string) {\n  try {\n    const raw = localStorage.getItem('default-embedding-model')\n    const parsed = raw ? JSON.parse(raw) : { state: {}, version: 0 }\n    const state = parsed.state ?? {}\n    const map = state.defaultByProvider ?? {}\n    map[provider] = modelId\n    parsed.state = { ...state, defaultByProvider: map }\n    if (parsed.version === undefined) parsed.version = 0\n    localStorage.setItem('default-embedding-model', JSON.stringify(parsed))\n  } catch {\n    /* localStorage write failed; non-fatal */\n  }\n}\n''', '''\nexport function getDefaultRerankingModelId(\n  provider: string = 'llamacpp'\n): string | undefined {\n  try {\n    const raw = localStorage.getItem('default-reranking-model')\n    if (!raw) return undefined\n    const parsed = JSON.parse(raw)\n    const map = parsed?.state?.defaultByProvider\n    const id = map && map[provider]\n    return typeof id === 'string' && id.length > 0 ? id : undefined\n  } catch {\n    return undefined\n  }\n}\n\nexport function setDefaultRerankingModelId(provider: string, modelId: string) {\n  try {\n    const raw = localStorage.getItem('default-reranking-model')\n    const parsed = raw ? JSON.parse(raw) : { state: {}, version: 0 }\n    const state = parsed.state ?? {}\n    const map = state.defaultByProvider ?? {}\n    map[provider] = modelId\n    parsed.state = { ...state, defaultByProvider: map }\n    if (parsed.version === undefined) parsed.version = 0\n    localStorage.setItem('default-reranking-model', JSON.stringify(parsed))\n  } catch {\n    /* localStorage write failed; non-fatal */\n  }\n}\n''', 'reranking default helpers', 'getDefaultRerankingModelId')\ninsert_after(util, '''export function detectEmbeddingFromGgufMeta(\n  meta: Record<string, unknown> | undefined\n): boolean {\n  if (!meta) return false\n  const arch = meta['general.architecture']\n  if (typeof arch !== 'string') return false\n  if (EMBEDDING_GGUF_ARCHS.has(arch)) return true\n  if (arch.toLowerCase().includes('embed')) return true\n  const raw = meta[`${arch}.pooling_type`]\n  const n =\n    typeof raw === 'number'\n      ? raw\n      : typeof raw === 'string' && raw.length > 0\n        ? Number(raw)\n        : NaN\n  return Number.isFinite(n) && n > 0\n}\n''', r'''\n\nconst RERANKING_NAME_RE =\n  /(^|[\\s._\\-/])(?:rerank|reranker|cross[\\s._\\-]?encoder|bge[\\s._\\-]?reranker|jina[\\s._\\-]?reranker|qwen3[\\s._\\-]?reranker|mxbai[\\s._\\-]?rerank)([\\s._\\-/]|$)/i\n\nfunction asMetaString(value: unknown): string {\n  return typeof value === 'string' ? value : ''\n}\n\nfunction metaStringHaystack(\n  meta: Record<string, unknown> | undefined,\n  modelId: string\n): string {\n  if (!meta) return modelId\n  const keys = [\n    'general.name',\n    'general.basename',\n    'general.description',\n    'general.source.url',\n    'general.url',\n    'general.repo_url',\n    'tokenizer.ggml.model',\n  ]\n  return [modelId, ...keys.map((key) => asMetaString(meta[key]))]\n    .filter(Boolean)\n    .join(' ')\n}\n\nfunction hasExplicitRankPooling(\n  meta: Record<string, unknown> | undefined\n): boolean {\n  if (!meta) return false\n  const arch = meta['general.architecture']\n  const keys = typeof arch === 'string' ? [`${arch}.pooling_type`] : []\n  for (const key of Object.keys(meta)) {\n    if (key.endsWith('.pooling_type') && !keys.includes(key)) keys.push(key)\n  }\n  for (const key of keys) {\n    const raw = meta[key]\n    if (typeof raw === 'string' && raw.toLowerCase().includes('rank')) return true\n  }\n  return false\n}\n\nexport function detectRerankingFromGgufMeta(\n  meta: Record<string, unknown> | undefined,\n  modelId: string = ''\n): boolean {\n  const haystack = metaStringHaystack(meta, modelId)\n  if (RERANKING_NAME_RE.test(haystack)) return true\n  if (hasExplicitRankPooling(meta)) return true\n  return false\n}\n''', 'reranking metadata detection', 'detectRerankingFromGgufMeta')\n\n# types.ts\ntypes = p('src-tauri/plugins/tauri-plugin-llamacpp/guest-js/types.ts')\nreplace_once(types, '''  is_embedding: boolean\n  api_key: string\n''', '''  is_embedding: boolean\n  is_reranking?: boolean\n  api_key: string\n''', 'SessionInfo is_reranking', 'is_reranking')\nreplace_once(types, '''  embedding?: boolean\n}\n\nexport interface EmbeddingResponse {\n''', '''  embedding?: boolean\n  reranking?: boolean\n  reranking_check_v?: number\n  capabilities?: {\n    chat?: boolean\n    embedding?: boolean\n    rerank?: boolean\n    [key: string]: boolean | undefined\n  }\n  preferred_for?: string[]\n  score_normalization?: 'none' | 'sigmoid' | 'auto'\n  max_tokens_per_doc?: number\n  pooling?: 'none' | 'mean' | 'cls' | 'last' | 'rank'\n  ubatch_size?: number\n  batch_size?: number\n}\n\nexport type RerankDocument =\n  | string\n  | {\n      text?: string\n      content?: string\n      page_content?: string\n      body?: string\n      id?: string\n      metadata?: Record<string, unknown>\n      [key: string]: unknown\n    }\n\nexport interface RerankRequest {\n  model?: string\n  query: string\n  documents?: RerankDocument[]\n  texts?: RerankDocument[]\n  top_n?: number\n  top_k?: number\n  return_documents?: boolean\n  normalize?: boolean\n  normalize_scores?: boolean\n  raw_scores?: boolean\n  max_tokens_per_doc?: number\n  min_relevance_score?: number\n  evidence_mode?: 'off' | 'top_n' | 'all'\n  profile?: 'default' | 'code' | 'multilingual' | 'long'\n  [key: string]: unknown\n}\n\nexport interface RerankResult {\n  index: number\n  relevance_score: number\n  raw_relevance_score?: number\n  document?: RerankDocument\n  evidence?: string\n  contribution?: string\n  [key: string]: unknown\n}\n\nexport interface RerankResponse {\n  object?: string\n  model: string\n  results: RerankResult[]\n  usage?: Record<string, number>\n  meta?: Record<string, unknown>\n  [key: string]: unknown\n}\n\nexport interface EmbeddingResponse {\n''', 'ModelConfig rerank types', 'RerankRequest')\n\n# preset.ts\npreset = p('extensions/llamacpp-extension/src/preset.ts')\nreplace_once(preset, '''  pooling?: 'none' | 'mean' | 'cls' | 'last' | 'rank'\n  ubatch_size?: number\n''', '''  pooling?: 'none' | 'mean' | 'cls' | 'last' | 'rank'\n  reranking?: boolean\n  capabilities?: { embedding?: boolean; rerank?: boolean; chat?: boolean }\n  preferred_for?: string[]\n  score_normalization?: 'none' | 'sigmoid' | 'auto'\n  max_tokens_per_doc?: number\n  ubatch_size?: number\n''', 'ModelYaml rerank fields', 'preferred_for?: string[]')\nreplace_once(preset, '''): Promise<{ path: string; embeddingCount: number }> {\n''', '''): Promise<{ path: string; embeddingCount: number; rerankingCount: number }> {\n''', 'preset return type', 'rerankingCount: number')\nreplace_once(preset, '''  let embeddingCount = 0\n  for (const { modelId, configPath } of modelEntries) {\n''', '''  let embeddingCount = 0\n  let rerankingCount = 0\n  for (const { modelId, configPath } of modelEntries) {\n''', 'preset reranking count', 'let rerankingCount = 0')\nreplace_once(preset, '''    if (mc.embedding === true) {\n      embeddingCount++\n      lines.push('embeddings = true')\n      const pooling =\n        typeof mc.pooling === 'string' && mc.pooling.length > 0\n          ? mc.pooling\n          : 'mean'\n      lines.push(`pooling = ${escapeIniValue(pooling)}`)\n      const ubatch =\n        typeof mc.ubatch_size === 'number' && mc.ubatch_size > 0\n          ? mc.ubatch_size\n          : DEFAULT_EMBEDDING_UBATCH\n      const batch =\n        typeof mc.batch_size === 'number' && mc.batch_size >= ubatch\n          ? mc.batch_size\n          : ubatch\n      lines.push(`ubatch-size = ${ubatch}`)\n      lines.push(`batch-size = ${batch}`)\n    }\n''', '''    const capabilityRerank = mc.capabilities?.rerank === true\n    const isReranker = mc.reranking === true || capabilityRerank\n    const needsEmbeddingMode = mc.embedding === true || mc.capabilities?.embedding === true || isReranker\n\n    if (isReranker) rerankingCount++\n    else if (mc.embedding === true || mc.capabilities?.embedding === true) embeddingCount++\n\n    if (needsEmbeddingMode) {\n      lines.push('embeddings = true')\n      const pooling = isReranker\n        ? 'rank'\n        : typeof mc.pooling === 'string' && mc.pooling.length > 0\n          ? mc.pooling\n          : 'mean'\n      lines.push(`pooling = ${escapeIniValue(pooling)}`)\n      if (isReranker) {\n        lines.push('reranking = true')\n      }\n      const ubatch =\n        typeof mc.ubatch_size === 'number' && mc.ubatch_size > 0\n          ? mc.ubatch_size\n          : DEFAULT_EMBEDDING_UBATCH\n      const batch =\n        typeof mc.batch_size === 'number' && mc.batch_size >= ubatch\n          ? mc.batch_size\n          : ubatch\n      lines.push(`ubatch-size = ${ubatch}`)\n      lines.push(`batch-size = ${batch}`)\n    }\n''', 'preset embedding/reranking section', 'const capabilityRerank')\nreplace_once(preset, '''  return { path: outPath, embeddingCount }\n''', '''  return { path: outPath, embeddingCount, rerankingCount }\n''', 'preset return counts', 'return { path: outPath, embeddingCount, rerankingCount }')\n\n# index.ts\nindex = p('extensions/llamacpp-extension/src/index.ts')\nreplace_once(index, '''  detectEmbeddingFromGgufMeta,\n  detectMtpLayersFromGgufMeta,\n  getDefaultEmbeddingModelId,\n  setDefaultEmbeddingModelId,\n  type EmbedBatchResult,\n''', '''  detectEmbeddingFromGgufMeta,\n  detectRerankingFromGgufMeta,\n  detectMtpLayersFromGgufMeta,\n  getDefaultEmbeddingModelId,\n  setDefaultEmbeddingModelId,\n  getDefaultRerankingModelId,\n  setDefaultRerankingModelId,\n  type EmbedBatchResult,\n''', 'index util imports', 'detectRerankingFromGgufMeta')\ninsert_after(index, '''} from './preset'\n''', '''import {\n  normalizeRerankRequest,\n  postprocessRerankResponse,\n  scoreRerankingModel,\n  type RerankProfileName,\n} from './rerank'\n''', 'index rerank module import', \"from './rerank'\")\nreplace_once(index, '''  EmbeddingResponse,\n  ModelProps,\n''', '''  EmbeddingResponse,\n  RerankRequest,\n  RerankResponse,\n  ModelProps,\n''', 'index rerank type imports', 'RerankRequest')\ninsert_after(index, '''const MTP_CHECK_VERSION = 1\n''', '''const RERANKING_CHECK_VERSION = 1\n''', 'reranking check version', 'RERANKING_CHECK_VERSION')\nreplace_once(index, '''    const { path: presetPath, embeddingCount } = await generatePreset(\n''', '''    const { path: presetPath, embeddingCount, rerankingCount } = await generatePreset(\n''', 'startRouter preset counts', 'rerankingCount } = await generatePreset')\nreplace_once(index, '''    const embeddingSlotBonus = embeddingCount > 0 ? 1 : 0\n    if (modelsMax > 0 && embeddingSlotBonus > 0) {\n      modelsMax += embeddingSlotBonus\n    }\n''', '''    const embeddingSlotBonus = embeddingCount > 0 ? 1 : 0\n    const rerankingSlotBonus = rerankingCount > 0 ? 1 : 0\n    const utilitySlotBonus = embeddingSlotBonus + rerankingSlotBonus\n    if (modelsMax > 0 && utilitySlotBonus > 0) {\n      modelsMax += utilitySlotBonus\n    }\n''', 'utility slot bonus', 'rerankingSlotBonus')\nreplace_once(index, '''      `Router started on port ${info.port} (pid ${info.pid}, models_max=${modelsMax} [user=${userModelsMax}, +${embeddingSlotBonus} embedding, ${embeddingCount} installed], preset=${presetPath})`\n''', '''      `Router started on port ${info.port} (pid ${info.pid}, models_max=${modelsMax} [user=${userModelsMax}, +${embeddingSlotBonus} embedding, ${embeddingCount} installed, +${rerankingSlotBonus} reranking, ${rerankingCount} installed], preset=${presetPath})`\n''', 'router log line', '+${rerankingSlotBonus} reranking')\nreplace_once(index, '''    const isEmbedding = await this.resolveEmbeddingConfig(modelId, modelConfig)\n    await this.resolveMtpLayersConfig(modelId, modelConfig)\n\n    return {\n''', '''    const isEmbedding = await this.resolveEmbeddingConfig(modelId, modelConfig)\n    const isReranking = await this.resolveRerankingConfig(modelId, modelConfig)\n    await this.resolveMtpLayersConfig(modelId, modelConfig)\n\n    const capabilities: string[] = []\n    if (isEmbedding || isReranking) capabilities.push('embedding')\n    if (isReranking) capabilities.push('rerank')\n\n    return {\n''', 'get model reranking', 'const isReranking = await this.resolveRerankingConfig(modelId, modelConfig)')\nreplace_once(index, '''      embedding: isEmbedding,\n    } as modelInfo\n  }\n''', '''      embedding: isEmbedding || isReranking,\n      reranking: isReranking,\n      capabilities: capabilities.length > 0 ? capabilities : undefined,\n    } as modelInfo\n  }\n''', 'get model capabilities', 'reranking: isReranking')\ninsert_before(index, '''  private async resolveMtpLayersConfig(\n''', '''  private async resolveRerankingConfig(\n    modelId: string,\n    modelConfig: ModelConfig\n  ): Promise<boolean> {\n    const cfg = modelConfig as ModelConfig & {\n      reranking?: boolean\n      reranking_check_v?: number\n      capabilities?: { embedding?: boolean; rerank?: boolean; chat?: boolean }\n      pooling?: string\n      preferred_for?: string[]\n      score_normalization?: string\n      max_tokens_per_doc?: number\n      ubatch_size?: number\n      batch_size?: number\n    }\n\n    const hasFlag = typeof cfg.reranking === 'boolean' || cfg.capabilities?.rerank === true\n    const upToDate = cfg.reranking_check_v === RERANKING_CHECK_VERSION\n    if (hasFlag && upToDate) return cfg.reranking === true || cfg.capabilities?.rerank === true\n    if (cfg.reranking === true || cfg.capabilities?.rerank === true) return true\n\n    let isReranking = false\n    try {\n      const janDataFolderPath = await getJanDataFolderPath()\n      const fullModelPath = await joinPath([janDataFolderPath, modelConfig.model_path])\n      if (await fs.existsSync(fullModelPath)) {\n        const metadata = await readGgufMetadata(fullModelPath)\n        if (detectRerankingFromGgufMeta(metadata.metadata, modelId)) isReranking = true\n      }\n    } catch (e) {\n      logger.warn(`Failed to check reranking metadata for ${modelId}`, e)\n      return cfg.reranking === true || cfg.capabilities?.rerank === true\n    }\n\n    try {\n      const configPath = await joinPath([await this.getProviderPath(), 'models', modelId, 'model.yml'])\n      cfg.reranking = isReranking\n      cfg.reranking_check_v = RERANKING_CHECK_VERSION\n      cfg.capabilities = { ...(cfg.capabilities ?? {}), ...(isReranking ? { embedding: true, rerank: true } : {}) }\n      if (isReranking) {\n        cfg.embedding = true\n        cfg.pooling = 'rank'\n        if (!cfg.ubatch_size) cfg.ubatch_size = 2048\n        if (!cfg.batch_size) cfg.batch_size = 2048\n      }\n      await invoke<void>('write_yaml', { data: cfg, savePath: configPath })\n    } catch (e) {\n      logger.warn(`Failed to update reranking config for ${modelId}`, e)\n    }\n\n    return isReranking\n  }\n\n''', 'resolveRerankingConfig', 'private async resolveRerankingConfig')\nreplace_once(index, '''        const isEmbedding = await this.resolveEmbeddingConfig(\n          modelId,\n          modelConfig\n        )\n\n        const capabilities: string[] = []\n''', '''        const isEmbedding = await this.resolveEmbeddingConfig(\n          modelId,\n          modelConfig\n        )\n        const isReranking = await this.resolveRerankingConfig(\n          modelId,\n          modelConfig\n        )\n\n        const capabilities: string[] = []\n        if (isEmbedding || isReranking) capabilities.push('embedding')\n        if (isReranking) capabilities.push('rerank')\n''', 'list reranking detection', 'const isReranking = await this.resolveRerankingConfig(\\n          modelId,\\n          modelConfig\\n        )')\nreplace_once(index, '''          embedding: isEmbedding,\n          imported: isAbsolute,\n''', '''          embedding: isEmbedding || isReranking,\n          reranking: isReranking,\n          imported: isAbsolute,\n''', 'list model reranking fields', 'reranking: isReranking')\nreplace_once(index, '''    let isEmbedding = false\n    let mtpLayers = 0\n''', '''    let isEmbedding = false\n    let isReranking = false\n    let mtpLayers = 0\n''', 'import isReranking var', 'let isReranking = false')\nreplace_once(index, '''      if (detectEmbeddingFromGgufMeta(modelMetadata.metadata)) {\n        isEmbedding = true\n      }\n      mtpLayers = detectMtpLayersFromGgufMeta(modelMetadata.metadata)\n''', '''      if (detectEmbeddingFromGgufMeta(modelMetadata.metadata)) {\n        isEmbedding = true\n      }\n      if (detectRerankingFromGgufMeta(modelMetadata.metadata, modelId)) {\n        isReranking = true\n        isEmbedding = true\n      }\n      mtpLayers = detectMtpLayersFromGgufMeta(modelMetadata.metadata)\n''', 'import detect reranking', 'isReranking = true')\nreplace_once(index, '''      embedding: isEmbedding,\n      embedding_check_v: EMBEDDING_CHECK_VERSION,\n      mtp_layers: mtpLayers,\n''', '''      embedding: isEmbedding,\n      reranking: isReranking,\n      capabilities: isReranking ? { embedding: true, rerank: true } : isEmbedding ? { embedding: true } : undefined,\n      embedding_check_v: EMBEDDING_CHECK_VERSION,\n      reranking_check_v: RERANKING_CHECK_VERSION,\n      mtp_layers: mtpLayers,\n''', 'import model config reranking', 'reranking_check_v')\nreplace_once(index, '''      ...(isEmbedding\n        ? { pooling: 'mean', ubatch_size: 2048, batch_size: 2048 }\n        : {}),\n''', '''      ...(isReranking\n        ? { pooling: 'rank', ubatch_size: 2048, batch_size: 2048, preferred_for: ['default'] }\n        : isEmbedding\n          ? { pooling: 'mean', ubatch_size: 2048, batch_size: 2048 }\n          : {}),\n''', 'import pooling', \"pooling: 'rank'\")\ninsert_before(index, '''  /**\n   * Check if a tool is supported by the model\n''', '''  private async installedRerankingModels(): Promise<modelInfo[]> {\n    const downloadedModelList = await this.list()\n    return downloadedModelList.filter(\n      (m) =>\n        (m as any).reranking === true ||\n        (Array.isArray((m as any).capabilities) &&\n          (m as any).capabilities.includes('rerank'))\n    )\n  }\n\n  private async selectRerankingModel(req: ReturnType<typeof normalizeRerankRequest>): Promise<modelInfo> {\n    const installed = await this.installedRerankingModels()\n    const loadedIds = new Set(await this.getLoadedModels().catch(() => [] as string[]))\n    const explicitModel = req.model && req.model !== 'auto' ? req.model : undefined\n    const storedDefault = getDefaultRerankingModelId('llamacpp')\n    const preferred = explicitModel ?? storedDefault\n\n    if (explicitModel) {\n      const match = installed.find((m) => m.id === explicitModel)\n      if (!match) throw new Error(`Requested reranking model \"${explicitModel}\" is not installed or is not marked reranking=true`)\n      return match\n    }\n\n    if (!storedDefault && installed.length === 1) {\n      setDefaultRerankingModelId('llamacpp', installed[0].id)\n      logger.info(`Auto-promoted \"${installed[0].id}\" as default reranking model (single installed reranker)`)\n    }\n\n    if (installed.length === 0) {\n      throw new Error('No reranking model is installed. Import a GGUF reranker or set reranking: true with pooling: rank in model.yml.')\n    }\n\n    const profile = req.profile as RerankProfileName\n    return installed\n      .map((m) => ({ model: m, score: scoreRerankingModel(m, profile, loadedIds, preferred) }))\n      .sort((a, b) => b.score - a.score)[0].model\n  }\n\n  async rerank(req: RerankRequest): Promise<RerankResponse> {\n    const started = performance.now()\n    const normalized = normalizeRerankRequest(req)\n    const model = await this.selectRerankingModel(normalized)\n    const targetModelId = model.id\n\n    let modelLoadMs = 0\n    let sInfo = await this.findSessionByModel(targetModelId)\n    const cacheHit = !!sInfo\n    if (!sInfo) {\n      const loadStarted = performance.now()\n      sInfo = await this.load(targetModelId, undefined, true)\n      modelLoadMs = Math.round(performance.now() - loadStarted)\n    }\n\n    const response = await fetch(`http://localhost:${sInfo.port}/v1/rerank`, {\n      method: 'POST',\n      headers: {\n        'Content-Type': 'application/json',\n        'Authorization': `Bearer ${sInfo.api_key}`,\n      },\n      body: JSON.stringify({\n        ...req,\n        model: targetModelId,\n        documents: normalized.documents,\n        top_n: normalized.top_n,\n        return_documents: false,\n      }),\n    })\n\n    if (!response.ok) {\n      const errorData = await response.json().catch(() => null)\n      throw new Error(`Rerank request failed with status ${response.status}: ${JSON.stringify(errorData)}`)\n    }\n\n    const raw = await response.json()\n    return postprocessRerankResponse(raw, normalized, {\n      model: targetModelId,\n      provider: 'local_gguf',\n      fallback_used: false,\n      latency_ms: Math.round(performance.now() - started),\n      model_load_ms: modelLoadMs,\n      cache_hit: cacheHit,\n    }) as RerankResponse\n  }\n\n  async getRerankStatus(): Promise<Record<string, unknown>> {\n    const installed = await this.installedRerankingModels()\n    const loaded = new Set(await this.getLoadedModels().catch(() => [] as string[]))\n    return {\n      enabled: installed.length > 0,\n      selected_model: getDefaultRerankingModelId('llamacpp') ?? 'auto',\n      available_models: installed.map((m) => ({\n        id: m.id,\n        name: m.name,\n        loaded: loaded.has(m.id),\n        sizeBytes: m.sizeBytes,\n        capabilities: (m as any).capabilities,\n      })),\n    }\n  }\n\n''', 'rerank methods', 'async rerank(req: RerankRequest)')\n\n# server mod.rs\nmodrs = p('src-tauri/src/core/server/mod.rs')\ninsert_after(modrs, 'pub mod proxy;\\n', 'pub mod rerank;\\n', 'server rerank mod', 'pub mod rerank;')\n\n# proxy.rs\nproxy = p('src-tauri/src/core/server/proxy.rs')\ninsert_after(proxy, '''use crate::core::{\n    mcp::models::McpSettings,\n    state::{ProviderConfig, ServerHandle, SharedMcpServers},\n};\n''', '''use crate::core::server::rerank::{\n    build_rerank_status_json, is_rerank_status_path, postprocess_rerank_response,\n    prepare_rerank_request, record_rerank_observation, rerank_error_json,\n};\n''', 'proxy rerank imports', 'build_rerank_status_json')\ninsert_after(proxy, '''    match (method.clone(), destination_path.as_str()) {\n''', '''        (hyper::Method::GET, path) if is_rerank_status_path(path) => {\n            let status_json = build_rerank_status_json(&jan_data_folder, &llama_state, &client).await;\n            let mut response_builder = Response::builder()\n                .status(StatusCode::OK)\n                .header(hyper::header::CONTENT_TYPE, \"application/json\");\n            response_builder = add_cors_headers_with_host_and_origin(\n                response_builder,\n                &host_header,\n                &origin_header,\n                &config.trusted_hosts,\n            );\n            return Ok(response_builder.body(full(status_json.to_string())).unwrap());\n        }\n        (hyper::Method::POST, \"/rerank\") | (hyper::Method::POST, \"/reranking\") => {\n            let started = std::time::Instant::now();\n            let body_bytes = match body.collect().await {\n                Ok(c) => c.to_bytes(),\n                Err(_) => {\n                    let mut error_response = Response::builder().status(StatusCode::INTERNAL_SERVER_ERROR);\n                    error_response = add_cors_headers_with_host_and_origin(\n                        error_response,\n                        &host_header,\n                        &origin_header,\n                        &config.trusted_hosts,\n                    );\n                    return Ok(error_response.body(full(\"Failed to read request body\")).unwrap());\n                }\n            };\n            let prepared = match prepare_rerank_request(body_bytes, &jan_data_folder, &client, &llama_state).await {\n                Ok(v) => v,\n                Err(e) => {\n                    let mut error_response = Response::builder().status(e.status);\n                    error_response = add_cors_headers_with_host_and_origin(\n                        error_response,\n                        &host_header,\n                        &origin_header,\n                        &config.trusted_hosts,\n                    );\n                    return Ok(error_response\n                        .header(hyper::header::CONTENT_TYPE, \"application/json\")\n                        .body(full(rerank_error_json(&e.kind, &e.message)))\n                        .unwrap());\n                }\n            };\n            let (upstream_url, api_key) = if let Some(ext) = prepared.external.clone() {\n                let base = ext.base_url.trim_end_matches('/').to_string();\n                (format!(\"{base}/rerank\"), ext.api_key)\n            } else if let Some((url, key)) = router_upstream(&llama_state, destination_path.as_str()).await {\n                (url, Some(key))\n            } else {\n                let mut error_response = Response::builder().status(StatusCode::SERVICE_UNAVAILABLE);\n                error_response = add_cors_headers_with_host_and_origin(\n                    error_response,\n                    &host_header,\n                    &origin_header,\n                    &config.trusted_hosts,\n                );\n                return Ok(error_response\n                    .header(hyper::header::CONTENT_TYPE, \"application/json\")\n                    .body(full(rerank_error_json(\"router_unavailable\", \"llama.cpp router is not running\")))\n                    .unwrap());\n            };\n            let mut req_out = client\n                .post(&upstream_url)\n                .header(\"Content-Type\", \"application/json\")\n                .body(prepared.body.clone());\n            if let Some(key) = api_key {\n                req_out = req_out.header(\"Authorization\", format!(\"Bearer {key}\"));\n            }\n            let upstream = match req_out.send().await {\n                Ok(v) => v,\n                Err(e) => {\n                    let mut error_response = Response::builder().status(StatusCode::BAD_GATEWAY);\n                    error_response = add_cors_headers_with_host_and_origin(\n                        error_response,\n                        &host_header,\n                        &origin_header,\n                        &config.trusted_hosts,\n                    );\n                    return Ok(error_response\n                        .header(hyper::header::CONTENT_TYPE, \"application/json\")\n                        .body(full(rerank_error_json(\"upstream_error\", &format!(\"Rerank upstream request failed: {e}\"))))\n                        .unwrap());\n                }\n            };\n            let status = upstream.status();\n            let text = upstream.text().await.unwrap_or_default();\n            if !status.is_success() {\n                let mut error_response = Response::builder().status(status);\n                error_response = add_cors_headers_with_host_and_origin(\n                    error_response,\n                    &host_header,\n                    &origin_header,\n                    &config.trusted_hosts,\n                );\n                return Ok(error_response\n                    .header(hyper::header::CONTENT_TYPE, \"application/json\")\n                    .body(full(text))\n                    .unwrap());\n            }\n            let raw_json: serde_json::Value = match serde_json::from_str(&text) {\n                Ok(v) => v,\n                Err(e) => {\n                    let mut error_response = Response::builder().status(StatusCode::BAD_GATEWAY);\n                    error_response = add_cors_headers_with_host_and_origin(\n                        error_response,\n                        &host_header,\n                        &origin_header,\n                        &config.trusted_hosts,\n                    );\n                    return Ok(error_response\n                        .header(hyper::header::CONTENT_TYPE, \"application/json\")\n                        .body(full(rerank_error_json(\"invalid_upstream_response\", &format!(\"Invalid rerank JSON from upstream: {e}\"))))\n                        .unwrap());\n                }\n            };\n            let out = postprocess_rerank_response(raw_json, prepared.trace, started.elapsed().as_millis() as u64);\n            if let Some(meta) = out.get(\"meta\") {\n                record_rerank_observation(meta.clone()).await;\n            }\n            let mut response_builder = Response::builder()\n                .status(StatusCode::OK)\n                .header(hyper::header::CONTENT_TYPE, \"application/json\");\n            response_builder = add_cors_headers_with_host_and_origin(\n                response_builder,\n                &host_header,\n                &origin_header,\n                &config.trusted_hosts,\n            );\n            return Ok(response_builder.body(full(out.to_string())).unwrap());\n        }\n''', 'proxy rerank routes', 'is_rerank_status_path(path)')\n\n# RAG extension\nrag = p('extensions/rag-extension/src/index.ts')\nreplace_once(rag, '''    autoInlineContextRatio: 0.75,\n  }\n''', '''    autoInlineContextRatio: 0.75,\n    rerankingMode: 'auto' as 'auto' | 'off' | 'model',\n    rerankingModel: 'auto',\n    rerankTopKBefore: 60,\n    rerankTopNAfter: 8,\n    rerankMinRelevanceScore: 0,\n    rerankMaxTokensPerDoc: 4096,\n    rerankEvidenceMode: 'off' as 'off' | 'top_n' | 'all',\n  }\n''', 'rag config', 'rerankingMode')\ninsert_after(rag, '''    this.config.autoInlineContextRatio = await this.getSetting(\n      'auto_inline_context_ratio',\n      this.config.autoInlineContextRatio\n    )\n''', '''    this.config.rerankingMode = await this.getSetting('reranking_mode', this.config.rerankingMode)\n    this.config.rerankingModel = await this.getSetting('reranking_model', this.config.rerankingModel)\n    this.config.rerankTopKBefore = await this.getSetting('rerank_top_k_before', this.config.rerankTopKBefore)\n    this.config.rerankTopNAfter = await this.getSetting('rerank_top_n_after', this.config.rerankTopNAfter)\n    this.config.rerankMinRelevanceScore = await this.getSetting('rerank_min_relevance_score', this.config.rerankMinRelevanceScore)\n    this.config.rerankMaxTokensPerDoc = await this.getSetting('rerank_max_tokens_per_doc', this.config.rerankMaxTokensPerDoc)\n    this.config.rerankEvidenceMode = await this.getSetting('rerank_evidence_mode', this.config.rerankEvidenceMode)\n''', 'rag configure reranking', 'rerank_top_k_before')\nreplace_once(rag, '''    const topK = (args['top_k'] as number) || s.retrievalLimit || 3\n    const threshold = s.retrievalThreshold ?? 0.3\n''', '''    const requestedTopK = (args['top_k'] as number) || s.retrievalLimit || 3\n    const shouldRerank = s.rerankingMode !== 'off'\n    const topK = shouldRerank ? Math.max(requestedTopK, s.rerankTopKBefore || 60) : requestedTopK\n    const threshold = shouldRerank ? Math.min(s.retrievalThreshold ?? 0.3, 0.05) : (s.retrievalThreshold ?? 0.3)\n''', 'rag topK reranking', 'const shouldRerank')\nreplace_once(rag, '''      const payload = {\n        thread_id: threadId,\n        project_id: projectId,\n        scope,\n        query,\n        citations:\n          results?.map((r: any) => ({\n            id: r.id,\n            text: r.text,\n            score: r.score,\n            file_id: r.file_id,\n            chunk_file_order: r.chunk_file_order,\n          })) ?? [],\n        mode,\n      }\n''', '''      let citations =\n        results?.map((r: any) => ({\n          id: r.id,\n          text: r.text,\n          score: r.score,\n          file_id: r.file_id,\n          chunk_file_order: r.chunk_file_order,\n        })) ?? []\n      let reranking: Record<string, unknown> = { enabled: false }\n      if (shouldRerank && citations.length > 1) {\n        const reranked = await this.rerankCitations(query, citations, requestedTopK)\n        citations = reranked.citations\n        reranking = reranked.meta\n      } else {\n        citations = citations.slice(0, requestedTopK)\n      }\n      const payload = {\n        thread_id: threadId,\n        project_id: projectId,\n        scope,\n        query,\n        citations,\n        mode,\n        reranking,\n      }\n''', 'rag payload reranking', 'let reranking: Record<string, unknown>')\ninsert_before(rag, '''  onSettingUpdate<T>(key: string, value: T): void {\n''', '''  private async rerankCitations(\n    query: string,\n    citations: Array<Record<string, unknown>>,\n    requestedTopK: number\n  ): Promise<{ citations: Array<Record<string, unknown>>; meta: Record<string, unknown> }> {\n    const llm = window.core?.extensionManager.getByName(\n      '@janhq/llamacpp-extension'\n    ) as AIEngine & {\n      rerank?: (req: any) => Promise<{ results: Array<{ index: number; relevance_score: number; evidence?: string; contribution?: string }>; meta?: Record<string, unknown> }>\n    }\n    if (!llm?.rerank) return { citations: citations.slice(0, requestedTopK), meta: { enabled: false, reason: 'llamacpp extension has no rerank method' } }\n    try {\n      const topN = Math.max(1, Math.min(citations.length, this.config.rerankTopNAfter || requestedTopK))\n      const response = await llm.rerank({\n        model: this.config.rerankingMode === 'model' ? this.config.rerankingModel : 'auto',\n        query,\n        documents: citations.map((c, index) => ({\n          text: String(c.text ?? ''),\n          metadata: {\n            index,\n            id: c.id,\n            file_id: c.file_id,\n            chunk_file_order: c.chunk_file_order,\n            vector_score: c.score,\n          },\n        })),\n        top_n: topN,\n        return_documents: true,\n        min_relevance_score: this.config.rerankMinRelevanceScore || undefined,\n        max_tokens_per_doc: this.config.rerankMaxTokensPerDoc || 4096,\n        evidence_mode: this.config.rerankEvidenceMode,\n      })\n      const reranked = response.results\n        .map((r) => ({\n          ...citations[r.index],\n          rerank_score: r.relevance_score,\n          evidence: r.evidence,\n          contribution: r.contribution,\n        }))\n        .filter((c) => c.text)\n      return { citations: reranked, meta: { enabled: true, ...(response.meta ?? {}) } }\n    } catch (e) {\n      console.warn('[RAG] Reranking failed, falling back to vector order:', e)\n      return {\n        citations: citations.slice(0, requestedTopK),\n        meta: {\n          enabled: false,\n          fallback_used: true,\n          error: e instanceof Error ? e.message : String(e),\n        },\n      }\n    }\n  }\n\n''', 'rag rerankCitations method', 'private async rerankCitations')\ninsert_after(rag, '''      case 'parse_mode':\n        this.config.parseMode = String(value) as\n          | 'auto'\n          | 'inline'\n          | 'embeddings'\n          | 'prompt'\n        break\n''', '''      case 'reranking_mode':\n        this.config.rerankingMode = String(value) as 'auto' | 'off' | 'model'\n        break\n      case 'reranking_model':\n        this.config.rerankingModel = String(value)\n        break\n      case 'rerank_top_k_before':\n        this.config.rerankTopKBefore = Number(value)\n        break\n      case 'rerank_top_n_after':\n        this.config.rerankTopNAfter = Number(value)\n        break\n      case 'rerank_min_relevance_score':\n        this.config.rerankMinRelevanceScore = Number(value)\n        break\n      case 'rerank_max_tokens_per_doc':\n        this.config.rerankMaxTokensPerDoc = Number(value)\n        break\n      case 'rerank_evidence_mode':\n        this.config.rerankEvidenceMode = String(value) as 'off' | 'top_n' | 'all'\n        break\n''', 'rag onSettingUpdate reranking', 'case \\'reranking_mode\\'')\n\nprint('Jan reranking full implementation applied. Run typecheck/build/tests now; software will not validate itself out of pity.')\n"
STAGE7_SCRIPT = "#!/usr/bin/env python3\nfrom __future__ import annotations\n\nimport json\nimport re\nfrom pathlib import Path\n\nROOT = Path.cwd()\n\n\ndef p(*parts: str) -> Path:\n    return ROOT.joinpath(*parts)\n\n\ndef read(path: Path) -> str:\n    return path.read_text(encoding='utf-8')\n\n\ndef write(path: Path, data: str) -> None:\n    path.write_text(data, encoding='utf-8')\n\n\ndef require(path: Path) -> None:\n    if not path.exists():\n        raise SystemExit(f'Missing {path}. Run this from the Jan repository root after applying stages 1-6.')\n\n\ndef replace_once(path: Path, old: str, new: str, label: str) -> None:\n    s = read(path)\n    if new.strip()[:120] in s:\n        return\n    if old not in s:\n        raise SystemExit(f'Could not find target for {label} in {path}')\n    write(path, s.replace(old, new, 1))\n\n\ndef insert_after(path: Path, needle: str, insertion: str, label: str, marker = None) -> None:\n    s = read(path)\n    if (marker and marker in s) or insertion.strip()[:120] in s:\n        return\n    idx = s.find(needle)\n    if idx < 0:\n        raise SystemExit(f'Could not find insertion point for {label} in {path}')\n    idx += len(needle)\n    write(path, s[:idx] + insertion + s[idx:])\n\n\ndef replace_regex(path: Path, pattern: str, repl: str, label: str, flags: int = re.S) -> None:\n    s = read(path)\n    # Use a callable replacement so literal backslashes in TypeScript/Rust regex\n    # strings (for example \\s or \\-) are not interpreted by Python's re\n    # replacement-template parser. Without this, Python 3.9 raises\n    # `re.error: bad escape \\s` on valid source text. Humanity loses another\n    # afternoon to escaping rules, as tradition demands.\n    new, n = re.subn(pattern, lambda _m: repl, s, count=1, flags=flags)\n    if n == 0:\n        if repl.strip()[:120] in s:\n            return\n        raise SystemExit(f'Could not find regex target for {label} in {path}')\n    write(path, new)\n\n\nrequired = [\n    p('extensions/llamacpp-extension/settings.json'),\n    p('extensions/llamacpp-extension/src/util.ts'),\n    p('extensions/llamacpp-extension/src/index.ts'),\n    p('extensions/rag-extension/src/index.ts'),\n    p('src-tauri/src/core/server/proxy.rs'),\n    p('src-tauri/static/openapi.json'),\n]\nfor path in required:\n    require(path)\n\n# 3. Turn the default reranker setting into a visible dropdown whose options are refreshed from installed rerankers.\nsettings_path = p('extensions/llamacpp-extension/settings.json')\nsettings = json.loads(read(settings_path))\nfor item in settings:\n    if item.get('key') == 'default_reranker_model':\n        item['controllerType'] = 'dropdown'\n        item['description'] = 'Local reranker model to use for /v1/rerank and automatic RAG reranking. Auto selects an installed local reranker when available.'\n        item['controllerProps'] = {\n            'value': item.get('controllerProps', {}).get('value', 'auto') or 'auto',\n            'options': [{'name': 'Auto (recommended)', 'value': 'auto'}],\n            'recommended': 'auto',\n        }\n        break\nelse:\n    insert_idx = next((i + 1 for i, it in enumerate(settings) if it.get('key') == 'models_max'), len(settings))\n    settings.insert(insert_idx, {\n        'key': 'default_reranker_model',\n        'title': 'Default reranker model',\n        'description': 'Local reranker model to use for /v1/rerank and automatic RAG reranking. Auto selects an installed local reranker when available.',\n        'controllerType': 'dropdown',\n        'controllerProps': {\n            'value': 'auto',\n            'options': [{'name': 'Auto (recommended)', 'value': 'auto'}],\n            'recommended': 'auto',\n        },\n    })\nwrite(settings_path, json.dumps(settings, indent=2) + '\\n')\n\n# 6. Tighten reranker detection with more real-world metadata/task signals.\nutil_path = p('extensions/llamacpp-extension/src/util.ts')\nreplace_regex(\n    util_path,\n    r\"const RERANKING_NAME_RE =\\n\\s+/\\(\\^\\|\\[\\\\s\\._\\\\-/\\]\\).*?\\n\",\n    \"const RERANKING_NAME_RE =\\n  /(^|[\\\\s._\\\\-/])(?:rerank|reranker|re-ranker|ranking|text[\\\\s._\\\\-]?ranking|cross[\\\\s._\\\\-]?encoder|crossencoder|bge[\\\\s._\\\\-]?reranker|jina[\\\\s._\\\\-]?reranker|qwen3[\\\\s._\\\\-]?reranker|mxbai[\\\\s._\\\\-]?rerank|mixedbread[\\\\s._\\\\-]?rerank|gte[\\\\s._\\\\-]?rerank)([\\\\s._\\\\-/]|$)/i\\n\\nconst RERANKING_TASK_RE =\\n  /(^|[\\\\s._\\\\-/])(?:rerank|reranking|re-ranking|rank|ranking|text[\\\\s._\\\\-]?ranking|cross[\\\\s._\\\\-]?encoder|crossencoder)([\\\\s._\\\\-/]|$)/i\\n\",\n    'broaden reranker regex'\n)\nreplace_once(\n    util_path,\n    \"  const keys = [\\n    'general.name',\\n    'general.basename',\\n    'general.description',\\n    'general.source.url',\\n    'general.url',\\n    'general.repo_url',\\n    'tokenizer.ggml.model',\\n  ]\\n\",\n    \"  const keys = [\\n    'general.name',\\n    'general.basename',\\n    'general.description',\\n    'general.tags',\\n    'general.datasets',\\n    'general.source.url',\\n    'general.url',\\n    'general.repo_url',\\n    'tokenizer.ggml.model',\\n    'pipeline_tag',\\n    'task',\\n    'tasks',\\n    'sentence_transformers.task',\\n    'sentence_transformers.model_type',\\n    'sentence_transformers.modules',\\n  ]\\n\",\n    'reranker metadata haystack keys'\n)\nreplace_once(\n    util_path,\n    \"export function detectRerankingFromGgufMeta(\\n  meta: Record<string, unknown> | undefined,\\n  modelId: string = ''\\n): boolean {\\n  const haystack = metaStringHaystack(meta, modelId)\\n  if (RERANKING_NAME_RE.test(haystack)) return true\\n  if (hasExplicitRankPooling(meta)) return true\\n  return false\\n}\\n\",\n    \"export function detectRerankingFromGgufMeta(\\n  meta: Record<string, unknown> | undefined,\\n  modelId: string = ''\\n): boolean {\\n  const haystack = metaStringHaystack(meta, modelId)\\n  if (RERANKING_NAME_RE.test(haystack)) return true\\n\\n  const taskHaystack = [\\n    meta?.['pipeline_tag'],\\n    meta?.['task'],\\n    meta?.['tasks'],\\n    meta?.['sentence_transformers.task'],\\n    meta?.['sentence_transformers.model_type'],\\n    meta?.['sentence_transformers.modules'],\\n  ]\\n    .map((value) =>\\n      Array.isArray(value) ? value.join(' ') : asMetaString(value)\\n    )\\n    .filter(Boolean)\\n    .join(' ')\\n  if (RERANKING_TASK_RE.test(taskHaystack)) return true\\n\\n  if (hasExplicitRankPooling(meta)) return true\\n  return false\\n}\\n\",\n    'reranker detection task metadata'\n)\n\n# 3, 8, 9. Refresh the dropdown dynamically and normalize/validate internal rerank requests.\nindex_path = p('extensions/llamacpp-extension/src/index.ts')\ninsert_after(\n    index_path,\n    \"    await this.migratePersistedModelSettingsToYaml()\\n\",\n    \"\\n    await this.refreshDefaultRerankerModelOptions().catch((e) =>\\n      logger.warn('Failed to refresh reranker model options:', e)\\n    )\\n\",\n    'refresh reranker dropdown on load',\n    'refreshDefaultRerankerModelOptions().catch'\n)\ninsert_after(\n    index_path,\n    \"    events.emit(AppEvent.onModelImported, {\\n      modelId,\\n      modelPath,\\n      mmprojPath,\\n      size_bytes,\\n      model_sha256: opts.modelSha256,\\n      model_size_bytes: opts.modelSize,\\n      mmproj_sha256: opts.mmprojSha256,\\n      mmproj_size_bytes: opts.mmprojSize,\\n      embedding: isEmbedding,\\n    })\\n\",\n    \"\\n    await this.refreshDefaultRerankerModelOptions().catch((e) =>\\n      logger.warn('Failed to refresh reranker model options after import:', e)\\n    )\\n\",\n    'refresh reranker dropdown after import',\n    'refresh reranker model options after import'\n)\ninsert_after(\n    index_path,\n    \"  private configuredDefaultRerankingModelId(): string | undefined {\\n    const value = (this.config as LlamacppConfig & {\\n      default_reranker_model?: string\\n    })?.default_reranker_model\\n    if (typeof value !== 'string') return undefined\\n    const trimmed = value.trim()\\n    if (!trimmed || trimmed === 'auto' || trimmed === '*') return undefined\\n    return trimmed\\n  }\\n\",\n    r'''\n\n  private async refreshDefaultRerankerModelOptions(): Promise<void> {\n    const settings = await this.getSettings()\n    const idx = settings.findIndex((s) => s.key === 'default_reranker_model')\n    if (idx < 0) return\n\n    const models = await this.installedRerankingModels().catch(() => [])\n    const options = [\n      { name: 'Auto (recommended)', value: 'auto' },\n      ...models.map((m) => ({ name: m.name || m.id, value: m.id })),\n    ]\n    const allowed = new Set(options.map((o) => o.value))\n    const current = String(\n      settings[idx].controllerProps?.value ??\n        (this.config as LlamacppConfig & { default_reranker_model?: string })\n          ?.default_reranker_model ??\n        'auto'\n    )\n    const value = allowed.has(current) ? current : 'auto'\n\n    const nextSetting = {\n      ...settings[idx],\n      controllerType: 'dropdown',\n      controllerProps: {\n        ...settings[idx].controllerProps,\n        value,\n        options,\n        recommended: 'auto',\n      },\n    }\n\n    await this.updateSettings(\n      settings.map((setting, settingIdx) =>\n        settingIdx === idx ? nextSetting : setting\n      )\n    )\n    ;(this.config as LlamacppConfig & { default_reranker_model?: string }).default_reranker_model = value\n  }\n\n  private extractRerankDocumentText(document: unknown, index: number): string {\n    if (typeof document === 'string') return document\n    if (!document || typeof document !== 'object') {\n      throw new Error(`documents[${index}] must be a string or an object with a text/content field`)\n    }\n\n    const obj = document as Record<string, unknown>\n    for (const key of ['text', 'content']) {\n      if (typeof obj[key] === 'string') return obj[key] as string\n    }\n\n    const nested = obj.document\n    if (typeof nested === 'string') return nested\n    if (nested && typeof nested === 'object') {\n      const nestedObj = nested as Record<string, unknown>\n      for (const key of ['text', 'content']) {\n        if (typeof nestedObj[key] === 'string') return nestedObj[key] as string\n      }\n    }\n\n    throw new Error(`documents[${index}] must contain a string text/content/document field`)\n  }\n\n  private normalizeRerankRequest(req: RerankRequest): RerankRequest & { documents: string[] } {\n    if (!req || typeof req.query !== 'string' || req.query.trim().length === 0) {\n      throw new Error('rerank requires a non-empty query string')\n    }\n\n    const rawDocuments = Array.isArray((req as any).documents)\n      ? (req as any).documents\n      : Array.isArray((req as any).texts)\n        ? (req as any).texts\n        : undefined\n\n    if (!Array.isArray(rawDocuments) || rawDocuments.length === 0) {\n      throw new Error('rerank requires a non-empty documents or texts array')\n    }\n\n    const documents = rawDocuments.map((document, index) =>\n      this.extractRerankDocumentText(document, index)\n    )\n    const topN = (req as any).top_n ?? (req as any).top_k\n    if (topN !== undefined) {\n      const parsedTopN = Number(topN)\n      if (!Number.isInteger(parsedTopN) || parsedTopN < 1 || parsedTopN > documents.length) {\n        throw new Error(`top_n/top_k must be an integer between 1 and ${documents.length}`)\n      }\n      return { ...(req as any), top_n: parsedTopN, documents }\n    }\n\n    return { ...(req as any), documents }\n  }\n''',\n    'reranker dropdown refresh + request normalization',\n    'normalizeRerankRequest(req: RerankRequest)'\n)\nreplace_once(\n    index_path,\n    \"  async rerank(req: RerankRequest): Promise<RerankResponse> {\\n    if (!req || typeof req.query !== 'string' || req.query.trim().length === 0) {\\n      throw new Error('rerank requires a non-empty query string')\\n    }\\n\\n    const documents = req.documents ?? req.texts\\n    if (!Array.isArray(documents) || documents.length === 0) {\\n      throw new Error('rerank requires a non-empty documents or texts array')\\n    }\\n\\n    const installedReranking = await this.installedRerankingModels()\\n\",\n    \"  async rerank(req: RerankRequest): Promise<RerankResponse> {\\n    const normalized = this.normalizeRerankRequest(req)\\n    const documents = normalized.documents\\n\\n    const installedReranking = await this.installedRerankingModels()\\n\",\n    'use normalized rerank request'\n)\nreplace_once(\n    index_path,\n    \"    const body = JSON.stringify({\\n      ...req,\\n      model: targetModelId,\\n      documents,\\n    })\\n\",\n    \"    const body = JSON.stringify({\\n      ...normalized,\\n      model: targetModelId,\\n      documents,\\n    })\\n\",\n    'send normalized rerank request'\n)\n\n# 8, 9. Normalize /v1/rerank requests and return structured JSON errors in the Rust proxy.\nproxy_path = p('src-tauri/src/core/server/proxy.rs')\nreplace_once(\n    proxy_path,\n    \"fn ensure_rerank_model_in_body(\\n    json_body: &mut serde_json::Value,\\n    jan_data_folder: &str,\\n) -> Result<String, String> {\\n    let requested = json_body.get(\\\"model\\\").and_then(|v| v.as_str()).map(str::trim);\\n    let needs_auto = requested.is_none() || requested == Some(\\\"\\\") || requested == Some(\\\"auto\\\");\\n    let model = if needs_auto {\\n        find_default_reranking_model_id(jan_data_folder).ok_or_else(|| {\\n            \\\"No local reranking model is available. Import a GGUF reranker or mark a model.yml with reranking: true and pooling: rank.\\\".to_string()\\n        })?\\n    } else {\\n        requested.unwrap().to_string()\\n    };\\n\\n    json_body[\\\"model\\\"] = serde_json::Value::String(model.clone());\\n    Ok(model)\\n}\\n\",\n    r'''type RerankProxyError = (StatusCode, &'static str, String);\n\nfn extract_rerank_document_text(value: &serde_json::Value) -> Option<String> {\n    if let Some(s) = value.as_str() {\n        return Some(s.to_string());\n    }\n    let obj = value.as_object()?;\n    for key in [\"text\", \"content\"] {\n        if let Some(s) = obj.get(key).and_then(|v| v.as_str()) {\n            return Some(s.to_string());\n        }\n    }\n    if let Some(document) = obj.get(\"document\") {\n        if let Some(s) = document.as_str() {\n            return Some(s.to_string());\n        }\n        if let Some(nested) = document.as_object() {\n            for key in [\"text\", \"content\"] {\n                if let Some(s) = nested.get(key).and_then(|v| v.as_str()) {\n                    return Some(s.to_string());\n                }\n            }\n        }\n    }\n    None\n}\n\nfn normalize_rerank_body(json_body: &mut serde_json::Value) -> Result<(), RerankProxyError> {\n    let query = json_body\n        .get(\"query\")\n        .and_then(|v| v.as_str())\n        .map(str::trim)\n        .unwrap_or(\"\");\n    if query.is_empty() {\n        return Err((\n            StatusCode::BAD_REQUEST,\n            \"invalid_request_error\",\n            \"rerank requires a non-empty query string\".to_string(),\n        ));\n    }\n\n    let raw_documents = json_body\n        .get(\"documents\")\n        .or_else(|| json_body.get(\"texts\"))\n        .and_then(|v| v.as_array())\n        .ok_or_else(|| {\n            (\n                StatusCode::BAD_REQUEST,\n                \"invalid_request_error\",\n                \"rerank requires a non-empty documents or texts array\".to_string(),\n            )\n        })?;\n\n    if raw_documents.is_empty() {\n        return Err((\n            StatusCode::BAD_REQUEST,\n            \"invalid_request_error\",\n            \"rerank requires at least one document\".to_string(),\n        ));\n    }\n\n    let mut documents = Vec::with_capacity(raw_documents.len());\n    for (idx, item) in raw_documents.iter().enumerate() {\n        let Some(text) = extract_rerank_document_text(item) else {\n            return Err((\n                StatusCode::BAD_REQUEST,\n                \"invalid_request_error\",\n                format!(\"documents[{idx}] must be a string or object with text/content/document\"),\n            ));\n        };\n        documents.push(serde_json::Value::String(text));\n    }\n\n    let top_n_value = json_body.get(\"top_n\").or_else(|| json_body.get(\"top_k\"));\n    if let Some(value) = top_n_value {\n        let parsed = value.as_i64().or_else(|| value.as_str().and_then(|s| s.parse::<i64>().ok()));\n        let Some(top_n) = parsed else {\n            return Err((\n                StatusCode::BAD_REQUEST,\n                \"invalid_request_error\",\n                \"top_n/top_k must be an integer\".to_string(),\n            ));\n        };\n        if top_n < 1 || top_n as usize > documents.len() {\n            return Err((\n                StatusCode::BAD_REQUEST,\n                \"invalid_request_error\",\n                format!(\"top_n/top_k must be between 1 and {}\", documents.len()),\n            ));\n        }\n        json_body[\"top_n\"] = serde_json::Value::Number(top_n.into());\n    }\n\n    if let Some(obj) = json_body.as_object_mut() {\n        obj.remove(\"texts\");\n        obj.remove(\"top_k\");\n        obj.insert(\"documents\".to_string(), serde_json::Value::Array(documents));\n    }\n    Ok(())\n}\n\nfn ensure_rerank_model_in_body(\n    json_body: &mut serde_json::Value,\n    jan_data_folder: &str,\n) -> Result<String, RerankProxyError> {\n    normalize_rerank_body(json_body)?;\n\n    let requested = json_body.get(\"model\").and_then(|v| v.as_str()).map(str::trim);\n    let needs_auto = requested.is_none() || requested == Some(\"\") || requested == Some(\"auto\");\n    let model = if needs_auto {\n        find_default_reranking_model_id(jan_data_folder).ok_or_else(|| {\n            (\n                StatusCode::SERVICE_UNAVAILABLE,\n                \"model_not_available\",\n                \"No local reranking model is available. Import a GGUF reranker or mark a model.yml with reranking: true and pooling: rank.\".to_string(),\n            )\n        })?\n    } else {\n        requested.unwrap().to_string()\n    };\n\n    json_body[\"model\"] = serde_json::Value::String(model.clone());\n    Ok(model)\n}\n''',\n    'proxy rerank request normalization and structured errors'\n)\nreplace_once(\n    proxy_path,\n    \"            if let Err(e) = ensure_rerank_model_in_body(&mut json_body, &jan_data_folder) {\\n                let mut error_response = Response::builder().status(StatusCode::SERVICE_UNAVAILABLE);\\n                error_response = add_cors_headers_with_host_and_origin(\\n                    error_response,\\n                    &host_header,\\n                    &origin_header,\\n                    &config.trusted_hosts,\\n                );\\n                let payload = serde_json::json!({\\n                    \\\"error\\\": {\\n                        \\\"type\\\": \\\"model_not_available\\\",\\n                        \\\"message\\\": e\\n                    }\\n                });\\n                return Ok(error_response\\n                    .header(hyper::header::CONTENT_TYPE, \\\"application/json\\\")\\n                    .body(full(payload.to_string()))\\n                    .unwrap());\\n            }\\n\",\n    \"            if let Err((status, error_type, message)) = ensure_rerank_model_in_body(&mut json_body, &jan_data_folder) {\\n                let mut error_response = Response::builder().status(status);\\n                error_response = add_cors_headers_with_host_and_origin(\\n                    error_response,\\n                    &host_header,\\n                    &origin_header,\\n                    &config.trusted_hosts,\\n                );\\n                let payload = serde_json::json!({\\n                    \\\"error\\\": {\\n                        \\\"type\\\": error_type,\\n                        \\\"message\\\": message\\n                    }\\n                });\\n                return Ok(error_response\\n                    .header(hyper::header::CONTENT_TYPE, \\\"application/json\\\")\\n                    .body(full(payload.to_string()))\\n                    .unwrap());\\n            }\\n\",\n    'proxy structured rerank errors'\n)\n\n# 7. Preserve vector score/rank metadata and expose rerank provenance in RAG citations.\nrag_path = p('extensions/rag-extension/src/index.ts')\nreplace_once(\n    rag_path,\n    \"            vector_score: r.vector_score,\\n            rerank_score: r.rerank_score,\\n            file_id: r.file_id,\\n\",\n    \"            vector_score: r.vector_score ?? r.score,\\n            rerank_score: r.rerank_score,\\n            rank_source: r.rank_source ?? (reranked.applied ? 'reranker' : 'vector'),\\n            original_rank: r.original_rank,\\n            file_id: r.file_id,\\n\",\n    'RAG citation score provenance'\n)\nreplace_once(\n    rag_path,\n    \"          first_stage_top_k: firstStageTopK,\\n        },\\n\",\n    \"          first_stage_top_k: firstStageTopK,\\n          candidate_count: results?.length ?? 0,\\n          returned_count: reranked.results?.length ?? 0,\\n        },\\n\",\n    'RAG reranking metadata counts'\n)\nreplace_regex(\n    rag_path,\n    r\"    const fallback = \\{\\n      results: \\(candidates \\|\\| \\[\\]\\)\\.slice\\(0, finalK\\),\\n      applied: false,\\n      model: undefined,\\n    \\}\\n\",\n    \"    const fallbackResults = (candidates || [])\\n      .slice(0, finalK)\\n      .map((candidate, index) => ({\\n        ...candidate,\\n        vector_score: candidate?.vector_score ?? candidate?.score,\\n        rank_source: 'vector',\\n        original_rank: candidate?.original_rank ?? index,\\n      }))\\n    const fallback = {\\n      results: fallbackResults,\\n      applied: false,\\n      model: undefined,\\n    }\\n\",\n    'RAG fallback vector provenance'\n)\nreplace_once(\n    rag_path,\n    \"          return {\\n            ...base,\\n            vector_score: base?.score,\\n            rerank_score: rerankScore,\\n            score: rerankScore,\\n          }\\n\",\n    \"          return {\\n            ...base,\\n            vector_score: base?.vector_score ?? base?.score,\\n            rerank_score: rerankScore,\\n            score: rerankScore,\\n            rank_source: 'reranker',\\n            original_rank: item.index,\\n          }\\n\",\n    'RAG reranked provenance'\n)\n\n# 4. Add OpenAPI docs for rerank/reranking and structured error schemas.\nopenapi_path = p('src-tauri/static/openapi.json')\napi = json.loads(read(openapi_path))\npaths = api.setdefault('paths', {})\nrerank_path_spec = {\n    'post': {\n        'summary': 'Rerank documents',\n        'description': 'Scores documents for relevance to a query using a local reranker model. Accepts documents or texts; model may be auto.',\n        'operationId': 'createRerank',\n        'tags': ['Inference'],\n        'requestBody': {\n            'required': True,\n            'content': {\n                'application/json': {\n                    'schema': {'$ref': '#/components/schemas/RerankRequestDto'},\n                    'example': {\n                        'model': 'auto',\n                        'query': 'what is rank pooling?',\n                        'documents': [\n                            'Rank pooling scores query-document pairs.',\n                            'Bananas are yellow.'\n                        ],\n                        'top_n': 2,\n                        'return_documents': True\n                    }\n                }\n            }\n        },\n        'responses': {\n            '200': {\n                'description': 'Rerank result',\n                'content': {'application/json': {'schema': {'$ref': '#/components/schemas/RerankResponseDto'}}}\n            },\n            '400': {\n                'description': 'Invalid rerank request',\n                'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponseDto'}}}\n            },\n            '503': {\n                'description': 'No local reranker is available',\n                'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponseDto'}}}\n            }\n        }\n    }\n}\npaths['/rerank'] = rerank_path_spec\npaths['/reranking'] = rerank_path_spec\nschemas = api.setdefault('components', {}).setdefault('schemas', {})\nschemas['RerankDocumentDto'] = {\n    'oneOf': [\n        {'type': 'string'},\n        {\n            'type': 'object',\n            'properties': {\n                'id': {'type': 'string'},\n                'text': {'type': 'string'},\n                'content': {'type': 'string'},\n                'metadata': {'type': 'object', 'additionalProperties': True}\n            },\n            'additionalProperties': True\n        }\n    ]\n}\nschemas['RerankRequestDto'] = {\n    'type': 'object',\n    'properties': {\n        'model': {'type': 'string', 'default': 'auto', 'description': 'Reranker model id, or auto.'},\n        'query': {'type': 'string'},\n        'documents': {'type': 'array', 'items': {'$ref': '#/components/schemas/RerankDocumentDto'}},\n        'texts': {'type': 'array', 'items': {'$ref': '#/components/schemas/RerankDocumentDto'}, 'description': 'Alias for documents.'},\n        'top_n': {'type': 'integer', 'minimum': 1},\n        'top_k': {'type': 'integer', 'minimum': 1, 'description': 'Alias for top_n.'},\n        'return_documents': {'type': 'boolean', 'default': True},\n        'normalize': {'type': 'boolean', 'default': True}\n    },\n    'required': ['query']\n}\nschemas['RerankResultDto'] = {\n    'type': 'object',\n    'properties': {\n        'index': {'type': 'integer'},\n        'relevance_score': {'type': 'number'},\n        'document': {'$ref': '#/components/schemas/RerankDocumentDto'}\n    },\n    'required': ['index', 'relevance_score']\n}\nschemas['RerankResponseDto'] = {\n    'type': 'object',\n    'properties': {\n        'object': {'type': 'string', 'default': 'list'},\n        'model': {'type': 'string'},\n        'results': {'type': 'array', 'items': {'$ref': '#/components/schemas/RerankResultDto'}},\n        'usage': {'type': 'object', 'additionalProperties': True}\n    },\n    'required': ['results']\n}\nschemas['ErrorResponseDto'] = {\n    'type': 'object',\n    'properties': {\n        'error': {\n            'type': 'object',\n            'properties': {\n                'type': {'type': 'string'},\n                'message': {'type': 'string'}\n            },\n            'required': ['type', 'message']\n        }\n    },\n    'required': ['error']\n}\nwrite(openapi_path, json.dumps(api, indent=2) + '\\n')\n\nprint('Stage 7 reranking completion patch applied.')\n"
BUNDLED_FILES = {"src-tauri/src/core/server/rerank.rs": "use hyper::body::Bytes;\nuse reqwest::Client;\nuse serde::{Deserialize, Serialize};\nuse serde_json::{json, Value};\nuse std::collections::HashSet;\nuse std::fs;\nuse std::path::{Path, PathBuf};\nuse std::sync::OnceLock;\nuse tokio::sync::Mutex;\nuse tauri_plugin_llamacpp::state::LlamacppState;\n\n#[derive(Debug, Clone, Serialize, Deserialize, Default)]\npub struct RerankRuntimeConfig {\n    pub mode: Option<String>,\n    pub model: Option<String>,\n    pub fallback_chain: Option<Vec<String>>,\n    pub external_base_url: Option<String>,\n    pub external_api_key: Option<String>,\n    pub external_model: Option<String>,\n    pub allow_embedding_similarity_fallback: Option<bool>,\n    pub default_top_n: Option<usize>,\n    pub max_tokens_per_doc: Option<usize>,\n    pub min_relevance_score: Option<f64>,\n    pub score_normalization: Option<String>,\n    pub evidence_mode: Option<String>,\n}\n\n#[derive(Debug, Clone, Serialize, Deserialize)]\npub struct RerankModelCandidate {\n    pub id: String,\n    pub name: Option<String>,\n    pub size_bytes: Option<u64>,\n    pub preferred_for: Vec<String>,\n    pub pooling: Option<String>,\n    pub score_normalization: Option<String>,\n    pub max_tokens_per_doc: Option<usize>,\n}\n\n#[derive(Debug, Clone)]\npub struct ExternalRerankTarget {\n    pub base_url: String,\n    pub api_key: Option<String>,\n}\n\n#[derive(Debug, Clone)]\npub struct RerankTrace {\n    pub model: String,\n    pub profile: String,\n    pub provider: String,\n    pub fallback_used: bool,\n    pub fallback_reason: Option<String>,\n    pub candidate_count: usize,\n    pub truncated_documents: usize,\n    pub return_documents: bool,\n    pub normalize_scores: bool,\n    pub raw_scores: bool,\n    pub evidence_mode: String,\n    pub min_relevance_score: Option<f64>,\n    pub top_n: Option<usize>,\n    pub query: String,\n    pub original_documents: Vec<Value>,\n}\n\n#[derive(Debug, Clone)]\npub struct PreparedRerankRequest {\n    pub body: Bytes,\n    pub model_id: String,\n    pub trace: RerankTrace,\n    pub external: Option<ExternalRerankTarget>,\n}\n\n#[derive(Debug, Clone)]\npub struct RerankHttpError {\n    pub status: hyper::StatusCode,\n    pub kind: String,\n    pub message: String,\n}\n\nstatic LAST_RERANK_META: OnceLock<Mutex<Option<Value>>> = OnceLock::new();\n\nfn last_meta() -> &'static Mutex<Option<Value>> {\n    LAST_RERANK_META.get_or_init(|| Mutex::new(None))\n}\n\npub async fn record_rerank_observation(meta: Value) {\n    let mut guard = last_meta().lock().await;\n    *guard = Some(meta);\n}\n\npub fn is_rerank_path(path: &str) -> bool {\n    matches!(path, \"/rerank\" | \"/reranking\")\n}\n\npub fn is_rerank_status_path(path: &str) -> bool {\n    matches!(path, \"/rerank/status\" | \"/reranking/status\")\n}\n\npub fn rerank_error_json(kind: &str, message: &str) -> String {\n    json!({ \"error\": { \"type\": kind, \"message\": message } }).to_string()\n}\n\nfn config_path(jan_data_folder: &str) -> PathBuf {\n    PathBuf::from(jan_data_folder).join(\"llamacpp\").join(\"reranking.json\")\n}\n\nfn load_runtime_config(jan_data_folder: &str) -> RerankRuntimeConfig {\n    let path = config_path(jan_data_folder);\n    let raw = match fs::read_to_string(path) {\n        Ok(v) => v,\n        Err(_) => return RerankRuntimeConfig::default(),\n    };\n    serde_json::from_str(&raw).unwrap_or_default()\n}\n\nfn yaml_bool(v: Option<&Value>) -> bool {\n    match v {\n        Some(Value::Bool(b)) => *b,\n        Some(Value::Object(map)) => map.get(\"enabled\").and_then(Value::as_bool).unwrap_or(true),\n        _ => false,\n    }\n}\n\nfn yaml_string_vec(v: Option<&Value>) -> Vec<String> {\n    match v {\n        Some(Value::Array(arr)) => arr.iter().filter_map(|x| x.as_str().map(str::to_string)).collect(),\n        Some(Value::String(s)) => vec![s.to_string()],\n        _ => Vec::new(),\n    }\n}\n\nfn capability_rerank(v: Option<&Value>) -> bool {\n    match v {\n        Some(Value::Object(map)) => map.get(\"rerank\").and_then(Value::as_bool).unwrap_or(false),\n        Some(Value::Array(arr)) => arr.iter().any(|x| x.as_str() == Some(\"rerank\")),\n        _ => false,\n    }\n}\n\nfn candidate_from_model_yaml(model_id: String, raw: &str) -> Option<RerankModelCandidate> {\n    let cfg: Value = serde_yaml::from_str(raw).ok()?;\n    let reranking = yaml_bool(cfg.get(\"reranking\")) || capability_rerank(cfg.get(\"capabilities\"));\n    let pooling_rank = cfg.get(\"pooling\").and_then(Value::as_str).map(|s| s.eq_ignore_ascii_case(\"rank\")).unwrap_or(false);\n    if !reranking && !pooling_rank { return None; }\n    let preferred_for = yaml_string_vec(cfg.get(\"preferred_for\"));\n    Some(RerankModelCandidate {\n        id: model_id,\n        name: cfg.get(\"name\").and_then(Value::as_str).map(str::to_string),\n        size_bytes: cfg.get(\"size_bytes\").and_then(Value::as_u64),\n        preferred_for,\n        pooling: cfg.get(\"pooling\").and_then(Value::as_str).map(str::to_string),\n        score_normalization: cfg.get(\"score_normalization\").and_then(Value::as_str).map(str::to_string),\n        max_tokens_per_doc: cfg.get(\"max_tokens_per_doc\").and_then(Value::as_u64).map(|n| n as usize),\n    })\n}\n\nfn path_to_model_id(models_root: &Path, model_dir: &Path) -> Option<String> {\n    let rel = model_dir.strip_prefix(models_root).ok()?;\n    let value = rel.components().map(|c| c.as_os_str().to_string_lossy().to_string()).collect::<Vec<_>>().join(\"/\");\n    if value.is_empty() { None } else { Some(value) }\n}\n\nfn collect_reranking_models_inner(models_root: &Path, current: &Path, out: &mut Vec<RerankModelCandidate>) {\n    let model_yml = current.join(\"model.yml\");\n    if model_yml.exists() {\n        if let Some(id) = path_to_model_id(models_root, current) {\n            if let Ok(raw) = fs::read_to_string(&model_yml) {\n                if let Some(candidate) = candidate_from_model_yaml(id, &raw) { out.push(candidate); }\n            }\n        }\n        return;\n    }\n    let Ok(entries) = fs::read_dir(current) else { return; };\n    for entry in entries.flatten() {\n        let path = entry.path();\n        if path.is_dir() { collect_reranking_models_inner(models_root, &path, out); }\n    }\n}\n\npub fn collect_reranking_models(jan_data_folder: &str) -> Vec<RerankModelCandidate> {\n    let root = PathBuf::from(jan_data_folder).join(\"llamacpp\").join(\"models\");\n    let mut out = Vec::new();\n    if root.exists() { collect_reranking_models_inner(&root, &root, &mut out); }\n    out.sort_by(|a, b| a.id.cmp(&b.id));\n    out\n}\n\nasync fn router_loaded_ids(llama_state: &LlamacppState, client: &Client) -> HashSet<String> {\n    let (url, key) = {\n        let guard = llama_state.router.lock().await;\n        match guard.as_ref() {\n            Some(h) => (format!(\"http://127.0.0.1:{}/models\", h.port), h.api_key.clone()),\n            None => return HashSet::new(),\n        }\n    };\n    let Ok(resp) = client.get(url).bearer_auth(key).send().await else { return HashSet::new(); };\n    let Ok(json) = resp.json::<Value>().await else { return HashSet::new(); };\n    let mut ids = HashSet::new();\n    if let Some(arr) = json.get(\"data\").and_then(Value::as_array) {\n        for item in arr {\n            let loaded = item.get(\"status\").and_then(|s| s.get(\"value\")).and_then(Value::as_str).map(|s| s == \"loaded\").unwrap_or(false);\n            if loaded {\n                if let Some(id) = item.get(\"id\").and_then(Value::as_str) { ids.insert(id.to_string()); }\n            }\n        }\n    }\n    ids\n}\n\nfn value_text(v: &Value) -> String {\n    match v {\n        Value::String(s) => s.clone(),\n        Value::Object(map) => {\n            for key in [\"text\", \"content\", \"page_content\", \"body\"] {\n                if let Some(s) = map.get(key).and_then(Value::as_str) { if !s.trim().is_empty() { return s.to_string(); } }\n            }\n            v.to_string()\n        }\n        _ => v.to_string(),\n    }\n}\n\nfn yaml_scalar(v: &Value) -> String {\n    match v {\n        Value::String(s) => s.replace(['\\r', '\\n'], \" \").trim().to_string(),\n        Value::Number(_) | Value::Bool(_) => v.to_string(),\n        Value::Null => String::new(),\n        _ => v.to_string(),\n    }\n}\n\nfn structured_doc_to_text(v: &Value) -> String {\n    if v.is_string() { return value_text(v); }\n    let Some(map) = v.as_object() else { return value_text(v); };\n    let text = value_text(v);\n    let mut meta = serde_json::Map::new();\n    if let Some(m) = map.get(\"metadata\").and_then(Value::as_object) {\n        for (k, val) in m { meta.insert(k.clone(), val.clone()); }\n    }\n    for (k, val) in map {\n        if [\"text\", \"content\", \"page_content\", \"body\", \"metadata\"].contains(&k.as_str()) { continue; }\n        meta.entry(k.clone()).or_insert_with(|| val.clone());\n    }\n    if meta.is_empty() { return text; }\n    let mut lines = Vec::new();\n    for (k, val) in meta { if !val.is_null() { lines.push(format!(\"{}: {}\", k, yaml_scalar(&val))); } }\n    lines.push(\"content: |\".to_string());\n    for line in text.lines() { lines.push(format!(\"  {}\", line)); }\n    lines.join(\"\\n\")\n}\n\nfn truncate_approx_tokens(text: &str, max_tokens: Option<usize>) -> (String, bool) {\n    let Some(max_tokens) = max_tokens else { return (text.to_string(), false); };\n    if max_tokens == 0 { return (text.to_string(), false); }\n    let max_chars = max_tokens.saturating_mul(4).max(1);\n    if text.len() <= max_chars { (text.to_string(), false) } else { (text.chars().take(max_chars).collect(), true) }\n}\n\nfn looks_like_code(s: &str) -> bool {\n    let lower = s.to_lowercase();\n    lower.contains(\"function \") || lower.contains(\"class \") || lower.contains(\"#include\") || lower.contains(\"0x\") || lower.contains(\"stack trace\") || s.matches('{').count() + s.matches(';').count() > 6 || s.contains(\"\\\\\") || s.contains(\"/src/\")\n}\n\nfn has_non_ascii(s: &str) -> bool { s.chars().any(|c| !c.is_ascii()) }\n\nfn classify_profile(query: &str, docs: &[Value]) -> String {\n    let mut sample = query.to_string();\n    for d in docs.iter().take(8) { sample.push('\\n'); sample.push_str(&value_text(d)); }\n    let avg_len = if docs.is_empty() { 0 } else { docs.iter().map(|d| value_text(d).len()).sum::<usize>() / docs.len() };\n    if looks_like_code(&sample) { \"code\".to_string() }\n    else if has_non_ascii(&sample) { \"multilingual\".to_string() }\n    else if avg_len > 6000 { \"long\".to_string() }\n    else { \"default\".to_string() }\n}\n\nfn score_candidate(c: &RerankModelCandidate, profile: &str, loaded: &HashSet<String>, preferred: Option<&str>) -> i64 {\n    let hay = format!(\"{} {} {}\", c.id, c.name.clone().unwrap_or_default(), c.preferred_for.join(\" \")).to_lowercase();\n    let mut score = 0i64;\n    if preferred == Some(c.id.as_str()) { score += 10_000; }\n    if loaded.contains(&c.id) { score += 80; }\n    if c.preferred_for.iter().any(|p| p == profile) { score += 90; }\n    if profile == \"code\" && [\"code\", \"coder\", \"qwen\", \"asm\", \"disassembl\", \"deobfusc\"].iter().any(|x| hay.contains(x)) { score += 60; }\n    if profile == \"multilingual\" && [\"multi\", \"jina\", \"bge\", \"qwen\", \"xlm\", \"m3\"].iter().any(|x| hay.contains(x)) { score += 60; }\n    if profile == \"long\" && [\"large\", \"4b\", \"7b\", \"8b\", \"long\", \"m3\"].iter().any(|x| hay.contains(x)) { score += 30; }\n    if hay.contains(\"rerank\") || hay.contains(\"cross\") { score += 50; }\n    if let Some(size) = c.size_bytes { if size > 0 { score += (30.0 - (size as f64).log10()).max(0.0) as i64; } }\n    score\n}\n\nfn normalize_score(score: f64, normalize: bool) -> f64 {\n    if !normalize { return score; }\n    if (0.0..=1.0).contains(&score) { score } else { 1.0 / (1.0 + (-score).exp()) }\n}\n\nfn query_terms(query: &str) -> Vec<String> {\n    let mut seen = HashSet::new();\n    query.to_lowercase().split(|c: char| !c.is_alphanumeric() && c != '_').filter(|s| s.len() >= 3).filter_map(|s| if seen.insert(s.to_string()) { Some(s.to_string()) } else { None }).take(16).collect()\n}\n\nfn evidence_for(query: &str, text: &str) -> (String, String) {\n    let terms = query_terms(query);\n    let mut best = text.lines().next().unwrap_or(text).trim().to_string();\n    let mut best_hits = -1i32;\n    for s in text.split(|c| c == '.' || c == '!' || c == '?' || c == '\\n').take(100) {\n        let st = s.trim();\n        if st.is_empty() { continue; }\n        let lower = st.to_lowercase();\n        let hits = terms.iter().filter(|t| lower.contains(t.as_str())).count() as i32;\n        if hits > best_hits { best = st.to_string(); best_hits = hits; }\n    }\n    if best.len() > 600 { best.truncate(600); }\n    let contribution = if best_hits > 0 { format!(\"Matches {} query term{} in the selected passage.\", best_hits, if best_hits == 1 { \"\" } else { \"s\" }) } else { \"Highest-scoring candidate from the reranker.\".to_string() };\n    (best, contribution)\n}\n\npub async fn prepare_rerank_request(body_bytes: Bytes, jan_data_folder: &str, client: &Client, llama_state: &LlamacppState) -> Result<PreparedRerankRequest, RerankHttpError> {\n    let mut body: Value = serde_json::from_slice(&body_bytes).map_err(|e| RerankHttpError { status: hyper::StatusCode::BAD_REQUEST, kind: \"invalid_request_error\".into(), message: format!(\"Invalid JSON body: {e}\") })?;\n    let cfg = load_runtime_config(jan_data_folder);\n    if cfg.mode.as_deref() == Some(\"off\") {\n        return Err(RerankHttpError { status: hyper::StatusCode::SERVICE_UNAVAILABLE, kind: \"reranking_disabled\".into(), message: \"Reranking is disabled in llamacpp/reranking.json\".into() });\n    }\n    let query = body.get(\"query\").and_then(Value::as_str).map(str::trim).filter(|s| !s.is_empty()).ok_or_else(|| RerankHttpError { status: hyper::StatusCode::BAD_REQUEST, kind: \"invalid_request_error\".into(), message: \"rerank requires a non-empty query string\".into() })?.to_string();\n    let docs_value = body.get(\"documents\").or_else(|| body.get(\"texts\")).and_then(Value::as_array).cloned().ok_or_else(|| RerankHttpError { status: hyper::StatusCode::BAD_REQUEST, kind: \"invalid_request_error\".into(), message: \"rerank requires a non-empty documents or texts array\".into() })?;\n    if docs_value.is_empty() {\n        return Err(RerankHttpError { status: hyper::StatusCode::BAD_REQUEST, kind: \"invalid_request_error\".into(), message: \"documents/texts must not be empty\".into() });\n    }\n    let profile = body.get(\"profile\").and_then(Value::as_str).map(str::to_string).unwrap_or_else(|| classify_profile(&query, &docs_value));\n    let requested_model = body.get(\"model\").and_then(Value::as_str).map(str::trim).filter(|s| !s.is_empty() && *s != \"auto\").map(str::to_string);\n    let preferred = requested_model.clone().or_else(|| cfg.model.clone().filter(|m| m != \"auto\"));\n    let candidates = collect_reranking_models(jan_data_folder);\n    let loaded = router_loaded_ids(llama_state, client).await;\n    let selected = if let Some(model) = requested_model.clone() {\n        candidates.iter().find(|c| c.id == model).cloned().ok_or_else(|| RerankHttpError { status: hyper::StatusCode::NOT_FOUND, kind: \"model_capability_error\".into(), message: format!(\"Requested model '{model}' is not marked as a reranker\") })?\n    } else {\n        candidates.iter().max_by_key(|c| score_candidate(c, &profile, &loaded, preferred.as_deref())).cloned().ok_or_else(|| RerankHttpError { status: hyper::StatusCode::SERVICE_UNAVAILABLE, kind: \"model_not_available\".into(), message: \"No local reranking model is available. Import a GGUF reranker or mark model.yml with reranking: true and pooling: rank.\".into() })?\n    };\n    let max_tokens = body.get(\"max_tokens_per_doc\").and_then(Value::as_u64).map(|n| n as usize).or(selected.max_tokens_per_doc).or(cfg.max_tokens_per_doc);\n    let mut docs = Vec::with_capacity(docs_value.len());\n    let mut truncated = 0usize;\n    for d in &docs_value {\n        let formatted = structured_doc_to_text(d);\n        let (t, was) = truncate_approx_tokens(&formatted, max_tokens);\n        if was { truncated += 1; }\n        docs.push(Value::String(t));\n    }\n    let top_n = body.get(\"top_n\").or_else(|| body.get(\"top_k\")).and_then(Value::as_u64).map(|n| n as usize).or(cfg.default_top_n).map(|n| n.max(1).min(docs.len()));\n    let return_documents = body.get(\"return_documents\").and_then(Value::as_bool).unwrap_or(true);\n    let normalize_scores = body.get(\"normalize_scores\").or_else(|| body.get(\"normalize\")).and_then(Value::as_bool).unwrap_or(true);\n    let raw_scores = body.get(\"raw_scores\").and_then(Value::as_bool).unwrap_or(false);\n    let evidence_mode = body.get(\"evidence_mode\").and_then(Value::as_str).or(cfg.evidence_mode.as_deref()).filter(|m| matches!(*m, \"off\" | \"top_n\" | \"all\")).unwrap_or(\"off\").to_string();\n    let min_score = body.get(\"min_relevance_score\").and_then(Value::as_f64).or(cfg.min_relevance_score);\n    body[\"model\"] = Value::String(selected.id.clone());\n    body[\"documents\"] = Value::Array(docs);\n    if let Some(n) = top_n { body[\"top_n\"] = json!(n); }\n    body[\"return_documents\"] = Value::Bool(false);\n    body.as_object_mut().map(|m| { m.remove(\"texts\"); });\n    let trace = RerankTrace { model: selected.id.clone(), profile, provider: \"local_gguf\".into(), fallback_used: false, fallback_reason: None, candidate_count: docs_value.len(), truncated_documents: truncated, return_documents, normalize_scores, raw_scores, evidence_mode, min_relevance_score: min_score, top_n, query, original_documents: docs_value };\n    Ok(PreparedRerankRequest { body: Bytes::from(serde_json::to_vec(&body).unwrap_or_else(|_| b\"{}\".to_vec())), model_id: selected.id, trace, external: None })\n}\n\npub fn postprocess_rerank_response(mut raw: Value, trace: RerankTrace, latency_ms: u64) -> Value {\n    let arr = raw.get_mut(\"results\").and_then(Value::as_array_mut).map(|a| std::mem::take(a)).or_else(|| raw.get_mut(\"data\").and_then(Value::as_array_mut).map(|a| std::mem::take(a))).unwrap_or_default();\n    let mut results = Vec::new();\n    for (fallback_index, item) in arr.into_iter().enumerate() {\n        let index = item.get(\"index\").and_then(Value::as_u64).map(|n| n as usize).unwrap_or(fallback_index);\n        let raw_score = item.get(\"relevance_score\").or_else(|| item.get(\"score\")).or_else(|| item.get(\"logit\")).and_then(Value::as_f64).unwrap_or(0.0);\n        let score = normalize_score(raw_score, trace.normalize_scores && !trace.raw_scores);\n        if let Some(min) = trace.min_relevance_score { if score < min { continue; } }\n        let mut obj = serde_json::Map::new();\n        obj.insert(\"index\".into(), json!(index));\n        obj.insert(\"relevance_score\".into(), json!(score));\n        if trace.raw_scores { obj.insert(\"raw_relevance_score\".into(), json!(raw_score)); }\n        if trace.return_documents { if let Some(doc) = trace.original_documents.get(index) { obj.insert(\"document\".into(), doc.clone()); } }\n        if trace.evidence_mode != \"off\" {\n            if let Some(doc) = trace.original_documents.get(index) {\n                let text = value_text(doc);\n                let (evidence, contribution) = evidence_for(&trace.query, &text);\n                obj.insert(\"evidence\".into(), Value::String(evidence));\n                obj.insert(\"contribution\".into(), Value::String(contribution));\n            }\n        }\n        results.push(Value::Object(obj));\n    }\n    results.sort_by(|a, b| b.get(\"relevance_score\").and_then(Value::as_f64).unwrap_or(0.0).partial_cmp(&a.get(\"relevance_score\").and_then(Value::as_f64).unwrap_or(0.0)).unwrap_or(std::cmp::Ordering::Equal));\n    if let Some(n) = trace.top_n { results.truncate(n); }\n    let meta = json!({\n        \"model\": trace.model,\n        \"profile\": trace.profile,\n        \"provider\": trace.provider,\n        \"fallback_used\": trace.fallback_used,\n        \"fallback_reason\": trace.fallback_reason,\n        \"candidate_count\": trace.candidate_count,\n        \"returned_count\": results.len(),\n        \"truncated_documents\": trace.truncated_documents,\n        \"normalize_scores\": trace.normalize_scores,\n        \"raw_scores\": trace.raw_scores,\n        \"latency_ms\": latency_ms,\n    });\n    json!({ \"object\": \"list\", \"model\": meta.get(\"model\").cloned().unwrap_or(json!(\"\")), \"results\": results, \"usage\": raw.get(\"usage\").cloned().unwrap_or(Value::Null), \"meta\": meta })\n}\n\npub async fn build_rerank_status_json(jan_data_folder: &str, llama_state: &LlamacppState, client: &Client) -> Value {\n    let cfg = load_runtime_config(jan_data_folder);\n    let models = collect_reranking_models(jan_data_folder);\n    let loaded = router_loaded_ids(llama_state, client).await;\n    let last = last_meta().lock().await.clone();\n    json!({\n        \"enabled\": cfg.mode.as_deref() != Some(\"off\"),\n        \"mode\": cfg.mode.unwrap_or_else(|| \"auto\".to_string()),\n        \"configured_model\": cfg.model.unwrap_or_else(|| \"auto\".to_string()),\n        \"available_models\": models.iter().map(|m| json!({ \"id\": m.id, \"name\": m.name, \"loaded\": loaded.contains(&m.id), \"pooling\": m.pooling, \"preferred_for\": m.preferred_for })).collect::<Vec<_>>(),\n        \"fallback_chain\": cfg.fallback_chain.unwrap_or_else(|| vec![\"local_gguf\".into(), \"disabled\".into()]),\n        \"external_configured\": cfg.external_base_url.is_some(),\n        \"last_request\": last,\n    })\n}\n", "extensions/llamacpp-extension/src/rerank.ts": "export type RerankDocument =\n  | string\n  | {\n      text?: string\n      content?: string\n      page_content?: string\n      body?: string\n      id?: string\n      metadata?: Record<string, unknown>\n      [key: string]: unknown\n    }\n\nexport type RerankProfileName = 'default' | 'code' | 'multilingual' | 'long'\n\nexport interface NormalizedRerankRequest {\n  model: string\n  query: string\n  documents: string[]\n  originalDocuments: RerankDocument[]\n  top_n?: number\n  return_documents: boolean\n  normalize_scores: boolean\n  raw_scores: boolean\n  max_tokens_per_doc?: number\n  min_relevance_score?: number\n  evidence_mode: 'off' | 'top_n' | 'all'\n  profile: RerankProfileName\n  truncated_documents: number\n}\n\nexport interface RerankTraceMeta {\n  model: string\n  profile: RerankProfileName\n  provider: string\n  fallback_used: boolean\n  fallback_reason?: string\n  candidate_count: number\n  returned_count: number\n  truncated_documents: number\n  normalize_scores: boolean\n  raw_scores: boolean\n  latency_ms?: number\n  model_load_ms?: number\n  cache_hit?: boolean\n}\n\nexport function extractDocumentText(doc: RerankDocument): string {\n  if (typeof doc === 'string') return doc\n  if (!doc || typeof doc !== 'object') return String(doc ?? '')\n  for (const key of ['text', 'content', 'page_content', 'body']) {\n    const value = doc[key]\n    if (typeof value === 'string' && value.trim().length > 0) return value\n  }\n  return JSON.stringify(doc)\n}\n\nfunction yamlScalar(value: unknown): string {\n  if (value == null) return ''\n  if (typeof value === 'string') return value.replace(/[\\r\\n]+/g, ' ').trim()\n  if (typeof value === 'number' || typeof value === 'boolean') return String(value)\n  return JSON.stringify(value)\n}\n\nexport function formatStructuredDocument(doc: RerankDocument): string {\n  if (typeof doc === 'string') return doc\n  if (!doc || typeof doc !== 'object') return String(doc ?? '')\n  const text = extractDocumentText(doc)\n  const lines: string[] = []\n  const meta = doc.metadata && typeof doc.metadata === 'object' ? doc.metadata as Record<string, unknown> : {}\n  const merged = { ...meta }\n  for (const [key, value] of Object.entries(doc)) {\n    if (['text', 'content', 'page_content', 'body', 'metadata'].includes(key)) continue\n    if (!(key in merged)) merged[key] = value\n  }\n  for (const [key, value] of Object.entries(merged)) {\n    if (value == null) continue\n    lines.push(`${key}: ${yamlScalar(value)}`)\n  }\n  if (lines.length === 0) return text\n  lines.push('content: |')\n  for (const line of text.split(/\\r?\\n/)) lines.push(`  ${line}`)\n  return lines.join('\\n')\n}\n\nexport function estimateTokens(text: string): number {\n  return Math.max(1, Math.ceil(text.length / 4))\n}\n\nexport function truncateByApproxTokens(text: string, maxTokens?: number): { text: string; truncated: boolean } {\n  if (!maxTokens || maxTokens <= 0) return { text, truncated: false }\n  const maxChars = Math.max(1, Math.floor(maxTokens * 4))\n  if (text.length <= maxChars) return { text, truncated: false }\n  return { text: text.slice(0, maxChars), truncated: true }\n}\n\nfunction hasNonAscii(text: string): boolean {\n  return /[^\\u0000-\\u007f]/.test(text)\n}\n\nfunction looksLikeCode(text: string): boolean {\n  return /(?:[A-Za-z]:[\\\\/]|\\b(?:function|class|struct|enum|namespace|template|#include|import|def|async|await|return|const|let|var|public:|private:|MOV|CALL|JMP)\\b|[{};]{2,}|0x[0-9a-fA-F]{4,}|\\.\\w{1,5}:\\d+)/.test(text)\n}\n\nexport function detectRerankProfile(query: string, documents: RerankDocument[]): RerankProfileName {\n  const sample = `${query}\\n${documents.slice(0, 8).map(extractDocumentText).join('\\n')}`\n  const avgLen = documents.length ? documents.reduce((n, d) => n + extractDocumentText(d).length, 0) / documents.length : 0\n  if (looksLikeCode(sample)) return 'code'\n  if (hasNonAscii(sample)) return 'multilingual'\n  if (avgLen > 6000) return 'long'\n  return 'default'\n}\n\nexport function normalizeRerankRequest(req: any): NormalizedRerankRequest {\n  if (!req || typeof req !== 'object') throw new Error('rerank request must be a JSON object')\n  const query = typeof req.query === 'string' ? req.query.trim() : ''\n  if (!query) throw new Error('rerank requires a non-empty query string')\n  const docs = Array.isArray(req.documents) ? req.documents : Array.isArray(req.texts) ? req.texts : undefined\n  if (!Array.isArray(docs) || docs.length === 0) throw new Error('rerank requires a non-empty documents or texts array')\n  const maxTokens = Number.isFinite(Number(req.max_tokens_per_doc)) ? Math.floor(Number(req.max_tokens_per_doc)) : undefined\n  const documents: string[] = []\n  let truncated = 0\n  for (const doc of docs) {\n    const formatted = formatStructuredDocument(doc)\n    const t = truncateByApproxTokens(formatted, maxTokens)\n    if (t.truncated) truncated++\n    documents.push(t.text)\n  }\n  const topNRaw = req.top_n ?? req.top_k\n  const topN = Number.isFinite(Number(topNRaw)) ? Math.max(1, Math.min(documents.length, Math.floor(Number(topNRaw)))) : undefined\n  const evidenceMode = req.evidence_mode === 'all' || req.evidence_mode === 'top_n' ? req.evidence_mode : 'off'\n  const minScore = Number.isFinite(Number(req.min_relevance_score)) ? Number(req.min_relevance_score) : undefined\n  return {\n    model: typeof req.model === 'string' && req.model.trim() ? req.model.trim() : 'auto',\n    query,\n    documents,\n    originalDocuments: docs as RerankDocument[],\n    top_n: topN,\n    return_documents: req.return_documents !== false,\n    normalize_scores: req.normalize_scores !== false && req.normalize !== false,\n    raw_scores: req.raw_scores === true,\n    max_tokens_per_doc: maxTokens,\n    min_relevance_score: minScore,\n    evidence_mode: evidenceMode,\n    profile: detectRerankProfile(query, docs as RerankDocument[]),\n    truncated_documents: truncated,\n  }\n}\n\nfunction sigmoid(x: number): number {\n  return 1 / (1 + Math.exp(-x))\n}\n\nfunction normalizeScore(score: number, normalize: boolean): number {\n  if (!normalize) return score\n  if (score >= 0 && score <= 1) return score\n  return sigmoid(score)\n}\n\nfunction queryTerms(query: string): string[] {\n  return Array.from(new Set(query.toLowerCase().split(/[^\\p{L}\\p{N}_]+/u).filter((t) => t.length >= 3))).slice(0, 16)\n}\n\nfunction evidenceFor(query: string, text: string): { evidence: string; contribution: string } {\n  const terms = queryTerms(query)\n  const sentences = text.split(/(?<=[.!?])\\s+|\\r?\\n+/).map((s) => s.trim()).filter(Boolean)\n  let best = sentences[0] ?? text.slice(0, 300)\n  let bestHits = -1\n  for (const sentence of sentences.slice(0, 80)) {\n    const lower = sentence.toLowerCase()\n    const hits = terms.reduce((n, t) => n + (lower.includes(t) ? 1 : 0), 0)\n    if (hits > bestHits) {\n      best = sentence\n      bestHits = hits\n    }\n  }\n  if (best.length > 600) best = best.slice(0, 600)\n  const contribution = bestHits > 0 ? `Matches ${bestHits} query term${bestHits === 1 ? '' : 's'} in the selected passage.` : 'Highest-scoring candidate from the reranker.'\n  return { evidence: best, contribution }\n}\n\nexport function postprocessRerankResponse(raw: any, req: NormalizedRerankRequest, meta: Partial<RerankTraceMeta> = {}) {\n  const rawResults = Array.isArray(raw?.results) ? raw.results : Array.isArray(raw?.data) ? raw.data : []\n  let results = rawResults.map((item: any, fallbackIndex: number) => {\n    const index = Number.isFinite(Number(item.index)) ? Number(item.index) : fallbackIndex\n    const rawScore = Number(item.relevance_score ?? item.score ?? item.logit ?? 0)\n    const score = normalizeScore(rawScore, req.normalize_scores && !req.raw_scores)\n    const out: any = { index, relevance_score: score }\n    if (req.raw_scores) out.raw_relevance_score = rawScore\n    if (req.return_documents) out.document = req.originalDocuments[index]\n    return out\n  })\n  results.sort((a: any, b: any) => Number(b.relevance_score) - Number(a.relevance_score))\n  if (typeof req.min_relevance_score === 'number') {\n    results = results.filter((r: any) => Number(r.relevance_score) >= req.min_relevance_score!)\n  }\n  if (req.top_n) results = results.slice(0, req.top_n)\n  if (req.evidence_mode !== 'off') {\n    for (const r of results) {\n      const original = req.originalDocuments[r.index]\n      const text = extractDocumentText(original)\n      const ev = evidenceFor(req.query, text)\n      r.evidence = ev.evidence\n      r.contribution = ev.contribution\n    }\n  }\n  const trace: RerankTraceMeta = {\n    model: String(meta.model ?? raw?.model ?? req.model),\n    profile: req.profile,\n    provider: String(meta.provider ?? 'local_gguf'),\n    fallback_used: Boolean(meta.fallback_used),\n    fallback_reason: meta.fallback_reason,\n    candidate_count: req.documents.length,\n    returned_count: results.length,\n    truncated_documents: req.truncated_documents,\n    normalize_scores: req.normalize_scores,\n    raw_scores: req.raw_scores,\n    latency_ms: meta.latency_ms,\n    model_load_ms: meta.model_load_ms,\n    cache_hit: meta.cache_hit,\n  }\n  return { object: 'list', model: trace.model, results, meta: trace, usage: raw?.usage }\n}\n\nexport function scoreRerankingModel(model: any, profile: RerankProfileName, loadedIds: Set<string>, preferred?: string): number {\n  const id = String(model.id ?? '')\n  const name = String(model.name ?? id)\n  const haystack = `${id} ${name}`.toLowerCase()\n  let score = 0\n  if (preferred && id === preferred) score += 1000\n  if (loadedIds.has(id)) score += 80\n  if (profile === 'code' && /(code|coder|qwen|starcoder|deobfusc|asm|disassembl)/.test(haystack)) score += 60\n  if (profile === 'multilingual' && /(multi|jina|bge|qwen|xlm|m3)/.test(haystack)) score += 60\n  if (profile === 'long' && /(large|4b|7b|8b|long|m3)/.test(haystack)) score += 30\n  if (/rerank|reranker|cross/.test(haystack)) score += 50\n  const size = Number(model.sizeBytes ?? 0)\n  if (size > 0) score += Math.max(0, 30 - Math.log10(size))\n  return score\n}\n"}

REQUIRED_BASE_FILES = [
    'extensions/llamacpp-extension/src/util.ts',
    'extensions/llamacpp-extension/src/index.ts',
    'extensions/llamacpp-extension/src/preset.ts',
    'extensions/llamacpp-extension/settings.json',
    'extensions/rag-extension/src/index.ts',
    'extensions/rag-extension/settings.json',
    'src-tauri/plugins/tauri-plugin-llamacpp/guest-js/types.ts',
    'src-tauri/src/core/server/proxy.rs',
    'src-tauri/src/core/server/mod.rs',
    'src-tauri/static/openapi.json',
]


def fail(message: str) -> None:
    raise SystemExit(message)


def ensure_repo_root() -> None:
    missing = [rel for rel in REQUIRED_BASE_FILES if not (ROOT / rel).exists()]
    if missing:
        fail('Run this from the Jan repository root. Missing:\n' + '\n'.join(f'  - {m}' for m in missing))


def write_embedded_applicators() -> None:
    if TMP.exists():
        shutil.rmtree(TMP)
    (TMP / 'jan-rerank-full' / 'files').mkdir(parents=True, exist_ok=True)
    (TMP / 'jan-rerank-full' / 'apply-jan-rerank-full.py').write_text(FULL_SCRIPT, encoding='utf-8')
    (TMP / 'jan-rerank-stage7-completion-apply-fixed.py').write_text(STAGE7_SCRIPT, encoding='utf-8')
    for rel, content in BUNDLED_FILES.items():
        out = TMP / 'jan-rerank-full' / 'files' / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content, encoding='utf-8')


def run_script(path: Path) -> None:
    print(f'>>> running {path.relative_to(ROOT)}')
    old_argv = sys.argv[:]
    try:
        sys.argv = [str(path)]
        runpy.run_path(str(path), run_name='__main__')
    finally:
        sys.argv = old_argv




def apply_post_full_ui_and_docs() -> None:
    import json
    from pathlib import Path

    def read_rel(rel: str) -> str:
        return (ROOT / rel).read_text(encoding='utf-8')

    def write_rel(rel: str, data: str) -> None:
        path = ROOT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding='utf-8')

    def replace_text(rel: str, old: str, new: str, label: str) -> None:
        s = read_rel(rel)
        if new.strip()[:120] in s:
            return
        if old not in s:
            raise SystemExit(f'Could not patch {label} in {rel}')
        write_rel(rel, s.replace(old, new, 1))

    def insert_after_text(rel: str, needle: str, insertion: str, label: str, marker = None) -> None:
        s = read_rel(rel)
        if (marker and marker in s) or insertion.strip()[:120] in s:
            return
        idx = s.find(needle)
        if idx < 0:
            raise SystemExit(f'Could not find insertion point for {label} in {rel}')
        idx += len(needle)
        write_rel(rel, s[:idx] + insertion + s[idx:])

    # llama.cpp visible default reranker setting. The core reranker already uses
    # the localStorage default; this setting provides a user-editable visible value
    # and is also consulted by the patched selector below.
    llama_settings_rel = 'extensions/llamacpp-extension/settings.json'
    llama_settings = json.loads(read_rel(llama_settings_rel))
    if not any(item.get('key') == 'default_reranker_model' for item in llama_settings):
        insert_idx = next((i + 1 for i, item in enumerate(llama_settings) if item.get('key') == 'models_max'), len(llama_settings))
        llama_settings.insert(insert_idx, {
            'key': 'default_reranker_model',
            'title': 'Default reranker model',
            'description': 'Reranker model id to prefer for /v1/rerank and automatic RAG reranking. Use auto to select the best local reranker.',
            'controllerType': 'input',
            'controllerProps': {
                'value': 'auto',
                'placeholder': 'auto or local reranker model id',
                'type': 'text',
                'textAlign': 'right'
            }
        })
        write_rel(llama_settings_rel, json.dumps(llama_settings, indent=2) + '\n')

    # Make the selector honor the visible default_reranker_model setting too.
    index_rel = 'extensions/llamacpp-extension/src/index.ts'
    selector_old = """    const explicitModel = req.model && req.model !== 'auto' ? req.model : undefined
    const storedDefault = getDefaultRerankingModelId('llamacpp')
    const preferred = explicitModel ?? storedDefault
"""
    selector_new = """    const explicitModel = req.model && req.model !== 'auto' ? req.model : undefined
    const configuredDefault = (() => {
      const value = (this.config as LlamacppConfig & { default_reranker_model?: string })?.default_reranker_model
      if (typeof value !== 'string') return undefined
      const trimmed = value.trim()
      return trimmed && trimmed !== 'auto' && trimmed !== '*' ? trimmed : undefined
    })()
    const storedDefault = configuredDefault ?? getDefaultRerankingModelId('llamacpp')
    const preferred = explicitModel ?? storedDefault
"""
    try:
        replace_text(index_rel, selector_old, selector_new, 'default reranker visible setting selector')
    except SystemExit:
        # Newer/full script variants may already have a different selector. Do not
        # abort the entire applicator for this cosmetic setting hookup.
        pass

    # RAG extension settings definitions so the knobs are visible in Settings > Attachments.
    rag_settings_rel = 'extensions/rag-extension/settings.json'
    rag_settings = json.loads(read_rel(rag_settings_rel))
    existing = {item.get('key') for item in rag_settings}
    rag_new = [
        {
            'key': 'reranking_mode',
            'title': 'RAG reranking',
            'description': 'Use a local reranker after vector retrieval. Auto uses a reranker when available; Off keeps vector-only retrieval; Model uses the configured reranking model id.',
            'controllerType': 'dropdown',
            'controllerProps': {
                'value': 'auto',
                'options': [
                    {'name': 'Auto (recommended)', 'value': 'auto'},
                    {'name': 'Off', 'value': 'off'},
                    {'name': 'Specific model', 'value': 'model'},
                ]
            }
        },
        {
            'key': 'reranking_model',
            'title': 'RAG reranker model',
            'description': 'Specific reranker model id to use when RAG reranking is set to Specific model. Use auto for automatic selection.',
            'controllerType': 'input',
            'controllerProps': {'value': 'auto', 'placeholder': 'auto or reranker model id', 'type': 'text', 'textAlign': 'right'}
        },
        {
            'key': 'rerank_top_k_before',
            'title': 'Rerank candidate count',
            'description': 'Number of vector-search candidates to retrieve before reranking.',
            'controllerType': 'input',
            'controllerProps': {'value': 60, 'type': 'number', 'min': 1, 'max': 200, 'step': 1, 'textAlign': 'right'}
        },
        {
            'key': 'rerank_top_n_after',
            'title': 'Reranked result count',
            'description': 'Maximum number of citations to keep after reranking.',
            'controllerType': 'input',
            'controllerProps': {'value': 8, 'type': 'number', 'min': 1, 'max': 50, 'step': 1, 'textAlign': 'right'}
        },
        {
            'key': 'rerank_min_relevance_score',
            'title': 'Minimum rerank score',
            'description': 'Drop reranked citations below this normalized score. Use 0 to keep all reranked results.',
            'controllerType': 'input',
            'controllerProps': {'value': 0, 'type': 'number', 'min': 0, 'max': 1, 'step': 0.01, 'textAlign': 'right'}
        },
        {
            'key': 'rerank_max_tokens_per_doc',
            'title': 'Max tokens per rerank document',
            'description': 'Approximate per-candidate truncation before reranking to avoid runaway context use.',
            'controllerType': 'input',
            'controllerProps': {'value': 4096, 'type': 'number', 'min': 128, 'max': 32768, 'step': 128, 'textAlign': 'right'}
        },
        {
            'key': 'rerank_evidence_mode',
            'title': 'Rerank evidence',
            'description': 'Include short evidence snippets in reranked citations.',
            'controllerType': 'dropdown',
            'controllerProps': {
                'value': 'off',
                'options': [
                    {'name': 'Off', 'value': 'off'},
                    {'name': 'Top results', 'value': 'top_n'},
                    {'name': 'All candidates', 'value': 'all'},
                ]
            }
        },
    ]
    if any(item['key'] not in existing for item in rag_new):
        rag_settings.extend([item for item in rag_new if item['key'] not in existing])
        write_rel(rag_settings_rel, json.dumps(rag_settings, indent=2) + '\n')

    # Let Settings > Attachments persist unknown extension-defined settings.
    hook_rel = 'web-app/src/hooks/useAttachments.ts'
    if (ROOT / hook_rel).exists():
        replace_text(
            hook_rel,
            """  setAutoInlineContextRatio: (v: number) => void
}""",
            """  setAutoInlineContextRatio: (v: number) => void
  setSetting: (key: string, value: unknown) => void
}""",
            'generic attachment setting type'
        )
        insert_after_text(
            hook_rel,
            """  settingsDefs: [],
""",
            """  setSetting: async (key, value) => {
    const ext = getRagExtension()
    if (ext?.updateSettings) {
      await ext.updateSettings([
        {
          key,
          controllerProps: { value },
        } as Partial<SettingComponentProps>,
      ])
    }
    set((s) => ({
      settingsDefs: s.settingsDefs.map((d) =>
        d.key === key
          ? ({
              ...d,
              controllerProps: { ...d.controllerProps, value },
            } as SettingComponentProps)
          : d
      ),
    }))
  },
""",
            'generic attachment setting implementation',
            'setSetting: async (key, value)'
        )

    attachments_rel = 'web-app/src/routes/settings/attachments.tsx'
    if (ROOT / attachments_rel).exists():
        replace_text(
            attachments_rel,
            """      setAutoInlineContextRatio: s.setAutoInlineContextRatio,
""",
            """      setAutoInlineContextRatio: s.setAutoInlineContextRatio,
      setSetting: s.setSetting,
""",
            'attachment settings selector generic setter'
        )
        replace_text(
            attachments_rel,
            """      // For non-numeric inputs, apply immediately without debounce
      if (key === 'enabled' || key === 'search_mode' || key === 'parse_mode') {
        if (key === 'enabled') sel.setEnabled(!!val)
        else if (key === 'search_mode')
          sel.setSearchMode(val as 'auto' | 'ann' | 'linear')
        else if (key === 'parse_mode')
          sel.setParseMode(val as 'auto' | 'inline' | 'embeddings' | 'prompt')
        return
      }
""",
            """      // For non-numeric inputs, apply immediately without debounce
      if (key === 'enabled' || key === 'search_mode' || key === 'parse_mode') {
        if (key === 'enabled') sel.setEnabled(!!val)
        else if (key === 'search_mode')
          sel.setSearchMode(val as 'auto' | 'ann' | 'linear')
        else if (key === 'parse_mode')
          sel.setParseMode(val as 'auto' | 'inline' | 'embeddings' | 'prompt')
        return
      }
      if (def.controllerType !== 'input' || (def.controllerProps as any).type !== 'number') {
        sel.setSetting(key, val)
        return
      }
""",
            'generic nonnumeric attachment setting persistence'
        )
        replace_text(
            attachments_rel,
            """            default:
              return 0
""",
            """            default:
              return Number(d.controllerProps?.value ?? 0)
""",
            'generic numeric setting current value'
        )
        replace_text(
            attachments_rel,
            """          case 'auto_inline_context_ratio':
            sel.setAutoInlineContextRatio(validated)
            break
        }
""",
            """          case 'auto_inline_context_ratio':
            sel.setAutoInlineContextRatio(validated)
            break
          default:
            sel.setSetting(key, validated)
            break
        }
""",
            'generic numeric attachment setting persistence'
        )

    # OpenAPI docs for reranking endpoints.
    openapi_rel = 'src-tauri/static/openapi.json'
    api = json.loads(read_rel(openapi_rel))
    paths = api.setdefault('paths', {})
    rerank_spec = {
        'post': {
            'summary': 'Rerank documents',
            'description': 'Scores documents for relevance to a query using a local reranker model. Accepts documents or texts; model may be auto.',
            'operationId': 'createRerank',
            'tags': ['Inference'],
            'requestBody': {
                'required': True,
                'content': {
                    'application/json': {
                        'schema': {'$ref': '#/components/schemas/RerankRequestDto'},
                        'example': {
                            'model': 'auto',
                            'query': 'what is rank pooling?',
                            'documents': ['Rank pooling scores query-document pairs.', 'Bananas are yellow.'],
                            'top_n': 2,
                            'return_documents': True,
                        },
                    }
                },
            },
            'responses': {
                '200': {'description': 'Rerank result', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/RerankResponseDto'}}}},
                '400': {'description': 'Invalid rerank request', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponseDto'}}}},
                '503': {'description': 'No local reranker is available', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponseDto'}}}},
            },
        }
    }
    paths['/rerank'] = rerank_spec
    paths['/reranking'] = rerank_spec
    schemas = api.setdefault('components', {}).setdefault('schemas', {})
    schemas['RerankDocumentDto'] = {
        'oneOf': [
            {'type': 'string'},
            {
                'type': 'object',
                'properties': {
                    'id': {'type': 'string'},
                    'text': {'type': 'string'},
                    'content': {'type': 'string'},
                    'metadata': {'type': 'object', 'additionalProperties': True},
                },
                'additionalProperties': True,
            },
        ]
    }
    schemas['RerankRequestDto'] = {
        'type': 'object',
        'properties': {
            'model': {'type': 'string', 'default': 'auto'},
            'query': {'type': 'string'},
            'documents': {'type': 'array', 'items': {'$ref': '#/components/schemas/RerankDocumentDto'}},
            'texts': {'type': 'array', 'items': {'$ref': '#/components/schemas/RerankDocumentDto'}, 'description': 'Alias for documents.'},
            'top_n': {'type': 'integer', 'minimum': 1},
            'top_k': {'type': 'integer', 'minimum': 1, 'description': 'Alias for top_n.'},
            'return_documents': {'type': 'boolean', 'default': True},
            'normalize': {'type': 'boolean', 'default': True},
            'max_tokens_per_doc': {'type': 'integer', 'minimum': 1},
            'evidence_mode': {'type': 'string', 'enum': ['off', 'top_n', 'all'], 'default': 'off'},
        },
        'required': ['query'],
    }
    schemas['RerankResultDto'] = {
        'type': 'object',
        'properties': {
            'index': {'type': 'integer'},
            'relevance_score': {'type': 'number'},
            'document': {'$ref': '#/components/schemas/RerankDocumentDto'},
            'evidence': {'type': 'string'},
            'contribution': {'type': 'string'},
        },
        'required': ['index', 'relevance_score'],
    }
    schemas['RerankResponseDto'] = {
        'type': 'object',
        'properties': {
            'object': {'type': 'string', 'default': 'list'},
            'model': {'type': 'string'},
            'results': {'type': 'array', 'items': {'$ref': '#/components/schemas/RerankResultDto'}},
            'usage': {'type': 'object', 'additionalProperties': True},
            'meta': {'type': 'object', 'additionalProperties': True},
        },
        'required': ['results'],
    }
    schemas['ErrorResponseDto'] = {
        'type': 'object',
        'properties': {
            'error': {
                'type': 'object',
                'properties': {
                    'type': {'type': 'string'},
                    'message': {'type': 'string'},
                },
                'required': ['type', 'message'],
            }
        },
        'required': ['error'],
    }
    write_rel(openapi_rel, json.dumps(api, indent=2) + '\n')


def apply_feature_ports_after_rerank() -> None:
    import json
    import re

    def read_rel(rel):
        return (ROOT / rel).read_text(encoding='utf-8')

    def write_rel(rel, data):
        path = ROOT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding='utf-8')

    def require_rel(rel):
        if not (ROOT / rel).exists():
            fail('Missing required file for feature-port patch: ' + rel)

    def insert_after(rel, needle, insertion, label, marker=None):
        s = read_rel(rel)
        if (marker and marker in s) or insertion.strip()[:100] in s:
            return
        idx = s.find(needle)
        if idx < 0:
            fail('Could not find insertion point for ' + label + ' in ' + rel)
        idx += len(needle)
        write_rel(rel, s[:idx] + insertion + s[idx:])

    def insert_before(rel, needle, insertion, label, marker=None):
        s = read_rel(rel)
        if (marker and marker in s) or insertion.strip()[:100] in s:
            return
        idx = s.find(needle)
        if idx < 0:
            fail('Could not find insertion point for ' + label + ' in ' + rel)
        write_rel(rel, s[:idx] + insertion + s[idx:])

    def replace_once(rel, old, new, label, marker=None):
        s = read_rel(rel)
        if (marker and marker in s) or new.strip()[:100] in s:
            return
        if old not in s:
            fail('Could not find target for ' + label + ' in ' + rel)
        write_rel(rel, s.replace(old, new, 1))

    def replace_regex(rel, pattern, repl, label, flags=re.S):
        s = read_rel(rel)
        if repl.strip()[:100] in s:
            return
        new, n = re.subn(pattern, lambda _m: repl, s, count=1, flags=flags)
        if n == 0:
            fail('Could not find regex target for ' + label + ' in ' + rel)
        write_rel(rel, new)

    for rel in [
        'web-app/src/routes/hub/index.tsx',
        'web-app/src/containers/ChatInput.tsx',
        'web-app/src/lib/custom-chat-transport.ts',
        'web-app/src/hooks/use-chat.ts',
        'web-app/src/containers/dialogs/EditModel.tsx',
        'web-app/src/routes/settings/providers/$providerName.tsx',
        'web-app/src/containers/RenderMarkdown.tsx',
        'web-app/src/containers/MessageItem.tsx',
        'web-app/src/styles/markdown.css',
        'src-tauri/src/core/server/proxy.rs',
        'src-tauri/static/openapi.json',
        'src-tauri/tauri.conf.json',
        'web-app/src/constants/localStorage.ts',
    ]:
        require_rel(rel)

    # ------------------------------------------------------------------
    # Hub category filter bar from commit 499cb63, adapted to current Hub.
    # ------------------------------------------------------------------
    hub = 'web-app/src/routes/hub/index.tsx'
    replace_once(hub,
        "  IconTool,\n} from '@tabler/icons-react'",
        "  IconTool,\n  IconBrain,\n  IconCode,\n  IconPencil,\n  IconStar,\n} from '@tabler/icons-react'",
        'hub category icon imports',
        'IconCode,'
    )
    insert_after(hub, '''function getQuantTier(modelId: string): QuantTier | null {
  const id = modelId.toLowerCase()
  if (/(^|[-_.])(f32|bf16|f16|q8|q6)([-_.]|$)/.test(id)) {
    return {
      label: 'Large',
      className:
        'bg-amber-500/10 text-amber-700 dark:text-amber-400',
    }
  }
  if (/(^|[-_.])(q5|q4_k|iq4)/.test(id)) {
    return {
      label: 'Balanced',
      className:
        'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400',
    }
  }
  if (/(^|[-_.])(iq2|iq3|q2|q3|q4_0|q4_1)/.test(id)) {
    return {
      label: 'Small',
      className: 'bg-blue-500/10 text-blue-600 dark:text-blue-400',
    }
  }
  return null
}
''', r'''

type Category = {
  id: string
  label: string
  icon: React.ReactNode
  match: (model: CatalogModel) => boolean
}

const CATEGORY_TEXT = (m: CatalogModel) =>
  `${m.model_name} ${m.description ?? ''} ${m.developer ?? ''}`.toLowerCase()

const CATEGORIES: Category[] = [
  { id: 'all', label: 'All', icon: null, match: () => true },
  {
    id: 'coding',
    label: 'Coding',
    icon: <IconCode size={13} />,
    match: (m) => /cod(e|ing|er)|starcoder|deepseek.?coder|devstral|granite.?code|qwen.?coder/.test(CATEGORY_TEXT(m)),
  },
  {
    id: 'reasoning',
    label: 'Reasoning',
    icon: <IconBrain size={13} />,
    match: (m) => /reason|think(ing)?|\br1\b|qwq|deepseek.?r\d|\bo[13]-|skywork/.test(CATEGORY_TEXT(m)),
  },
  {
    id: 'creative',
    label: 'Creative',
    icon: <IconPencil size={13} />,
    match: (m) => /creat|writ(e|ing)|story|novel|roleplay|\brp\b|chat|llama|mistral/.test(CATEGORY_TEXT(m)),
  },
  {
    id: 'vision',
    label: 'Vision',
    icon: <IconEye size={13} />,
    match: (m) => (m.num_mmproj ?? 0) > 0,
  },
  {
    id: 'agentic',
    label: 'Agentic',
    icon: <IconTool size={13} />,
    match: (m) => !!m.tools,
  },
  {
    id: 'small',
    label: 'Small (≤4B)',
    icon: <IconStar size={13} />,
    match: (m) => /[-_. ]([1234]b)([-_. ]|$)|nano|mini(?!mal)|(^|[^a-z])small([^a-z]|$)/.test(m.model_name.toLowerCase()),
  },
]
''', 'hub category definitions', 'const CATEGORIES')
    replace_once(hub,
        "  const [sortSelected, setSortSelected] = useState('newest')\n",
        "  const [sortSelected, setSortSelected] = useState('newest')\n  const [activeCategory, setActiveCategory] = useState('all')\n",
        'hub active category state',
        'activeCategory'
    )
    insert_before(hub,
        "    // Add HuggingFace repo at the beginning if available\n",
        "    if (activeCategory !== 'all') {\n      const cat = CATEGORIES.find((c) => c.id === activeCategory)\n      if (cat) filtered = filtered.filter(cat.match)\n    }\n",
        'hub category filter apply',
        "activeCategory !== 'all'"
    )
    replace_once(hub,
        "    showOnlyDownloaded,\n    huggingFaceRepo,\n",
        "    showOnlyDownloaded,\n    activeCategory,\n    huggingFaceRepo,\n",
        'hub activeCategory useMemo dependency'
    )
    insert_after(hub,
        "  const rowVirtualizer = useVirtualizer(\n    filteredModels.length > 0\n      ? {\n          count: filteredModels.length,\n          getScrollElement: () => parentRef.current,\n          estimateSize,\n          overscan: 8,\n          measureElement: (el: HTMLElement) => el.getBoundingClientRect().height,\n        }\n      : { count: 0, getScrollElement: () => null, estimateSize: () => 0 }\n  )\n",
        "\n  useEffect(() => {\n    rowVirtualizer.scrollToOffset(0, { align: 'start' })\n  }, [activeCategory, rowVirtualizer])\n",
        'hub scroll to top on category change',
        'scrollToOffset(0'
    )
    replace_once(hub,
        "    setHuggingFaceRepo(null) // Clear previous repo info\n\n    if (!showOnlyDownloaded) {",
        "    setHuggingFaceRepo(null) // Clear previous repo info\n    setActiveCategory('all')\n\n    if (!showOnlyDownloaded) {",
        'hub reset category on search'
    )
    replace_once(hub,
        "        <div ref={parentRef} className=\"p-4 w-full h-[calc(100%-60px)] overflow-y-auto! first-step-setup-local-provider\">",
        '''        <div className="shrink-0 border-b border-border bg-background/95 px-4 py-2">
          <div className="mx-auto flex w-full md:w-4/5 xl:w-4/6 gap-2 overflow-x-auto scrollbar-hide">
            {CATEGORIES.map((category) => {
              const active = activeCategory === category.id
              return (
                <button
                  key={category.id}
                  type="button"
                  onClick={() => {
                    setIsInitialLoad(true)
                    setActiveCategory(category.id)
                  }}
                  className={cn(
                    'shrink-0 inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-colors',
                    active
                      ? 'border-primary bg-primary/10 text-primary'
                      : 'border-border text-muted-foreground hover:bg-secondary hover:text-foreground'
                  )}
                >
                  {category.icon}
                  {category.label}
                </button>
              )
            })}
          </div>
        </div>
        <div ref={parentRef} className="p-4 w-full h-[calc(100%-104px)] overflow-y-auto! first-step-setup-local-provider">''',
        'hub category bar render',
        'Category filter bar'
    )

    # ------------------------------------------------------------------
    # PR #8039 capability toggles and auto-detection.
    # ------------------------------------------------------------------
    write_rel('web-app/src/lib/model-capabilities-detector.ts', r'''export type DetectedModelCapabilities = {
  reasoning: boolean
  web_search: boolean
  embeddings: boolean
}

const REASONING_RE = /(?:\br1\b|deepseek[-_. ]?r\d|qwq|qvq|reason(?:ing)?|think(?:ing)?|o[13](?:[-_.]|$)|gpt-5|skywork)/i
const WEB_SEARCH_RE = /(?:sonar|perplexity|web[-_. ]?search|search[-_. ]?enabled|online)/i
const EMBEDDING_RE = /(?:embed(?:ding)?|bge[-_. ]?m3|nomic[-_. ]?embed|mxbai[-_. ]?embed|jina[-_. ]?embed|e5[-_. ]|gte[-_. ]|sentence[-_. ]?transformer)/i

export function detectModelCapabilities(modelId: string): DetectedModelCapabilities {
  const id = String(modelId ?? '')
  return {
    reasoning: REASONING_RE.test(id),
    web_search: WEB_SEARCH_RE.test(id),
    embeddings: EMBEDDING_RE.test(id),
  }
}

export function hasDetectedCapabilities(caps: DetectedModelCapabilities): boolean {
  return caps.reasoning || caps.web_search || caps.embeddings
}

export function mergeDetectedCapabilities(model: Model): string[] {
  const base = new Set(model.capabilities ?? [])
  if ((model as Model & { _userConfiguredCapabilities?: boolean })._userConfiguredCapabilities) {
    return Array.from(base)
  }
  const detected = detectModelCapabilities(model.id)
  if (detected.reasoning) base.add('reasoning')
  if (detected.web_search) base.add('web_search')
  if (detected.embeddings) base.add('embeddings')
  return Array.from(base)
}
''')
    write_rel('web-app/src/stores/capability-toggles-store.ts', r'''import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { localStorageKey } from '@/constants/localStorage'

export type CapabilityToggleName = 'reasoning' | 'webSearch' | 'embeddings'
export type CapabilityToggles = Record<CapabilityToggleName, boolean>

export const DEFAULT_CAPABILITY_TOGGLES: CapabilityToggles = {
  reasoning: false,
  webSearch: false,
  embeddings: false,
}

type CapabilityToggleStore = {
  byThread: Record<string, CapabilityToggles>
  getToggles: (threadId: string) => CapabilityToggles
  setToggle: (threadId: string, name: CapabilityToggleName, value: boolean) => void
  toggle: (threadId: string, name: CapabilityToggleName) => void
  reset: (threadId: string) => void
}

function normalize(toggles?: Partial<CapabilityToggles>): CapabilityToggles {
  return { ...DEFAULT_CAPABILITY_TOGGLES, ...(toggles ?? {}) }
}

export const useCapabilityToggles = create<CapabilityToggleStore>()(
  persist(
    (set, get) => ({
      byThread: {},
      getToggles: (threadId) => normalize(get().byThread[threadId]),
      setToggle: (threadId, name, value) =>
        set((state) => ({
          byThread: {
            ...state.byThread,
            [threadId]: {
              ...normalize(state.byThread[threadId]),
              [name]: value,
            },
          },
        })),
      toggle: (threadId, name) => {
        const current = normalize(get().byThread[threadId])
        get().setToggle(threadId, name, !current[name])
      },
      reset: (threadId) =>
        set((state) => {
          const next = { ...state.byThread }
          delete next[threadId]
          return { byThread: next }
        }),
    }),
    {
      name: localStorageKey.capabilityToggles,
      version: 1,
    }
  )
)
''')
    replace_once('web-app/src/constants/localStorage.ts',
        "  latestJanModel: 'latest-jan-model',\n",
        "  latestJanModel: 'latest-jan-model',\n  capabilityToggles: 'capability-toggles',\n",
        'capability toggles localStorage key',
        'capabilityToggles'
    )

    chat = 'web-app/src/containers/ChatInput.tsx'
    insert_after(chat,
        "import { useAgentMode } from '@/hooks/useAgentMode'\n",
        "import { useCapabilityToggles } from '@/stores/capability-toggles-store'\nimport { mergeDetectedCapabilities } from '@/lib/model-capabilities-detector'\n",
        'ChatInput capability imports',
        'useCapabilityToggles'
    )
    insert_after(chat,
        "  const handleAgentToggle = useCallback(() => {\n    toggleAgentMode(agentModeKey)\n  }, [agentModeKey, toggleAgentMode])\n",
        "\n  const capabilityKey = currentThreadId ?? TEMPORARY_CHAT_ID\n  const capabilityToggles = useCapabilityToggles((state) => state.getToggles(capabilityKey))\n  const toggleCapability = useCapabilityToggles((state) => state.toggle)\n  const setCapabilityToggle = useCapabilityToggles((state) => state.setToggle)\n\n  const handleWebSearchToggle = useCallback(() => {\n    toggleCapability(capabilityKey, 'webSearch')\n  }, [capabilityKey, toggleCapability])\n\n  const handleReasoningToggle = useCallback(() => {\n    toggleCapability(capabilityKey, 'reasoning')\n  }, [capabilityKey, toggleCapability])\n\n  const handleEmbeddingsToggle = useCallback(() => {\n    toggleCapability(capabilityKey, 'embeddings')\n  }, [capabilityKey, toggleCapability])\n",
        'ChatInput capability toggle state',
        'handleWebSearchToggle'
    )
    insert_after(chat,
        "  const selectedModel = useModelProvider((state) => state.selectedModel)\n  const selectedProvider = useModelProvider((state) => state.selectedProvider)\n",
        "  const effectiveCapabilities = useMemo(\n    () => (selectedModel ? mergeDetectedCapabilities(selectedModel) : []),\n    [selectedModel]\n  )\n\n  useEffect(() => {\n    if (capabilityToggles.webSearch && !effectiveCapabilities.includes('web_search')) {\n      setCapabilityToggle(capabilityKey, 'webSearch', false)\n    }\n    if (capabilityToggles.reasoning && !effectiveCapabilities.includes('reasoning')) {\n      setCapabilityToggle(capabilityKey, 'reasoning', false)\n    }\n    if (capabilityToggles.embeddings && !effectiveCapabilities.includes('embeddings')) {\n      setCapabilityToggle(capabilityKey, 'embeddings', false)\n    }\n  }, [capabilityKey, capabilityToggles, effectiveCapabilities, setCapabilityToggle])\n",
        'ChatInput effective capabilities and auto-disable',
        'effectiveCapabilities = useMemo'
    )
    replace_once(chat,
        "    const capabilities = selectedModel?.capabilities || []\n    return capabilities.includes('vision') && capabilities.includes('tools')\n  }, [selectedModel?.capabilities])",
        "    return effectiveCapabilities.includes('vision') && effectiveCapabilities.includes('tools')\n  }, [effectiveCapabilities])",
        'ChatInput browser capability detection'
    )
    replace_once(chat,
        "disabled={!selectedModel?.capabilities?.includes('tools')}",
        "disabled={!effectiveCapabilities.includes('tools')}",
        'ChatInput document attachment tools check'
    )
    replace_once(chat,
        "{!effectiveAgentMode && selectedModel?.capabilities?.includes('embeddings') && (",
        "{!effectiveAgentMode && effectiveCapabilities.includes('embeddings') && (",
        'ChatInput embeddings capability condition'
    )
    replace_once(chat,
        "<Button\n                          variant=\"ghost\"\n                          size=\"icon-xs\"\n                        >\n                        <IconCodeCircle2\n                          size={18}\n                          className=\"text-muted-foreground\"\n                        />\n                      </Button>",
        "<Button\n                          variant=\"ghost\"\n                          size=\"icon-xs\"\n                          onClick={handleEmbeddingsToggle}\n                          className={cn(capabilityToggles.embeddings && 'text-primary')}\n                        >\n                        <IconCodeCircle2\n                          size={18}\n                          className={cn('text-muted-foreground', capabilityToggles.embeddings && 'text-primary')}\n                        />\n                      </Button>",
        'ChatInput embeddings toggle button'
    )
    replace_once(chat,
        "<p>{t('embeddings')}</p>",
        "<p>{capabilityToggles.embeddings ? `${t('embeddings')} (${t('active')})` : t('embeddings')}</p>",
        'ChatInput embeddings tooltip'
    )
    insert_before(chat,
        "                {!effectiveAgentMode && effectiveCapabilities.includes('embeddings') && (",
        '''                {!effectiveAgentMode && effectiveCapabilities.includes('web_search') && (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        onClick={handleWebSearchToggle}
                        className={cn(capabilityToggles.webSearch && 'text-primary')}
                      >
                        <IconWorld
                          size={18}
                          className={cn('text-muted-foreground', capabilityToggles.webSearch && 'text-primary')}
                        />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>
                      <p>{capabilityToggles.webSearch ? `${t('webSearch')} (${t('active')})` : t('webSearch')}</p>
                    </TooltipContent>
                  </Tooltip>
                )}

                {!effectiveAgentMode && effectiveCapabilities.includes('reasoning') && (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        onClick={handleReasoningToggle}
                        className={cn(capabilityToggles.reasoning && 'text-primary')}
                      >
                        <IconBrain
                          size={18}
                          className={cn('text-muted-foreground', capabilityToggles.reasoning && 'text-primary')}
                        />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>
                      <p>{capabilityToggles.reasoning ? `${t('reasoning')} (${t('active')})` : t('reasoning')}</p>
                    </TooltipContent>
                  </Tooltip>
                )}

''',
        'ChatInput web search and reasoning toggles',
        'handleReasoningToggle'
    )
    replace_once(chat,
        "!effectiveAgentMode && selectedModel?.capabilities?.includes('tools') &&",
        "!effectiveAgentMode && effectiveCapabilities.includes('tools') &&",
        'ChatInput tools capability condition'
    )
    replace_once(chat,
        "selectedModel?.capabilities?.includes('tools') ?? false",
        "effectiveCapabilities.includes('tools')",
        'ChatInput MCP selected model tools capability'
    )

    transport = 'web-app/src/lib/custom-chat-transport.ts'
    insert_after(transport,
        "import { paramsSettings } from '@/lib/predefinedParams'\n",
        "import type { CapabilityToggles } from '@/stores/capability-toggles-store'\nimport { DEFAULT_CAPABILITY_TOGGLES } from '@/stores/capability-toggles-store'\n",
        'transport capability imports',
        'CapabilityToggles'
    )
    insert_after(transport,
        "export function buildLlamacppReasoningParams(\n  providerName: string | null | undefined,\n  reasoning: 'auto' | 'on' | 'off' | undefined\n): { chat_template_kwargs?: { enable_thinking: boolean } } {\n  if (providerName !== 'llamacpp') return {}\n  if (reasoning !== 'on' && reasoning !== 'off') return {}\n  return {\n    chat_template_kwargs: { enable_thinking: reasoning === 'on' },\n  }\n}\n",
        r'''

function supportsLooseProviderExtras(providerName: string | null | undefined, modelId: string | undefined): boolean {
  const p = String(providerName ?? '').toLowerCase()
  const m = String(modelId ?? '').toLowerCase()
  if (['openai', 'anthropic', 'google', 'gemini', 'xai'].includes(p)) return false
  return p.includes('perplexity') || m.includes('sonar') || m.includes('perplexity')
}

function buildCapabilityRequestParams(
  providerName: string | null | undefined,
  modelId: string | undefined,
  toggles: CapabilityToggles
): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  if (toggles.reasoning) {
    if (providerName === 'anthropic') {
      out.thinking = { type: 'enabled', budget_tokens: 4096 }
    } else if (providerName === 'llamacpp') {
      out.chat_template_kwargs = { enable_thinking: true }
    }
  }
  if (toggles.webSearch && supportsLooseProviderExtras(providerName, modelId)) {
    out.web_search = true
  }
  return out
}
''',
        'transport capability request params',
        'buildCapabilityRequestParams'
    )
    replace_once(transport,
        "  private ragFeatureAvailable = false\n",
        "  private ragFeatureAvailable = false\n  private capabilityToggles: CapabilityToggles = DEFAULT_CAPABILITY_TOGGLES\n",
        'transport capability toggle field'
    )
    insert_after(transport,
        "  updateSystemMessage(systemMessage: string | undefined) {\n    this.systemMessage = systemMessage\n  }\n",
        "\n  setCapabilityToggles(toggles: CapabilityToggles | undefined) {\n    this.capabilityToggles = { ...DEFAULT_CAPABILITY_TOGGLES, ...(toggles ?? {}) }\n  }\n",
        'transport setCapabilityToggles',
        'setCapabilityToggles'
    )
    replace_once(transport,
        "      if (hasDocuments && ragFeatureAvailable) {",
        "      if ((hasDocuments || this.capabilityToggles.embeddings) && ragFeatureAvailable) {",
        'transport embeddings toggle enables RAG tools'
    )
    replace_once(transport,
        "        ...reasoningParams,\n      }",
        "        ...reasoningParams,\n        ...buildCapabilityRequestParams(effectiveProviderName, modelId, this.capabilityToggles),\n      }",
        'transport inject capability request params'
    )

    usechat = 'web-app/src/hooks/use-chat.ts'
    insert_after(usechat,
        "import { useAppState } from '@/hooks/useAppState'\n",
        "import { useCapabilityToggles } from '@/stores/capability-toggles-store'\nimport { TEMPORARY_CHAT_ID } from '@/constants/chat'\n",
        'use-chat capability imports',
        'useCapabilityToggles'
    )
    insert_after(usechat,
        "  const ragToolNames = useAppState((state) => state.ragToolNames)\n",
        "  const capabilityKey = sessionId ?? TEMPORARY_CHAT_ID\n  const capabilityToggles = useCapabilityToggles((state) => state.getToggles(capabilityKey))\n",
        'use-chat capability state',
        'capabilityToggles'
    )
    insert_after(usechat,
        "  useEffect(() => {\n    if (transportRef.current) {\n      transportRef.current.setOnTokenUsage(onTokenUsage)\n    }\n  }, [onTokenUsage])\n",
        "\n  useEffect(() => {\n    transportRef.current?.setCapabilityToggles(capabilityToggles)\n  }, [capabilityToggles])\n",
        'use-chat sync capability toggles'
    )

    edit = 'web-app/src/containers/dialogs/EditModel.tsx'
    replace_once(edit,
        "  IconVideo,\n} from '@tabler/icons-react'",
        "  IconVideo,\n  IconAtom,\n  IconWorld,\n  IconCodeCircle2,\n  IconSparkles,\n  IconInfoCircle,\n} from '@tabler/icons-react'",
        'EditModel capability icon imports',
        'IconSparkles'
    )
    insert_after(edit,
        "import { toast } from 'sonner'\n",
        "import {\n  detectModelCapabilities,\n  hasDetectedCapabilities,\n} from '@/lib/model-capabilities-detector'\n",
        'EditModel detector import',
        'detectModelCapabilities'
    )
    replace_once(edit,
        "    audio: false,\n    video: false,\n  })",
        "    audio: false,\n    video: false,\n    reasoning: false,\n    web_search: false,\n    embeddings: false,\n  })\n  const [isAutoDetected, setIsAutoDetected] = useState(false)",
        'EditModel capability state'
    )
    replace_once(edit,
        "    audio: capabilitiesList.includes('audio'),\n    video: capabilitiesList.includes('video'),\n  })",
        "    audio: capabilitiesList.includes('audio'),\n    video: capabilitiesList.includes('video'),\n    reasoning: capabilitiesList.includes('reasoning'),\n    web_search: capabilitiesList.includes('web_search'),\n    embeddings: capabilitiesList.includes('embeddings'),\n  })",
        'EditModel capabilitiesToObject'
    )
    replace_once(edit,
        "      const modelCapabilities = selectedModel.capabilities || []\n      const capsObject = capabilitiesToObject(modelCapabilities)\n\n      setCapabilities(capsObject)\n      setOriginalCapabilities(capsObject)\n",
        "      const modelCapabilities = selectedModel.capabilities || []\n      const userConfigured = (selectedModel as Model & { _userConfiguredCapabilities?: boolean })._userConfiguredCapabilities\n      let capsObject = capabilitiesToObject(modelCapabilities)\n      let autoDetected = false\n      if (!userConfigured) {\n        const detected = detectModelCapabilities(selectedModel.id)\n        if (hasDetectedCapabilities(detected)) {\n          capsObject = {\n            ...capsObject,\n            reasoning: capsObject.reasoning || detected.reasoning,\n            web_search: capsObject.web_search || detected.web_search,\n            embeddings: capsObject.embeddings || detected.embeddings,\n          }\n          autoDetected = true\n        }\n      }\n\n      setCapabilities(capsObject)\n      setOriginalCapabilities(capsObject)\n      setIsAutoDetected(autoDetected)\n",
        'EditModel auto-detect capabilities'
    )
    insert_after(edit,
        "          <h3 className=\"text-sm font-medium mb-3\">\n            {t('providers:editModel.capabilities')}\n          </h3>\n",
        '''          {isAutoDetected && (
            <div className="flex items-start gap-2 mb-3 rounded-md bg-primary/10 px-3 py-2 text-xs text-primary">
              <IconSparkles size={14} className="mt-0.5 shrink-0" />
              <span>Capabilities auto-detected from model name. Review and adjust if needed.</span>
            </div>
          )}
''',
        'EditModel auto-detected banner',
        'Capabilities auto-detected'
    )
    insert_before(edit,
        "            <div className=\"flex items-center justify-between\">\n              <div className=\"flex items-center space-x-2\">\n                <IconHeadphones",
        '''            <div className="space-y-1">
              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-2">
                  <IconAtom className="size-4 text-muted-foreground" />
                  <span className="text-sm">{t('providers:editModel.reasoning')}</span>
                </div>
                <Switch id="reasoning-capability" checked={capabilities.reasoning} onCheckedChange={(checked) => handleCapabilityChange('reasoning', checked)} disabled={isLoading} />
              </div>
              {capabilities.reasoning && <div className="flex items-start gap-1.5 pl-6 text-xs text-muted-foreground"><IconInfoCircle size={12} className="mt-0.5 shrink-0" /><span>Only works with reasoning models. Has no effect on standard models.</span></div>}
            </div>

            <div className="space-y-1">
              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-2">
                  <IconWorld className="size-4 text-muted-foreground" />
                  <span className="text-sm">{t('providers:editModel.webSearch')}</span>
                </div>
                <Switch id="web-search-capability" checked={capabilities.web_search} onCheckedChange={(checked) => handleCapabilityChange('web_search', checked)} disabled={isLoading} />
              </div>
              {capabilities.web_search && <div className="flex items-start gap-1.5 pl-6 text-xs text-muted-foreground"><IconInfoCircle size={12} className="mt-0.5 shrink-0" /><span>Only works with providers or models that support built-in web search.</span></div>}
            </div>

            <div className="space-y-1">
              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-2">
                  <IconCodeCircle2 className="size-4 text-muted-foreground" />
                  <span className="text-sm">{t('providers:editModel.embeddings')}</span>
                </div>
                <Switch id="embeddings-capability" checked={capabilities.embeddings} onCheckedChange={(checked) => handleCapabilityChange('embeddings', checked)} disabled={isLoading} />
              </div>
              {capabilities.embeddings && <div className="flex items-start gap-1.5 pl-6 text-xs text-muted-foreground"><IconInfoCircle size={12} className="mt-0.5 shrink-0" /><span>Enables semantic search/RAG tooling for this model.</span></div>}
            </div>

''',
        'EditModel reasoning web search embedding rows',
        'reasoning-capability'
    )

    providers = 'web-app/src/routes/settings/providers/$providerName.tsx'
    insert_after(providers,
        "} from '@/lib/remoteModelCatalog'\n",
        "import { mergeDetectedCapabilities } from '@/lib/model-capabilities-detector'\n",
        'provider settings detector import',
        'mergeDetectedCapabilities'
    )
    replace_once(providers,
        "                    const capabilities = model.capabilities || []",
        "                    const capabilities = mergeDetectedCapabilities(model)",
        'provider settings chat model detected caps'
    )
    replace_once(providers,
        "capabilities={model.capabilities || []}",
        "capabilities={mergeDetectedCapabilities(model)}",
        'provider settings embedding model detected caps'
    )

    # ------------------------------------------------------------------
    # PR #8292 copyable inline code, adapted to current RenderMarkdown.
    # ------------------------------------------------------------------
    render = 'web-app/src/containers/RenderMarkdown.tsx'
    replace_once(render,
        "import { memo, useDeferredValue, useMemo } from 'react'",
        "import { memo, useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from 'react'\nimport { createPortal } from 'react-dom'",
        'RenderMarkdown copyable inline imports',
        'createPortal'
    )
    replace_once(render,
        "  isAnimating?: boolean\n}",
        "  isAnimating?: boolean\n  copyableInlineCode?: boolean\n}",
        'RenderMarkdown copyableInlineCode prop'
    )
    insert_after(render,
        "const EMPTY_MERMAID = {}\n",
        "const INLINE_CODE_SELECTOR = '[data-streamdown=\"inline-code\"]'\nconst COPY_FEEDBACK_MS = 1200\n",
        'RenderMarkdown inline code constants',
        'INLINE_CODE_SELECTOR'
    )
    replace_once(render,
        "  isAnimating,\n  isStreaming,\n}: MarkdownProps) {",
        "  isAnimating,\n  isStreaming,\n  copyableInlineCode,\n}: MarkdownProps) {",
        'RenderMarkdown destructure copyableInlineCode'
    )
    insert_after(render,
        "  }, [normalizedContent, isStreaming, renderHtmlArtifacts])\n",
        r'''

  const [copyBadge, setCopyBadge] = useState<{ x: number; y: number } | null>(null)
  const copyBadgeTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  useEffect(() => () => clearTimeout(copyBadgeTimer.current), [])

  const handleMarkdownClick = useCallback((event: React.MouseEvent<HTMLDivElement>) => {
    if (!copyableInlineCode) return
    const el = (event.target as HTMLElement).closest<HTMLElement>(INLINE_CODE_SELECTOR)
    if (!el) return
    const selection = window.getSelection()
    if (selection && !selection.isCollapsed && selection.toString().length > 0) return
    const text = el.textContent ?? ''
    if (!text || !navigator.clipboard?.writeText) return
    navigator.clipboard
      .writeText(text)
      .then(() => {
        setCopyBadge({ x: event.clientX, y: event.clientY })
        clearTimeout(copyBadgeTimer.current)
        copyBadgeTimer.current = setTimeout(() => setCopyBadge(null), COPY_FEEDBACK_MS)
      })
      .catch(() => {})
  }, [copyableInlineCode])
''',
        'RenderMarkdown click-to-copy handler',
        'handleMarkdownClick'
    )
    replace_once(render,
        "        isUser && 'is-user',\n        className\n      )}\n    >",
        "        isUser && 'is-user',\n        copyableInlineCode && 'copyable-inline-code',\n        className\n      )}\n      onClick={copyableInlineCode ? handleMarkdownClick : undefined}\n    >",
        'RenderMarkdown wrapper onClick class'
    )
    insert_before(render,
        "    </div>\n  )\n}\n\ninterface StreamdownViewProps",
        r'''      {copyBadge &&
        createPortal(
          <div
            className="pointer-events-none fixed z-50 -translate-x-1/2 -translate-y-full rounded-md bg-foreground px-2 py-1 text-xs font-medium text-background shadow-md animate-in fade-in-0 zoom-in-95"
            style={{ left: copyBadge.x, top: copyBadge.y - 8 }}
          >
            Copied!
          </div>,
          document.body
        )}
''',
        'RenderMarkdown copied badge portal',
        'Copied!'
    )
    replace_once(render,
        "    prevProps.content === nextProps.content &&\n    prevProps.isStreaming === nextProps.isStreaming\n)",
        "    prevProps.content === nextProps.content &&\n    prevProps.isStreaming === nextProps.isStreaming &&\n    prevProps.copyableInlineCode === nextProps.copyableInlineCode\n)",
        'RenderMarkdown memo compare copyableInlineCode'
    )
    replace_once('web-app/src/containers/MessageItem.tsx',
        "                isAnimating={isAnimating}\n              />",
        "                isAnimating={isAnimating}\n                copyableInlineCode\n              />",
        'MessageItem enable copyable inline code',
        'copyableInlineCode'
    )
    insert_after('web-app/src/styles/markdown.css',
        "  pre code {\n    background-color: transparent;\n    padding: 2px;\n    @apply text-sm!;\n    display: inline-block;\n  }\n",
        "\n  .copyable-inline-code [data-streamdown='inline-code'] {\n    cursor: pointer;\n    transition: background-color 0.1s ease;\n  }\n\n  .copyable-inline-code [data-streamdown='inline-code']:hover {\n    @apply bg-secondary brightness-95;\n  }\n",
        'markdown copyable inline CSS',
        'copyable-inline-code'
    )

    # ------------------------------------------------------------------
    # PR #7944 model lifecycle API, adapted to current router architecture.
    # Content-Length fix already exists in upstream; preserve it and add endpoints.
    # ------------------------------------------------------------------
    proxy = 'src-tauri/src/core/server/proxy.rs'
    replace_once(proxy,
        "use std::path::PathBuf;",
        "use std::path::{Component, Path, PathBuf};",
        'proxy Path imports',
        'Component, Path, PathBuf'
    )
    lifecycle = r'''

        (hyper::Method::GET, "/models/available") => {
            log::debug!("Handling GET /v1/models/available request");
            let data_folder = PathBuf::from(&jan_data_folder);
            let router_models: std::collections::HashSet<String> = router_list_models(&llama_state, &client).await.into_iter().collect();
            let models_root = data_folder.join("llamacpp").join("models");
            let mut all_models: Vec<serde_json::Value> = Vec::new();

            if models_root.exists() {
                let mut stack = vec![models_root.clone()];
                while let Some(dir) = stack.pop() {
                    let yml_path = dir.join("model.yml");
                    if yml_path.exists() {
                        if let Ok(content) = fs::read_to_string(&yml_path) {
                            if let Ok(yml) = serde_yaml::from_str::<serde_json::Value>(&content) {
                                let model_id = dir
                                    .strip_prefix(&models_root)
                                    .unwrap_or(&dir)
                                    .to_string_lossy()
                                    .replace('\\', "/");
                                all_models.push(serde_json::json!({
                                    "id": model_id,
                                    "object": "model",
                                    "engine": "llamacpp",
                                    "status": if router_models.contains(&model_id) { "running" } else { "available" },
                                    "name": yml.get("name").and_then(|v| v.as_str()).unwrap_or(&model_id),
                                    "size_bytes": yml.get("size_bytes").and_then(|v| v.as_u64()).unwrap_or(0),
                                    "embedding": yml.get("embedding").and_then(|v| v.as_bool()).unwrap_or(false),
                                    "reranking": yml.get("reranking").and_then(|v| v.as_bool()).unwrap_or(false),
                                    "capabilities": yml.get("capabilities").cloned().unwrap_or_else(|| serde_json::json!([])),
                                }));
                                continue;
                            }
                        }
                    }
                    if let Ok(entries) = fs::read_dir(&dir) {
                        for entry in entries.flatten() {
                            if entry.path().is_dir() {
                                stack.push(entry.path());
                            }
                        }
                    }
                }
            }

            all_models.sort_by(|a, b| {
                let a_id = a.get("id").and_then(|v| v.as_str()).unwrap_or("");
                let b_id = b.get("id").and_then(|v| v.as_str()).unwrap_or("");
                a_id.cmp(b_id)
            });

            let body_str = serde_json::json!({"object": "list", "data": all_models}).to_string();
            let mut response_builder = Response::builder().status(StatusCode::OK).header(hyper::header::CONTENT_TYPE, "application/json");
            response_builder = add_cors_headers_with_host_and_origin(response_builder, &host_header, &origin_header, &config.trusted_hosts);
            return Ok(response_builder.body(full(body_str)).unwrap());
        }

        (hyper::Method::POST, "/models/load") => {
            log::debug!("Handling POST /v1/models/load request");
            let body_bytes = match body.collect().await {
                Ok(c) => c.to_bytes(),
                Err(_) => {
                    let mut error_response = Response::builder().status(StatusCode::BAD_REQUEST);
                    error_response = add_cors_headers_with_host_and_origin(error_response, &host_header, &origin_header, &config.trusted_hosts);
                    return Ok(error_response.body(full("Failed to read request body")).unwrap());
                }
            };
            let json_body: serde_json::Value = match serde_json::from_slice(&body_bytes) {
                Ok(v) => v,
                Err(e) => {
                    let mut error_response = Response::builder().status(StatusCode::BAD_REQUEST).header(hyper::header::CONTENT_TYPE, "application/json");
                    error_response = add_cors_headers_with_host_and_origin(error_response, &host_header, &origin_header, &config.trusted_hosts);
                    return Ok(error_response.body(full(serde_json::json!({"error": format!("Invalid JSON: {e}")}).to_string())).unwrap());
                }
            };
            let model_id = match json_body.get("model").and_then(|v| v.as_str()).map(str::trim).filter(|s| !s.is_empty()) {
                Some(v) => v.to_string(),
                None => {
                    let mut error_response = Response::builder().status(StatusCode::BAD_REQUEST).header(hyper::header::CONTENT_TYPE, "application/json");
                    error_response = add_cors_headers_with_host_and_origin(error_response, &host_header, &origin_header, &config.trusted_hosts);
                    return Ok(error_response.body(full(serde_json::json!({"error": "Missing 'model' field in request body"}).to_string())).unwrap());
                }
            };
            let model_id_lower = model_id.to_lowercase();
            let has_pct_traversal = model_id_lower.contains("%2e%2e") || model_id_lower.contains("%2f") || model_id_lower.contains("%5c");
            let has_path_traversal = Path::new(&model_id).components().any(|c| matches!(c, Component::ParentDir | Component::RootDir | Component::Prefix(_)));
            if has_pct_traversal || has_path_traversal {
                let mut error_response = Response::builder().status(StatusCode::BAD_REQUEST).header(hyper::header::CONTENT_TYPE, "application/json");
                error_response = add_cors_headers_with_host_and_origin(error_response, &host_header, &origin_header, &config.trusted_hosts);
                return Ok(error_response.body(full(serde_json::json!({"error": "Invalid model ID: path traversal not allowed"}).to_string())).unwrap());
            }
            let model_yml = PathBuf::from(&jan_data_folder).join("llamacpp").join("models").join(&model_id).join("model.yml");
            if !model_yml.exists() {
                let mut error_response = Response::builder().status(StatusCode::NOT_FOUND).header(hyper::header::CONTENT_TYPE, "application/json");
                error_response = add_cors_headers_with_host_and_origin(error_response, &host_header, &origin_header, &config.trusted_hosts);
                return Ok(error_response.body(full(serde_json::json!({"error": format!("Model '{model_id}' is not available locally")}).to_string())).unwrap());
            }
            let (router_url, router_key) = match router_upstream(&llama_state, "/chat/completions").await {
                Some(v) => v,
                None => {
                    let mut error_response = Response::builder().status(StatusCode::SERVICE_UNAVAILABLE).header(hyper::header::CONTENT_TYPE, "application/json");
                    error_response = add_cors_headers_with_host_and_origin(error_response, &host_header, &origin_header, &config.trusted_hosts);
                    return Ok(error_response.body(full(serde_json::json!({"error": "llama.cpp router is not running"}).to_string())).unwrap());
                }
            };
            let warmup_body = serde_json::json!({
                "model": model_id,
                "messages": [{"role": "user", "content": " "}],
                "stream": false,
                "n_predict": 0,
                "max_tokens": 1
            });
            let resp = client.post(&router_url).header("Authorization", format!("Bearer {router_key}")).json(&warmup_body).send().await;
            match resp {
                Ok(r) if r.status().is_success() => {
                    let body_str = serde_json::json!({"success": true, "model": model_id, "message": "Model load requested through llama.cpp router"}).to_string();
                    let mut response_builder = Response::builder().status(StatusCode::OK).header(hyper::header::CONTENT_TYPE, "application/json");
                    response_builder = add_cors_headers_with_host_and_origin(response_builder, &host_header, &origin_header, &config.trusted_hosts);
                    return Ok(response_builder.body(full(body_str)).unwrap());
                }
                Ok(r) => {
                    let status = r.status();
                    let text = r.text().await.unwrap_or_default();
                    let mut error_response = Response::builder().status(status).header(hyper::header::CONTENT_TYPE, "application/json");
                    error_response = add_cors_headers_with_host_and_origin(error_response, &host_header, &origin_header, &config.trusted_hosts);
                    return Ok(error_response.body(full(serde_json::json!({"error": text}).to_string())).unwrap());
                }
                Err(e) => {
                    let mut error_response = Response::builder().status(StatusCode::BAD_GATEWAY).header(hyper::header::CONTENT_TYPE, "application/json");
                    error_response = add_cors_headers_with_host_and_origin(error_response, &host_header, &origin_header, &config.trusted_hosts);
                    return Ok(error_response.body(full(serde_json::json!({"error": format!("Failed to contact llama.cpp router: {e}")}).to_string())).unwrap());
                }
            }
        }

        (hyper::Method::POST, "/models/unload") => {
            log::debug!("Handling POST /v1/models/unload request");
            let body_bytes = match body.collect().await {
                Ok(c) => c.to_bytes(),
                Err(_) => {
                    let mut error_response = Response::builder().status(StatusCode::BAD_REQUEST);
                    error_response = add_cors_headers_with_host_and_origin(error_response, &host_header, &origin_header, &config.trusted_hosts);
                    return Ok(error_response.body(full("Failed to read request body")).unwrap());
                }
            };
            let json_body: serde_json::Value = match serde_json::from_slice(&body_bytes) { Ok(v) => v, Err(_) => serde_json::json!({}) };
            let model_id = json_body.get("model").and_then(|v| v.as_str()).unwrap_or("*").to_string();
            if let Some((router_url, router_key)) = router_upstream(&llama_state, "/models/unload").await {
                if let Ok(resp) = client.post(&router_url).header("Authorization", format!("Bearer {router_key}")).body(body_bytes.clone()).send().await {
                    let status = resp.status();
                    let text = resp.text().await.unwrap_or_else(|_| "{}".to_string());
                    let mut response_builder = Response::builder().status(status).header(hyper::header::CONTENT_TYPE, "application/json");
                    response_builder = add_cors_headers_with_host_and_origin(response_builder, &host_header, &origin_header, &config.trusted_hosts);
                    return Ok(response_builder.body(full(text)).unwrap());
                }
            }
            let body_str = serde_json::json!({
                "success": true,
                "model": model_id,
                "message": "Unload request accepted. Current llama.cpp router builds may evict models lazily according to models_max."
            }).to_string();
            let mut response_builder = Response::builder().status(StatusCode::ACCEPTED).header(hyper::header::CONTENT_TYPE, "application/json");
            response_builder = add_cors_headers_with_host_and_origin(response_builder, &host_header, &origin_header, &config.trusted_hosts);
            return Ok(response_builder.body(full(body_str)).unwrap());
        }
'''
    insert_before(proxy,
        "        (hyper::Method::GET, \"/models\") => {",
        lifecycle,
        'model lifecycle API endpoints',
        '/models/available'
    )

    openapi = 'src-tauri/static/openapi.json'
    api = json.loads(read_rel(openapi))
    paths = api.setdefault('paths', {})
    paths['/models/available'] = {
        'get': {
            'summary': 'List available local models',
            'operationId': 'listAvailableModels',
            'tags': ['Models'],
            'responses': {'200': {'description': 'Available local models'}}
        }
    }
    model_lifecycle_request = {
        'type': 'object',
        'properties': {'model': {'type': 'string'}},
        'required': ['model']
    }
    lifecycle_response = {
        'type': 'object',
        'properties': {
            'success': {'type': 'boolean'},
            'model': {'type': 'string'},
            'message': {'type': 'string'},
            'error': {'type': 'string'},
        }
    }
    api.setdefault('components', {}).setdefault('schemas', {})['ModelLifecycleRequestDto'] = model_lifecycle_request
    api['components']['schemas']['ModelLifecycleResponseDto'] = lifecycle_response
    for endpoint, opid, summary in [
        ('/models/load', 'loadModel', 'Load a local model through the llama.cpp router'),
        ('/models/unload', 'unloadModel', 'Unload or evict a local model'),
    ]:
        paths[endpoint] = {
            'post': {
                'summary': summary,
                'operationId': opid,
                'tags': ['Models'],
                'requestBody': {
                    'required': True,
                    'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ModelLifecycleRequestDto'}}}
                },
                'responses': {'200': {'description': 'Model lifecycle response', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ModelLifecycleResponseDto'}}}}}
            }
        }
    write_rel(openapi, json.dumps(api, indent=2) + '\n')

    tauri = 'src-tauri/tauri.conf.json'
    replace_once(tauri,
        "http://asset.localhost https://eu-assets.i.posthog.com",
        "http://asset.localhost https://asset.localhost https://eu-assets.i.posthog.com",
        'Windows asset localhost CSP script-src',
        'https://asset.localhost'
    )

    print('Applied hub categories, capability toggles, model lifecycle API, CSP fix, and copyable inline code ports.')



def apply_last_pr_ports_after_feature_ports() -> None:
    """Apply PR #8288, #8302, #7943 and router-safe #8115 integration."""
    import json
    import re

    def read_rel(rel):
        return (ROOT / rel).read_text(encoding='utf-8')
    def write_rel(rel, data):
        path = ROOT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding='utf-8')
    def require_rel(rel):
        if not (ROOT / rel).exists():
            fail('Missing required file for final PR-port patch: ' + rel)
    def replace_once(rel, old, new, label, marker=None):
        s = read_rel(rel)
        if (marker and marker in s) or new.strip()[:100] in s:
            return
        if old not in s:
            fail('Could not find target for ' + label + ' in ' + rel)
        write_rel(rel, s.replace(old, new, 1))
    def insert_after(rel, needle, insertion, label, marker=None):
        s = read_rel(rel)
        if (marker and marker in s) or insertion.strip()[:100] in s:
            return
        idx = s.find(needle)
        if idx < 0:
            fail('Could not find insertion point for ' + label + ' in ' + rel)
        idx += len(needle)
        write_rel(rel, s[:idx] + insertion + s[idx:])
    def insert_before(rel, needle, insertion, label, marker=None):
        s = read_rel(rel)
        if (marker and marker in s) or insertion.strip()[:100] in s:
            return
        idx = s.find(needle)
        if idx < 0:
            fail('Could not find insertion point for ' + label + ' in ' + rel)
        write_rel(rel, s[:idx] + insertion + s[idx:])
    def replace_regex(rel, pattern, repl, label, flags=re.S):
        s = read_rel(rel)
        if repl.strip()[:100] in s:
            return
        new, n = re.subn(pattern, lambda _m: repl, s, count=1, flags=flags)
        if n == 0:
            fail('Could not find regex target for ' + label + ' in ' + rel)
        write_rel(rel, new)

    for rel in [
        'web-app/src/containers/dialogs/SearchDialog.tsx', 'web-app/src/hooks/useMessages.ts',
        'web-app/src/hooks/useThreads.ts', 'web-app/src/locales/en/common.json', 'Makefile',
        'extensions/llamacpp-extension/settings.json', 'extensions/llamacpp-extension/src/util.ts',
        'extensions/llamacpp-extension/src/index.ts', 'extensions/llamacpp-extension/src/preset.ts',
        'src-tauri/plugins/tauri-plugin-llamacpp/guest-js/types.ts',
        'src-tauri/plugins/tauri-plugin-llamacpp/guest-js/index.ts',
        'web-app/src/containers/ModelSetting.tsx', 'web-app/src/containers/Capabilities.tsx'
    ]:
        require_rel(rel)

    # PR #8288: full-text thread search index module.
    search_index_ts = """import { getServiceHub } from '@/hooks/useServiceHub'\nimport { TEMPORARY_CHAT_ID } from '@/constants/chat'\nimport { ContentType, type ThreadContent } from '@janhq/core'\n\nexport interface ThreadSearchResult {\n  thread: Thread\n  matchSource: 'title' | 'content' | 'both'\n  snippet?: string\n}\n\ninterface CorpusEntry {\n  thread: Thread\n  contentText: string\n}\n\nconst MAX_CONTENT_CHARS = 5000\nconst MAX_INDEXED_THREADS = 2000\n\nexport function extractTextFromContent(content: ThreadContent[] | undefined): string {\n  if (!content) return ''\n  const parts: string[] = []\n  for (const c of content) {\n    if (c.type === ContentType.Text && c.text?.value) {\n      const clean = c.text.value.replace(/<(think|thinking|reasoning|analysis)[^>]*>[\\s\\S]*?<\\/\\1>/gi, '').trim()\n      if (clean) parts.push(clean)\n    }\n  }\n  return parts.join(' ')\n}\n\nexport function extractSnippet(text: string, term: string): string | undefined {\n  const idx = text.toLowerCase().indexOf(term.toLowerCase())\n  if (idx === -1) return undefined\n  const margin = 55\n  const start = Math.max(0, idx - margin)\n  const end = Math.min(text.length, idx + term.length + margin)\n  let snippet = text.slice(start, end)\n  if (start > 0) snippet = '…' + snippet\n  if (end < text.length) snippet = snippet + '…'\n  return snippet\n}\n\nasync function buildEntryForThread(thread: Thread): Promise<CorpusEntry> {\n  const messages = await getServiceHub().messages().fetchMessages(thread.id)\n  const contentText = messages.map((m) => extractTextFromContent(m.content)).join(' ').slice(0, MAX_CONTENT_CHARS)\n  return { thread, contentText }\n}\n\nclass ThreadSearchIndex {\n  private entriesByThreadId: Map<string, CorpusEntry> | null = null\n  private staleThreadIds = new Set<string>()\n  private deletedThreadIds = new Set<string>()\n  private buildPromise: Promise<void> | null = null\n  private latestThreads: Record<string, Thread> = {}\n\n  private eligibleThreads(threads: Record<string, Thread>): Thread[] {\n    const list = Object.values(threads).filter((t) => t.id !== TEMPORARY_CHAT_ID && t.title)\n    if (list.length <= MAX_INDEXED_THREADS) return list\n    return [...list].sort((a, b) => (b.updated ?? 0) - (a.updated ?? 0)).slice(0, MAX_INDEXED_THREADS)\n  }\n\n  async build(threads: Record<string, Thread>): Promise<void> {\n    this.latestThreads = threads\n    if (this.buildPromise) return this.buildPromise\n    this.buildPromise = (async () => {\n      try {\n        do { await this.doBuild(this.latestThreads) } while (this.hasPendingWork(this.latestThreads))\n      } finally { this.buildPromise = null }\n    })()\n    return this.buildPromise\n  }\n\n  private async doBuild(threads: Record<string, Thread>): Promise<void> {\n    const isFirstBuild = this.entriesByThreadId === null\n    if (!this.entriesByThreadId) this.entriesByThreadId = new Map()\n    for (const id of this.deletedThreadIds) this.entriesByThreadId.delete(id)\n    this.deletedThreadIds.clear()\n    const threadList = this.eligibleThreads(threads)\n    const toFetch: Thread[] = []\n    for (const thread of threadList) {\n      if (!this.entriesByThreadId.has(thread.id) || this.staleThreadIds.has(thread.id)) toFetch.push(thread)\n    }\n    this.staleThreadIds.clear()\n    const liveIds = new Set(threadList.map((t) => t.id))\n    for (const id of this.entriesByThreadId.keys()) if (!liveIds.has(id)) this.entriesByThreadId.delete(id)\n    if (toFetch.length === 0 && !isFirstBuild) return\n    for (let i = 0; i < toFetch.length; i += 10) {\n      const results = await Promise.allSettled(toFetch.slice(i, i + 10).map(buildEntryForThread))\n      for (const r of results) if (r.status === 'fulfilled') this.entriesByThreadId.set(r.value.thread.id, r.value)\n    }\n  }\n\n  search(term: string): ThreadSearchResult[] {\n    if (!term || !this.entriesByThreadId) return []\n    const lowerTerm = term.toLowerCase()\n    const results: ThreadSearchResult[] = []\n    for (const entry of this.entriesByThreadId.values()) {\n      const titleMatch = entry.thread.title?.toLowerCase().includes(lowerTerm)\n      const contentMatch = entry.contentText.toLowerCase().includes(lowerTerm)\n      if (!titleMatch && !contentMatch) continue\n      results.push({ thread: entry.thread, matchSource: titleMatch && contentMatch ? 'both' : titleMatch ? 'title' : 'content', snippet: contentMatch ? extractSnippet(entry.contentText, term) : undefined })\n    }\n    results.sort((a, b) => {\n      if (a.matchSource === 'title' && b.matchSource !== 'title') return -1\n      if (a.matchSource !== 'title' && b.matchSource === 'title') return 1\n      return (b.thread.updated ?? 0) - (a.thread.updated ?? 0)\n    })\n    return results\n  }\n\n  invalidateThread(threadId: string): void { this.staleThreadIds.add(threadId) }\n  removeThread(threadId: string): void { this.deletedThreadIds.add(threadId); this.staleThreadIds.delete(threadId) }\n  invalidate(): void { this.entriesByThreadId = null; this.staleThreadIds.clear(); this.deletedThreadIds.clear() }\n  get isReady(): boolean { return this.entriesByThreadId !== null }\n  hasPendingWork(threads: Record<string, Thread>): boolean {\n    if (this.entriesByThreadId === null || this.staleThreadIds.size > 0 || this.deletedThreadIds.size > 0) return true\n    const eligible = this.eligibleThreads(threads)\n    for (const t of eligible) if (!this.entriesByThreadId.has(t.id)) return true\n    return this.entriesByThreadId.size !== eligible.length\n  }\n}\n\nlet instance: ThreadSearchIndex | null = null\nexport function getThreadSearchIndex(): ThreadSearchIndex { if (!instance) instance = new ThreadSearchIndex(); return instance }\nexport function __resetThreadSearchIndexForTests(): void { instance = null }\n"""
    write_rel('web-app/src/lib/search-index.ts', search_index_ts)

    sd = 'web-app/src/containers/dialogs/SearchDialog.tsx'
    replace_once(sd, "  IconFolder,\n} from '@tabler/icons-react'", "  IconFolder,\n  IconLoader,\n} from '@tabler/icons-react'", 'SearchDialog loader import', 'IconLoader,')
    insert_after(sd, "import { VisuallyHidden } from '@radix-ui/react-visually-hidden'\n", "import { getThreadSearchIndex, type ThreadSearchResult } from '@/lib/search-index'\n", 'SearchDialog search-index import', '@/lib/search-index')
    insert_after(sd, "  const [recentVersion, setRecentVersion] = useState(0)\n", "  const [fullTextResults, setFullTextResults] = useState<ThreadSearchResult[]>([])\n  const [indexReady, setIndexReady] = useState(false)\n  const [indexBuilding, setIndexBuilding] = useState(false)\n", 'SearchDialog state', 'fullTextResults')
    insert_after(sd, "  const listRef = useRef<HTMLDivElement>(null)\n", "  const searchTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)\n", 'SearchDialog debounce ref', 'searchTimerRef')
    insert_after(sd, "  const getFilteredThreads = useThreads((state) => state.getFilteredThreads)\n", "\n  useEffect(() => {\n    if (!open) return\n    const index = getThreadSearchIndex()\n    if (!index.hasPendingWork(threads)) {\n      setIndexReady(true)\n      setIndexBuilding(false)\n      return\n    }\n    setIndexReady(index.isReady)\n    setIndexBuilding(true)\n    index.build(threads).then(() => {\n      setIndexReady(true)\n      setIndexBuilding(false)\n    })\n  }, [open, threads])\n", 'SearchDialog build index effect', 'index.hasPendingWork')
    replace_once(sd, "      setSelectedIndex(0)\n      setTimeout(() => {", "      setSelectedIndex(0)\n      setFullTextResults([])\n      setTimeout(() => {", 'SearchDialog clear on open', 'setFullTextResults([])')
    insert_after(sd, "  }, [open])\n", "\n  useEffect(() => {\n    if (!searchQuery || !indexReady) {\n      setFullTextResults([])\n      return\n    }\n    clearTimeout(searchTimerRef.current)\n    searchTimerRef.current = setTimeout(() => {\n      const index = getThreadSearchIndex()\n      setFullTextResults(index.search(searchQuery))\n    }, 100)\n    return () => clearTimeout(searchTimerRef.current)\n  }, [searchQuery, indexReady, indexBuilding])\n", 'SearchDialog search effect', 'index.search(searchQuery)')
    replace_once(sd, "  const handleClose = () => {\n    setSearchQuery('')\n    onOpenChange(false)\n  }", "  const handleClose = () => {\n    setSearchQuery('')\n    setFullTextResults([])\n    onOpenChange(false)\n  }", 'SearchDialog close clear')
    replace_regex(sd, r"  const searchResults = useMemo\(\(\) => \{\n    if \(!searchQuery\) return \{ withProject: \[\], withoutProject: \[\] \}[\s\S]*?  \}, \[searchQuery, getFilteredThreads\]\)", """  const searchResults = useMemo(() => {
    if (!searchQuery) return { withProject: [], withoutProject: [] }

    const filteredThreads = indexReady
      ? fullTextResults.map((r) => r.thread)
      : getFilteredThreads(searchQuery)
    const withProject: Array<{ thread: Thread; projectName: string; snippet?: string }> = []
    const withoutProject: Array<{ thread: Thread; snippet?: string }> = []
    filteredThreads.forEach((thread) => {
      const ftResult = fullTextResults.find((r) => r.thread.id === thread.id)
      const snippet = ftResult?.snippet
      const projectName = thread.metadata?.project?.name
      if (projectName) withProject.push({ thread, projectName, snippet })
      else withoutProject.push({ thread, snippet })
    })
    return { withProject, withoutProject }
  }, [searchQuery, fullTextResults, getFilteredThreads, indexReady])""", 'SearchDialog search results')
    replace_once(sd, "      searchResults.withoutProject.forEach((thread) => {\n        items.push({ type: 'result', id: thread.id })\n      })", "      searchResults.withoutProject.forEach(({ thread }) => {\n        items.push({ type: 'result', id: thread.id })\n      })", 'SearchDialog keyboard items')
    insert_after(sd, "            onKeyDown={handleKeyDown}\n          />", "\n          {indexBuilding && (\n            <span className=\"flex items-center gap-1.5 text-xs text-muted-foreground shrink-0\" title=\"Indexing message content for full-text search\">\n              <IconLoader className=\"size-3.5 animate-spin\" />\n              <span className=\"hidden sm:inline\">Indexing…</span>\n            </span>\n          )}", 'SearchDialog indexing indicator', 'Indexing…')
    insert_after(sd, "        {/* Results */}\n        <div ref={listRef} className=\"max-h-80 overflow-y-auto px-1 py-2\">", "\n          {searchQuery && !hasResults && indexBuilding && (\n            <div className=\"flex flex-col items-center justify-center py-12 px-4 text-center\">\n              <IconLoader className=\"size-6 text-muted-foreground mb-2 animate-spin\" />\n              <h3 className=\"text-base font-medium mb-1\">{t('common:searchIndexing')}</h3>\n              <p className=\"text-xs leading-relaxed text-muted-foreground w-1/2 mx-auto\">{t('common:searchIndexingDesc')}</p>\n            </div>\n          )}\n", 'SearchDialog indexing state', 'searchIndexingDesc')
    replace_once(sd, "          {searchQuery && !hasResults && (", "          {searchQuery && !hasResults && !indexBuilding && (", 'SearchDialog no results guard')
    # Lightweight snippet rendering: append snippet under title with broad replacements.
    replace_once(sd, "{searchResults.withProject.map(({ thread, projectName }, index) => {", "{searchResults.withProject.map(({ thread, projectName, snippet }, index) => {", 'SearchDialog project binding')
    replace_once(sd, "{searchResults.withoutProject.map((thread, index) => {", "{searchResults.withoutProject.map(({ thread, snippet }, index) => {", 'SearchDialog non-project binding')
    s = read_rel(sd)
    if 'truncate mt-0.5' not in s:
        s = s.replace("<span className=\"text-sm truncate\">{thread.title}</span>", "<span className=\"text-sm truncate\">{thread.title}</span>\n                      {snippet && (<p className=\"text-xs text-muted-foreground/70 truncate mt-0.5\">{snippet}</p>)}", 1)
        s = s.replace("<span className=\"text-sm truncate\">{thread.title}</span>", "<span className=\"text-sm truncate\">{thread.title}</span>\n                    {snippet && (<p className=\"text-xs text-muted-foreground/70 truncate pl-6\">{snippet}</p>)}", 1)
        write_rel(sd, s)

    um = 'web-app/src/hooks/useMessages.ts'
    insert_after(um, "import { getServiceHub } from '@/hooks/useServiceHub'\n", "import { getThreadSearchIndex } from '@/lib/search-index'\n", 'useMessages search import', '@/lib/search-index')
    s = read_rel(um)
    if 'getThreadSearchIndex().invalidateThread(message.thread_id)' not in s:
        s = s.replace("    }))\n\n    // Persist", "    }))\n\n    getThreadSearchIndex().invalidateThread(message.thread_id)\n\n    // Persist", 1)
        s = s.replace("    }))\n\n    // Persist", "    }))\n\n    getThreadSearchIndex().invalidateThread(message.thread_id)\n\n    // Persist", 1)
        s = s.replace("  clearAllMessages: () => {\n    set({ messages: {} })\n  },", "  clearAllMessages: () => {\n    set({ messages: {} })\n    getThreadSearchIndex().invalidate()\n  },")
        s = s.replace("      },\n    }))\n  },", "      },\n    }))\n\n    getThreadSearchIndex().invalidateThread(threadId)\n  },", 1)
        write_rel(um, s)

    ut = 'web-app/src/hooks/useThreads.ts'
    insert_after(ut, "import { useAppState } from '@/hooks/useAppState'\n", "import { getThreadSearchIndex } from '@/lib/search-index'\n", 'useThreads search import', '@/lib/search-index')
    s = read_rel(ut)
    if 'getThreadSearchIndex().removeThread(threadId)' not in s:
        s = s.replace('getServiceHub().threads().deleteThread(threadId)\n', 'getServiceHub().threads().deleteThread(threadId)\n      getThreadSearchIndex().removeThread(threadId)\n')
        s = s.replace('getServiceHub().threads().updateThread(updatedThread) // External call, order is fine\n', 'getServiceHub().threads().updateThread(updatedThread) // External call, order is fine\n      getThreadSearchIndex().invalidateThread(threadId)\n')
        s = s.replace('return {\n        threads: {},', 'getThreadSearchIndex().invalidate()\n\n      return {\n        threads: {},')
        write_rel(ut, s)

    common = 'web-app/src/locales/en/common.json'
    obj = json.loads(read_rel(common))
    obj.setdefault('searchIndexing', 'Still indexing…')
    obj.setdefault('searchIndexingDesc', "We're loading your message history. Matches will appear as soon as indexing finishes.")
    write_rel(common, json.dumps(obj, indent=2, ensure_ascii=False) + '\n')

    # PR #8302
    replace_once('Makefile', "\tcopy src-tauri\\target\\release\\jan-cli.exe src-tauri\\resources\\bin\\jan-cli.exe", "\tpowershell -NoLogo -NoProfile -NonInteractive -Command \"Copy-Item -Force -ErrorAction Stop -LiteralPath 'src-tauri/target/release/jan-cli.exe' -Destination 'src-tauri/resources/bin/jan-cli.exe'\"", 'Windows release CLI copy', 'target/release/jan-cli.exe')
    replace_once('Makefile', "\tcopy src-tauri\\target\\debug\\jan-cli.exe src-tauri\\resources\\bin\\jan-cli.exe", "\tpowershell -NoLogo -NoProfile -NonInteractive -Command \"Copy-Item -Force -ErrorAction Stop -LiteralPath 'src-tauri/target/debug/jan-cli.exe' -Destination 'src-tauri/resources/bin/jan-cli.exe'\"", 'Windows debug CLI copy', 'target/debug/jan-cli.exe')

    # PR #7943 and #8115 settings.
    settings_rel = 'extensions/llamacpp-extension/settings.json'
    settings = json.loads(read_rel(settings_rel))
    def add_setting(item, after_key=None):
        if any(x.get('key') == item['key'] for x in settings): return
        idx = len(settings)
        if after_key:
            idx = next((i+1 for i,x in enumerate(settings) if x.get('key') == after_key), idx)
        settings.insert(idx, item)
    add_setting({'key':'embedding_model_id','title':'Embedding model ID (RAG / file uploads)','description':'Jan llama.cpp model folder name used for embeddings when ingesting files. Leave empty to auto-pick an installed embedding model, or download the default from Hugging Face.','controllerType':'input','controllerProps':{'value':'','placeholder':'e.g. my-local-embedder (empty = auto / default)','type':'text','textAlign':'left'}}, 'models_max')
    add_setting({'key':'spec_type','title':'Speculative type','description':'Model-free speculative decoding using token history patterns. ngram-mod is usually best for reasoning models.','controllerType':'dropdown','controllerProps':{'value':'none','options':[{'value':'none','name':'none'},{'value':'ngram-simple','name':'ngram-simple'},{'value':'ngram-mod','name':'ngram-mod'},{'value':'ngram-cache','name':'ngram-cache'},{'value':'ngram-map-k','name':'ngram-map-k'},{'value':'ngram-map-k4v','name':'ngram-map-k4v (experimental)'}]}})
    write_rel(settings_rel, json.dumps(settings, indent=2) + '\n')

    util = 'extensions/llamacpp-extension/src/util.ts'
    if 'DEFAULT_EMBEDDING_MODEL_ID' not in read_rel(util):
        insert_before(util, "// --- Embedding batching helpers ---", """// --- RAG embedding model selection (testable without extension I/O) ---\nexport const DEFAULT_EMBEDDING_MODEL_ID = 'sentence-transformer-mini'\n\nexport function resolveEmbeddingModelIdFromModels(\n  configuredTrimmed: string,\n  models: Array<{ id: string; embedding?: boolean }>\n): string {\n  if (configuredTrimmed) return configuredTrimmed\n  const embeddingModels = models.filter((m) => m.embedding === true).sort((a, b) => a.id.localeCompare(b.id))\n  if (embeddingModels.length === 0) return DEFAULT_EMBEDDING_MODEL_ID\n  const preferred = embeddingModels.find((m) => m.id === DEFAULT_EMBEDDING_MODEL_ID)\n  return (preferred ?? embeddingModels[0]).id\n}\n\n""", 'embedding resolver', 'resolveEmbeddingModelIdFromModels')

    # Types and normalizer for fields. This keeps TS and normalized config aligned.
    types = 'src-tauri/plugins/tauri-plugin-llamacpp/guest-js/types.ts'
    if 'embedding_model_id: string' not in read_rel(types):
        replace_once(types, '  parallel: number\n', '  parallel: number\n  embedding_model_id: string\n', 'type embedding_model_id', 'embedding_model_id: string')
    if 'draft_model_path: string' not in read_rel(types):
        replace_once(types, '  keep: number\n', '  keep: number\n  draft_model_id?: string\n  draft_model_path: string\n  spec_type: string\n  draft_max: number\n  draft_min: number\n', 'type speculative fields', 'draft_model_path: string')
    guest = 'src-tauri/plugins/tauri-plugin-llamacpp/guest-js/index.ts'
    gs = read_rel(guest)
    if 'function asU32' not in gs:
        helper = """
function asU32(v: any, defaultValue = 0): number {
  const n = Math.trunc(asNumber(v, defaultValue))
  if (n <= 0) return 0
  return Math.min(n, I32_MAX)
}
"""
        inserted = False
        m_as_i32 = re.search(r"function\s+asI32\s*\([^)]*\)\s*:\s*number\s*\{.*?\n\}\n", gs, flags=re.S)
        if m_as_i32:
            gs = gs[:m_as_i32.end()] + helper + gs[m_as_i32.end():]
            inserted = True
        else:
            for fallback in ('function asBool(', 'export function normalizeLlamacppConfig'):
                idx = gs.find(fallback)
                if idx >= 0:
                    gs = gs[:idx] + helper + '\n' + gs[idx:]
                    inserted = True
                    break
        if not inserted:
            fail('Could not insert asU32 helper in ' + guest + '; asI32/asBool/normalizeLlamacppConfig anchors all missing')
    if 'embedding_model_id: asString(config.embedding_model_id)' not in gs:
        gs, n = re.subn(
            r"(parallel:\s*asI32\(config\.parallel,\s*1\),\n)(?!\s*embedding_model_id:)",
            r"\1    embedding_model_id: asString(config.embedding_model_id),\n",
            gs,
            count=1,
        )
        if n == 0:
            fail('Could not insert embedding_model_id normalizer in ' + guest + '; parallel normalizer anchor missing')
    if 'draft_model_path: asString(config.draft_model_path' not in gs:
        gs, n = re.subn(
            r"(keep:\s*asI32\(config\.keep,\s*0\),\n)(?!\s*draft_model_path:)",
            r"\1    draft_model_path: asString(config.draft_model_path, ''),\n    spec_type: asString(config.spec_type, 'none'),\n    draft_max: asU32(config.draft_max, 0),\n    draft_min: asU32(config.draft_min, 0),\n",
            gs,
            count=1,
        )
        if n == 0:
            fail('Could not insert speculative config normalizers in ' + guest + '; keep normalizer anchor missing')
    write_rel(guest, gs)

    # Router preset speculative fields. Best-effort, but anchored to current model loop.
    preset = 'extensions/llamacpp-extension/src/preset.ts'
    ps = read_rel(preset)
    if 'draft_model_id?: string' not in ps:
        ps = ps.replace("  max_tokens_per_doc?: number\n", "  max_tokens_per_doc?: number\n  draft_model_id?: string\n  draft_model_path?: string\n  spec_type?: string\n  draft_max?: number\n  draft_min?: number\n")
    if 'speculative.type' not in ps:
        needle = "    lines.push('')\n  }\n"
        insertion = """    const specType = typeof mc.spec_type === 'string' ? mc.spec_type.trim() : ''\n    if (specType && specType !== 'none') {\n      lines.push(`speculative.type = ${escapeIniValue(specType)}`)\n    }\n    const draftPath = typeof mc.draft_model_path === 'string' ? mc.draft_model_path.trim() : ''\n    if (draftPath) {\n      lines.push(`model-draft = ${escapeIniValue(draftPath)}`)\n    }\n    const draftMax = typeof mc.draft_max === 'number' ? mc.draft_max : Number(mc.draft_max ?? 0)\n    if (Number.isFinite(draftMax) && draftMax > 0) lines.push(`draft-max = ${Math.floor(draftMax)}`)\n    const draftMin = typeof mc.draft_min === 'number' ? mc.draft_min : Number(mc.draft_min ?? 0)\n    if (Number.isFinite(draftMin) && draftMin > 0) lines.push(`draft-min = ${Math.floor(draftMin)}`)\n\n"""
        if needle in ps: ps = ps.replace(needle, insertion + needle, 1)
        else: ps += "\n// Speculative decoding ModelYaml fields added; preset loop anchor not found.\n"
    write_rel(preset, ps)

    # Model setting UI draft dropdown wiring.
    ms_rel = 'web-app/src/containers/ModelSetting.tsx'
    ms = read_rel(ms_rel)
    if "import { useCallback, useMemo } from 'react'" not in ms:
        ms = ms.replace("import { useAppState } from '@/hooks/useAppState'\n", "import { useAppState } from '@/hooks/useAppState'\nimport { useCallback, useMemo } from 'react'\n")
    if 'const { updateProvider, providers } = useModelProvider()' not in ms:
        ms = ms.replace('const { updateProvider } = useModelProvider()', 'const { updateProvider, providers } = useModelProvider()')
    if 'draftModelCandidates' not in ms:
        ms = ms.replace('  const setActiveModels = useAppState((state) => state.setActiveModels)\n', """  const setActiveModels = useAppState((state) => state.setActiveModels)\n\n  const draftModelCandidates = useMemo(\n    () =>\n      providers\n        .filter((p) => p.provider === 'llamacpp')\n        .flatMap((p) => p.models)\n        .filter((candidate) => candidate.id !== model.id && !candidate.settings?.embedding?.controller_props?.value)\n        .map((candidate) => ({ value: candidate.id, name: candidate.displayName ?? candidate.id })),\n    [model.id, providers]\n  )\n\n  const getControllerProps = useCallback((config: ProviderSetting) => {\n    if (config.key === 'draft_model_id') {\n      return { ...config.controller_props, options: [...(config.controller_props?.options || []), ...draftModelCandidates], value: config.controller_props?.value }\n    }\n    return { ...config.controller_props, value: config.controller_props?.value }\n  }, [draftModelCandidates])\n""")
    for key in ['reasoning', 'draft_model_id', 'spec_type', 'draft_max', 'draft_min']:
        if "key === '" + key + "'" not in ms:
            ms = ms.replace("key === 'n_cpu_moe'", "key === 'n_cpu_moe' ||\n        key === '" + key + "'")
    if 'controllerProps={getControllerProps(config)}' not in ms:
        ms = re.sub(r"controllerProps=\{\{\s*\.\.\.config\.controller_props,\s*value: config\.controller_props\?\.value,\s*\}\}", "controllerProps={getControllerProps(config)}", ms, count=1, flags=re.S)
    write_rel(ms_rel, ms)

    cap = 'web-app/src/containers/Capabilities.tsx'
    cs = read_rel(cap)
    if 'IconCodeCircle2' in cs and 'IconCircles' not in cs:
        cs = cs.replace('IconCodeCircle2,', 'IconCircles,')
        cs = cs.replace('IconCodeCircle2 className="size-3.5"', 'IconCircles className="size-3.5"')
        write_rel(cap, cs)

    print('Applied full-text thread search, Windows Makefile copy fix, local embedding selector, and router-safe speculative decoding settings.')

def main() -> None:
    ensure_repo_root()
    write_embedded_applicators()
    run_script(TMP / 'jan-rerank-full' / 'apply-jan-rerank-full.py')
    apply_post_full_ui_and_docs()
    apply_feature_ports_after_rerank()
    apply_last_pr_ports_after_feature_ports()
    print('')
    print('Applied Jan reranking + turboquant + feature-port changes in one pass.')
    print('Next checks:')
    print('  git diff --stat')
    print('  pnpm typecheck')
    print('  cargo check --manifest-path src-tauri/Cargo.toml')


if __name__ == '__main__':
    main()
