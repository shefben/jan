import {
  RAGExtension,
  MCPTool,
  MCPToolCallResult,
  ExtensionTypeEnum,
  VectorDBExtension,
  type AttachmentInput,
  type SettingComponentProps,
  AIEngine,
  type AttachmentFileInfo,
} from '@janhq/core'
import './env.d'
import { getRAGTools, RETRIEVE, LIST_ATTACHMENTS, GET_CHUNKS } from './tools'
import * as ragApi from '@janhq/tauri-plugin-rag-api'

export default class RagExtension extends RAGExtension {
  private config = {
    enabled: true,
    retrievalLimit: 3,
    retrievalThreshold: 0.3,
    chunkSizeChars: 512,
    overlapChars: 64,
    searchMode: 'auto' as 'auto' | 'ann' | 'linear',
    maxFileSizeMB: 100,
    parseMode: 'auto' as 'auto' | 'inline' | 'embeddings' | 'prompt',
    autoInlineContextRatio: 0.75,
    rerankingMode: 'auto' as 'auto' | 'off' | 'model',
    rerankingModel: 'auto',
    rerankTopKBefore: 60,
    rerankCandidateMultiplier: 5,
    rerankMaxCandidates: 40,
    rerankTopNAfter: 8,
    rerankMinRelevanceScore: 0,
    rerankMaxTokensPerDoc: 4096,
    rerankEvidenceMode: 'off' as 'off' | 'top_n' | 'all',
  }

  private rerankStatusCache: { at: number; enabled: boolean } | null = null

  private async isLocalRerankingAvailable(): Promise<boolean> {
    const now = Date.now()
    if (this.rerankStatusCache && now - this.rerankStatusCache.at < 30000) {
      return this.rerankStatusCache.enabled
    }
    try {
      const llm = window.core?.extensionManager.getByName('@janhq/llamacpp-extension') as { getRerankStatus?: () => Promise<Record<string, unknown>> }
      const status = await llm?.getRerankStatus?.()
      const enabled = status?.enabled === true
      this.rerankStatusCache = { at: now, enabled }
      return enabled
    } catch {
      this.rerankStatusCache = { at: now, enabled: false }
      return false
    }
  }

  async onLoad(): Promise<void> {
    try {
      await this.configure()
    } catch (e) {
      console.error('[RAG] configure() failed during onLoad:', e)
    }
    // Check ANN availability on load (already self-contained try/catch)
    this.checkANNAvailability()
  }

  onUnload(): void {}

  async configure() {
    const settings = structuredClone(SETTINGS) as SettingComponentProps[]
    await this.registerSettings(settings)
    this.config.enabled = await this.getSetting('enabled', this.config.enabled)
    this.config.maxFileSizeMB = await this.getSetting(
      'max_file_size_mb',
      this.config.maxFileSizeMB
    )
    this.config.retrievalLimit = await this.getSetting(
      'retrieval_limit',
      this.config.retrievalLimit
    )
    this.config.retrievalThreshold = await this.getSetting(
      'retrieval_threshold',
      this.config.retrievalThreshold
    )
    // Prefer char-based keys; fall back to legacy token keys for backward compatibility
    this.config.chunkSizeChars =
      (await this.getSetting('chunk_size_chars', this.config.chunkSizeChars)) ||
      (await this.getSetting('chunk_size_tokens', this.config.chunkSizeChars))
    this.config.overlapChars =
      (await this.getSetting('overlap_chars', this.config.overlapChars)) ||
      (await this.getSetting('overlap_tokens', this.config.overlapChars))
    this.config.searchMode = await this.getSetting(
      'search_mode',
      this.config.searchMode
    )
    this.config.parseMode = await this.getSetting(
      'parse_mode',
      this.config.parseMode
    )
    this.config.autoInlineContextRatio = await this.getSetting(
      'auto_inline_context_ratio',
      this.config.autoInlineContextRatio
    )
    this.config.rerankingMode = await this.getSetting('reranking_mode', this.config.rerankingMode)
    this.config.rerankingModel = await this.getSetting('reranking_model', this.config.rerankingModel)
    this.config.rerankTopKBefore = await this.getSetting('rerank_top_k_before', this.config.rerankTopKBefore)
    this.config.rerankCandidateMultiplier = await this.getSetting('rerank_candidate_multiplier', this.config.rerankCandidateMultiplier)
    this.config.rerankMaxCandidates = await this.getSetting('rerank_max_candidates', this.config.rerankMaxCandidates)
    this.config.rerankTopNAfter = await this.getSetting('rerank_top_n_after', this.config.rerankTopNAfter)
    this.config.rerankMinRelevanceScore = await this.getSetting('rerank_min_relevance_score', this.config.rerankMinRelevanceScore)
    this.config.rerankMaxTokensPerDoc = await this.getSetting('rerank_max_tokens_per_doc', this.config.rerankMaxTokensPerDoc)
    this.config.rerankEvidenceMode = await this.getSetting('rerank_evidence_mode', this.config.rerankEvidenceMode)
  }

  async checkANNAvailability() {
    try {
      const vec = window.core?.extensionManager.get(
        ExtensionTypeEnum.VectorDB
      ) as unknown as VectorDBExtension
      if (vec?.getStatus) {
        const status = await vec.getStatus()
        console.log(
          '[RAG] Vector DB ANN support:',
          status.ann_available ? '✓ AVAILABLE' : '✗ NOT AVAILABLE'
        )
        if (!status.ann_available) {
          console.warn(
            '[RAG] Warning: sqlite-vec not loaded. Collections will use slower linear search.'
          )
        }
      }
    } catch (e) {
      console.error('[RAG] Failed to check ANN status:', e)
    }
  }

  async getTools(): Promise<MCPTool[]> {
    return getRAGTools(this.config.retrievalLimit)
  }

  async getToolNames(): Promise<string[]> {
    // Keep this in sync with getTools() but without building full schemas
    return [LIST_ATTACHMENTS, RETRIEVE, GET_CHUNKS]
  }

  async callTool(
    toolName: string,
    args: Record<string, unknown>
  ): Promise<MCPToolCallResult> {
    switch (toolName) {
      case LIST_ATTACHMENTS:
        return this.listAttachments(args)
      case RETRIEVE:
        return this.retrieve(args)
      case GET_CHUNKS:
        return this.getChunks(args)
      default:
        return {
          error: `Unknown tool: ${toolName}`,
          content: [{ type: 'text', text: `Unknown tool: ${toolName}` }],
        }
    }
  }

  private async listAttachments(
    args: Record<string, unknown>
  ): Promise<MCPToolCallResult> {
    const threadId = String(args['thread_id'] || '')
    const scope = String(args['scope'] || 'thread')

    if (!threadId && scope === 'thread') {
      return {
        error: 'Missing thread_id',
        content: [{ type: 'text', text: 'Missing thread_id' }],
      }
    }
    try {
      const vec = window.core?.extensionManager.get(
        ExtensionTypeEnum.VectorDB
      ) as unknown as VectorDBExtension
      if (!vec?.listAttachments && !vec?.listAttachmentsForProject) {
        return {
          error: 'Vector DB extension missing listAttachments',
          content: [
            {
              type: 'text',
              text: 'Vector DB extension missing listAttachments',
            },
          ],
        }
      }

      let files: AttachmentFileInfo[] = []
      if (scope === 'project' && vec.listAttachmentsForProject) {
        files = await vec.listAttachmentsForProject(threadId)
      } else if (vec.listAttachments) {
        files = await vec.listAttachments(threadId)
      }

      return {
        error: '',
        content: [
          {
            type: 'text',
            text: JSON.stringify({
              thread_id: threadId,
              scope,
              attachments: files || [],
            }),
          },
        ],
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : JSON.stringify(e)
      return {
        error: msg,
        content: [{ type: 'text', text: `List attachments failed: ${JSON.stringify(msg)}` }],
      }
    }
  }

  private async retrieve(
    args: Record<string, unknown>
  ): Promise<MCPToolCallResult> {
    const threadId = String(args['thread_id'] || '')
    const projectId = String(args['project_id'] || '')
    const query = String(args['query'] || '')
    const rawFileIds = args['file_ids']
    const fileIds = Array.isArray(rawFileIds)
      ? rawFileIds.map((id) => String(id)).filter(Boolean)
      : undefined
    const scope = String(args['scope'] || 'thread')

    // Use project_id as threadId when scope is project
    const effectiveThreadId = scope === 'project' ? projectId || threadId : threadId

    const s = this.config
    const requestedTopK = Math.max(1, Number(args['top_k'] ?? s.retrievalLimit ?? 3))
    const requestedRerank = args['rerank']
    const requestedRerankModel =
      typeof args['reranking_model'] === 'string' && args['reranking_model'].trim()
        ? String(args['reranking_model']).trim()
        : undefined
    let shouldRerank =
      requestedRerank === false || requestedRerank === 'false'
        ? false
        : requestedRerank === true || requestedRerank === 'true'
          ? true
          : s.rerankingMode !== 'off'
    if (shouldRerank && s.rerankingMode === 'auto' && requestedRerank !== true && requestedRerank !== 'true') {
      shouldRerank = await this.isLocalRerankingAvailable()
    }
    const multiplier = Math.max(1, Number(args['rerank_candidate_multiplier'] ?? s.rerankCandidateMultiplier ?? 1))
    const maxCandidates = Math.max(
      requestedTopK,
      Number(args['rerank_max_candidates'] ?? s.rerankMaxCandidates ?? s.rerankTopKBefore ?? 40)
    )
    const legacyBefore = Math.max(requestedTopK, Number(args['rerank_top_k_before'] ?? s.rerankTopKBefore ?? 0))
    const multipliedBefore = Math.max(requestedTopK, Math.ceil(requestedTopK * multiplier))
    const topK = shouldRerank ? Math.min(maxCandidates, Math.max(legacyBefore, multipliedBefore)) : requestedTopK
    const threshold = shouldRerank ? Math.min(s.retrievalThreshold ?? 0.3, 0.05) : (s.retrievalThreshold ?? 0.3)
    const mode: 'auto' | 'ann' | 'linear' = s.searchMode || 'auto'
    const rerankOverrides = {
      model: requestedRerankModel,
      topNAfter: Number(args['rerank_top_n_after'] ?? s.rerankTopNAfter ?? requestedTopK),
      minRelevanceScore:
        args['rerank_min_relevance_score'] === undefined
          ? s.rerankMinRelevanceScore
          : Number(args['rerank_min_relevance_score']),
      maxTokensPerDoc:
        args['rerank_max_tokens_per_doc'] === undefined
          ? s.rerankMaxTokensPerDoc
          : Number(args['rerank_max_tokens_per_doc']),
      evidenceMode:
        args['rerank_evidence_mode'] === 'top_n' || args['rerank_evidence_mode'] === 'all' || args['rerank_evidence_mode'] === 'off'
          ? args['rerank_evidence_mode']
          : s.rerankEvidenceMode,
    }

    if (s.enabled === false) {
      return {
        error: 'Attachments feature disabled',
        content: [
          {
            type: 'text',
            text: 'Attachments are disabled in Settings. Enable them to use retrieval.',
          },
        ],
      }
    }
    if (!query || (!threadId && scope === 'thread') || (scope === 'project' && !effectiveThreadId)) {
      return {
        error: 'Missing thread_id, project_id, or query',
        content: [{ type: 'text', text: 'Missing required parameters' }],
      }
    }

    try {
      // Resolve extensions
      const vec = window.core?.extensionManager.get(
        ExtensionTypeEnum.VectorDB
      ) as unknown as VectorDBExtension
      if (!vec?.searchCollection && !vec?.searchCollectionForProject) {
        return {
          error: 'RAG dependencies not available',
          content: [
            { type: 'text', text: 'Vector DB extension not available' },
          ],
        }
      }

      const queryEmb = (await this.embedTexts([query]))?.[0]
      if (!queryEmb) {
        return {
          error: 'Failed to compute embeddings',
          content: [{ type: 'text', text: 'Failed to compute embeddings' }],
        }
      }

      let results
      if (scope === 'project' && vec.searchCollectionForProject) {
        results = await vec.searchCollectionForProject(
          effectiveThreadId,
          queryEmb,
          topK,
          threshold,
          mode,
          fileIds
        )
      } else {
        results = await vec.searchCollection!(
          effectiveThreadId,
          queryEmb,
          topK,
          threshold,
          mode,
          fileIds
        )
      }

      let citations =
        results?.map((r: any) => ({
          id: r.id,
          text: r.text,
          score: r.score,
          file_id: r.file_id,
          chunk_file_order: r.chunk_file_order,
        })) ?? []
      let reranking: Record<string, unknown> = { enabled: false }
      if (shouldRerank && citations.length > 1) {
        const reranked = await this.rerankCitations(query, citations, requestedTopK, rerankOverrides)
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
        reranking: { applied: reranking.enabled === true, ...reranking },
      }
      return {
        error: '',
        content: [{ type: 'text', text: JSON.stringify(payload) }],
      }
    } catch (e) {
      console.error('[RAG] Retrieve error:', e)
      let msg = 'Unknown error'
      if (e instanceof Error) {
        msg = e.message
      } else if (typeof e === 'string') {
        msg = e
      } else if (e && typeof e === 'object') {
        msg = JSON.stringify(e)
      }
      return {
        error: msg,
        content: [{ type: 'text', text: `Retrieve failed: ${msg}` }],
      }
    }
  }

  private async getChunks(
    args: Record<string, unknown>
  ): Promise<MCPToolCallResult> {
    const threadId = String(args['thread_id'] || '')
    const projectId = String(args['project_id'] || '')
    const fileId = String(args['file_id'] || '')
    const startOrder = args['start_order'] as number | undefined
    const endOrder = args['end_order'] as number | undefined
    const scope = String(args['scope'] || 'thread')
    const effectiveThreadId = scope === 'project' ? projectId || threadId : threadId

    if (
      !fileId ||
      startOrder === undefined ||
      endOrder === undefined ||
      (!effectiveThreadId && (scope === 'thread' || scope === 'project'))
    ) {
      return {
        error: 'Missing thread_id/project_id, file_id, start_order, or end_order',
        content: [{ type: 'text', text: 'Missing required parameters' }],
      }
    }

    try {
      const vec = window.core?.extensionManager.get(
        ExtensionTypeEnum.VectorDB
      ) as unknown as VectorDBExtension
      if (!vec?.getChunks && !vec?.getChunksForProject) {
        return {
          error: 'Vector DB extension not available',
          content: [
            { type: 'text', text: 'Vector DB extension not available' },
          ],
        }
      }

      let chunks
      if (scope === 'project' && vec.getChunksForProject) {
        chunks = await vec.getChunksForProject(effectiveThreadId, fileId, startOrder, endOrder)
      } else {
        chunks = await vec.getChunks!(effectiveThreadId, fileId, startOrder, endOrder)
      }

      const payload = {
        thread_id: threadId,
        project_id: projectId,
        scope,
        file_id: fileId,
        chunks: chunks || [],
      }
      return {
        error: '',
        content: [{ type: 'text', text: JSON.stringify(payload) }],
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : JSON.stringify(e)
      return {
        error: msg,
        content: [{ type: 'text', text: `Get chunks failed: ${msg}` }],
      }
    }
  }

  // Desktop-only ingestion by file paths for a project
  async ingestAttachmentsForProject(
    projectId: string,
    files: AttachmentInput[]
  ): Promise<{
    filesProcessed: number
    chunksInserted: number
    files: AttachmentFileInfo[]
  }> {
    if (!projectId || !Array.isArray(files) || files.length === 0) {
      return { filesProcessed: 0, chunksInserted: 0, files: [] }
    }

    // Respect feature flag: do nothing when disabled
    if (this.config.enabled === false) {
      return { filesProcessed: 0, chunksInserted: 0, files: [] }
    }

    const vec = window.core?.extensionManager.get(
      ExtensionTypeEnum.VectorDB
    ) as unknown as VectorDBExtension
    if (!vec?.ingestFileForProject) {
      throw new Error('Vector DB extension does not support project-level ingestion')
    }

    // Load settings
    const s = this.config
    const maxSize = (s?.enabled === false ? 0 : s?.maxFileSizeMB) || undefined
    const chunkSize = s?.chunkSizeChars as number | undefined
    const chunkOverlap = s?.overlapChars as number | undefined

    let totalChunks = 0
    const processedFiles: AttachmentFileInfo[] = []

    for (const f of files) {
      if (!f?.path) continue
      if (maxSize && f.size && f.size > maxSize * 1024 * 1024) {
        throw new Error(
          `File '${f.name}' exceeds size limit (${f.size} bytes > ${maxSize} MB).`
        )
      }

      const fileName = f.name || f.path.split(/[\\/]/).pop()
      const info = await (vec as VectorDBExtension).ingestFileForProject(
        projectId,
        { path: f.path, name: fileName, type: f.type, size: f.size },
        { chunkSize: chunkSize ?? 512, chunkOverlap: chunkOverlap ?? 64 }
      )
      totalChunks += Number(info?.chunk_count || 0)
      processedFiles.push(info)
    }

    return {
      filesProcessed: processedFiles.length,
      chunksInserted: totalChunks,
      files: processedFiles,
    }
  }

  async ingestAttachments(
    threadId: string,
    files: AttachmentInput[]
  ): Promise<{
    filesProcessed: number
    chunksInserted: number
    files: AttachmentFileInfo[]
  }> {
    if (!threadId || !Array.isArray(files) || files.length === 0) {
      return { filesProcessed: 0, chunksInserted: 0, files: [] }
    }

    // Respect feature flag: do nothing when disabled
    if (this.config.enabled === false) {
      return { filesProcessed: 0, chunksInserted: 0, files: [] }
    }

    const vec = window.core?.extensionManager.get(
      ExtensionTypeEnum.VectorDB
    ) as unknown as VectorDBExtension
    if (typeof (vec as any)?.ingestFile !== 'function') {
      throw new Error('Vector DB extension does not support file ingestion')
    }

    // Load settings
    const s = this.config
    const maxSize = (s?.enabled === false ? 0 : s?.maxFileSizeMB) || undefined
    const chunkSize = s?.chunkSizeChars as number | undefined
    const chunkOverlap = s?.overlapChars as number | undefined

    let totalChunks = 0
    const processedFiles: AttachmentFileInfo[] = []

    for (const f of files) {
      if (!f?.path) continue
      if (maxSize && f.size && f.size > maxSize * 1024 * 1024) {
        throw new Error(
          `File '${f.name}' exceeds size limit (${f.size} bytes > ${maxSize} MB).`
        )
      }

      const fileName = f.name || f.path.split(/[\\/]/).pop()
      // Preferred/required path: let Vector DB extension handle full file ingestion
      const info = await (vec as VectorDBExtension).ingestFile(
        threadId,
        { path: f.path, name: fileName, type: f.type, size: f.size },
        { chunkSize: chunkSize ?? 512, chunkOverlap: chunkOverlap ?? 64 }
      )
      totalChunks += Number(info?.chunk_count || 0)
      processedFiles.push(info)
    }

    // Return files we ingested with real IDs directly from ingestFile
    return {
      filesProcessed: processedFiles.length,
      chunksInserted: totalChunks,
      files: processedFiles,
    }
  }

  private async rerankCitations(
    query: string,
    citations: Array<Record<string, unknown>>,
    requestedTopK: number,
    options: {
      model?: string
      topNAfter?: number
      minRelevanceScore?: number
      maxTokensPerDoc?: number
      evidenceMode?: 'off' | 'top_n' | 'all'
    } = {}
  ): Promise<{ citations: Array<Record<string, unknown>>; meta: Record<string, unknown> }> {
    const llm = window.core?.extensionManager.getByName(
      '@janhq/llamacpp-extension'
    ) as AIEngine & {
      rerank?: (req: any) => Promise<{ results: Array<{ index: number; relevance_score: number; evidence?: string; contribution?: string }>; meta?: Record<string, unknown> }>
    }
    if (!llm?.rerank) {
      return {
        citations: citations.slice(0, requestedTopK),
        meta: { enabled: false, applied: false, reason: 'llamacpp extension has no rerank method' },
      }
    }

    try {
      const topN = Math.max(1, Math.min(citations.length, Number(options.topNAfter || this.config.rerankTopNAfter || requestedTopK)))
      const requestedModel =
        options.model ||
        (this.config.rerankingMode === 'model' ? this.config.rerankingModel : 'auto')
      const response = await llm.rerank({
        model: requestedModel,
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
        min_relevance_score:
          typeof options.minRelevanceScore === 'number'
            ? options.minRelevanceScore
            : this.config.rerankMinRelevanceScore || undefined,
        max_tokens_per_doc:
          typeof options.maxTokensPerDoc === 'number' && options.maxTokensPerDoc > 0
            ? options.maxTokensPerDoc
            : this.config.rerankMaxTokensPerDoc || 4096,
        evidence_mode: options.evidenceMode || this.config.rerankEvidenceMode,
      })

      const seen = new Set<number>()
      const reranked: Array<Record<string, unknown>> = []
      for (const result of response.results || []) {
        const index = Number(result.index)
        if (!Number.isInteger(index) || index < 0 || index >= citations.length || seen.has(index)) {
          continue
        }
        seen.add(index)
        const source = citations[index]
        if (!source?.text) continue
        reranked.push({
          ...source,
          rerank_score: result.relevance_score,
          evidence: result.evidence,
          contribution: result.contribution,
        })
      }

      if (reranked.length === 0) {
        return {
          citations: citations.slice(0, requestedTopK),
          meta: {
            enabled: false,
            applied: false,
            fallback_used: true,
            fallback_reason: 'reranker returned no valid result indices',
          },
        }
      }

      return {
        citations: reranked,
        meta: {
          enabled: true,
          applied: true,
          requested_model: requestedModel,
          ...(response.meta ?? {}),
        },
      }
    } catch (e) {
      console.warn('[RAG] Reranking failed, falling back to vector order:', e)
      return {
        citations: citations.slice(0, requestedTopK),
        meta: {
          enabled: false,
          applied: false,
          fallback_used: true,
          error: e instanceof Error ? e.message : String(e),
        },
      }
    }
  }

  onSettingUpdate<T>(key: string, value: T): void {
    switch (key) {
      case 'enabled':
        this.config.enabled = Boolean(value)
        break
      case 'max_file_size_mb':
        this.config.maxFileSizeMB = Number(value)
        break
      case 'auto_inline_context_ratio':
        this.config.autoInlineContextRatio = Number(value)
        break
      case 'retrieval_limit':
        this.config.retrievalLimit = Number(value)
        break
      case 'retrieval_threshold':
        this.config.retrievalThreshold = Number(value)
        break
      case 'chunk_size_chars':
        this.config.chunkSizeChars = Number(value)
        break
      case 'overlap_chars':
        this.config.overlapChars = Number(value)
        break
      case 'search_mode':
        this.config.searchMode = String(value) as 'auto' | 'ann' | 'linear'
        break
      case 'parse_mode':
        this.config.parseMode = String(value) as
          | 'auto'
          | 'inline'
          | 'embeddings'
          | 'prompt'
        break
      case 'reranking_mode':
        this.config.rerankingMode = String(value) as 'auto' | 'off' | 'model'
        break
      case 'reranking_model':
        this.config.rerankingModel = String(value)
        break
      case 'rerank_top_k_before':
        this.config.rerankTopKBefore = Number(value)
        break
      case 'rerank_candidate_multiplier':
        this.config.rerankCandidateMultiplier = Number(value)
        break
      case 'rerank_max_candidates':
        this.config.rerankMaxCandidates = Number(value)
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
    }
  }

  async parseDocument(path: string, type?: string): Promise<string> {
    return await ragApi.parseDocument(path, type || 'application/octet-stream')
  }

  async embed(texts: string[]): Promise<number[][]> {
    if (!texts || texts.length === 0) return []
    return this.embedTexts(texts)
  }

  // Locally implement embedding logic (previously in embeddings-extension)
  private async embedTexts(texts: string[]): Promise<number[][]> {
    const llm = window.core?.extensionManager.getByName(
      '@janhq/llamacpp-extension'
    ) as AIEngine & {
      embed?: (
        texts: string[]
      ) => Promise<{ data: Array<{ embedding: number[]; index: number }> }>
    }
    if (!llm?.embed) throw new Error('llamacpp extension not available')
    const res = await llm.embed(texts)
    const data: Array<{ embedding: number[]; index: number }> = res?.data || []
    const out: number[][] = new Array(texts.length)
    for (const item of data) {
      if (
        Number.isInteger(item.index) &&
        item.index >= 0 &&
        item.index < texts.length &&
        Array.isArray(item.embedding)
      ) {
        out[item.index] = item.embedding
      }
    }
    if (out.some((embedding) => !Array.isArray(embedding))) {
      throw new Error('Embedding response did not contain one embedding per input text')
    }
    return out
  }
}
