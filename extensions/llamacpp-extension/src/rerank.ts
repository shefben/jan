// Capability toggles are intentionally chat-only; rerank requests normalize only rerank fields.
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

export type RerankProfileName = 'default' | 'code' | 'multilingual' | 'long'

export interface NormalizedRerankRequest {
  model: string
  query: string
  documents: string[]
  originalDocuments: RerankDocument[]
  top_n?: number
  return_documents: boolean
  normalize_scores: boolean
  raw_scores: boolean
  max_tokens_per_doc?: number
  min_relevance_score?: number
  evidence_mode: 'off' | 'top_n' | 'all'
  profile: RerankProfileName
  truncated_documents: number
}

export interface RerankTraceMeta {
  model: string
  profile: RerankProfileName
  provider: string
  fallback_used: boolean
  fallback_reason?: string
  candidate_count: number
  returned_count: number
  truncated_documents: number
  normalize_scores: boolean
  raw_scores: boolean
  latency_ms?: number
  model_load_ms?: number
  cache_hit?: boolean
}

export function extractDocumentText(doc: RerankDocument): string {
  if (typeof doc === 'string') return doc
  if (!doc || typeof doc !== 'object') return String(doc ?? '')
  for (const key of ['text', 'content', 'page_content', 'body']) {
    const value = doc[key]
    if (typeof value === 'string' && value.trim().length > 0) return value
  }
  return JSON.stringify(doc)
}

function yamlScalar(value: unknown): string {
  if (value == null) return ''
  if (typeof value === 'string') return value.replace(/[\r\n]+/g, ' ').trim()
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value)
}

export function formatStructuredDocument(doc: RerankDocument): string {
  if (typeof doc === 'string') return doc
  if (!doc || typeof doc !== 'object') return String(doc ?? '')
  const text = extractDocumentText(doc)
  const lines: string[] = []
  const meta = doc.metadata && typeof doc.metadata === 'object' ? doc.metadata as Record<string, unknown> : {}
  const merged = { ...meta }
  for (const [key, value] of Object.entries(doc)) {
    if (['text', 'content', 'page_content', 'body', 'metadata'].includes(key)) continue
    if (!(key in merged)) merged[key] = value
  }
  for (const [key, value] of Object.entries(merged)) {
    if (value == null) continue
    lines.push(`${key}: ${yamlScalar(value)}`)
  }
  if (lines.length === 0) return text
  lines.push('content: |')
  for (const line of text.split(/\r?\n/)) lines.push(`  ${line}`)
  return lines.join('\n')
}

export function estimateTokens(text: string): number {
  return Math.max(1, Math.ceil(text.length / 4))
}

export function truncateByApproxTokens(text: string, maxTokens?: number): { text: string; truncated: boolean } {
  if (!maxTokens || maxTokens <= 0) return { text, truncated: false }
  const maxChars = Math.max(1, Math.floor(maxTokens * 4))
  if (text.length <= maxChars) return { text, truncated: false }
  return { text: text.slice(0, maxChars), truncated: true }
}

function hasNonAscii(text: string): boolean {
  return /[^\u0000-\u007f]/.test(text)
}

function looksLikeCode(text: string): boolean {
  return /(?:[A-Za-z]:[\\/]|\b(?:function|class|struct|enum|namespace|template|#include|import|def|async|await|return|const|let|var|public:|private:|MOV|CALL|JMP)\b|[{};]{2,}|0x[0-9a-fA-F]{4,}|\.\w{1,5}:\d+)/.test(text)
}

export function detectRerankProfile(query: string, documents: RerankDocument[]): RerankProfileName {
  const sample = `${query}\n${documents.slice(0, 8).map(extractDocumentText).join('\n')}`
  const avgLen = documents.length ? documents.reduce((n, d) => n + extractDocumentText(d).length, 0) / documents.length : 0
  if (looksLikeCode(sample)) return 'code'
  if (hasNonAscii(sample)) return 'multilingual'
  if (avgLen > 6000) return 'long'
  return 'default'
}

export function normalizeRerankRequest(req: any): NormalizedRerankRequest {
  if (!req || typeof req !== 'object') throw new Error('rerank request must be a JSON object')
  const query = typeof req.query === 'string' ? req.query.trim() : ''
  if (!query) throw new Error('rerank requires a non-empty query string')
  const docs = Array.isArray(req.documents) ? req.documents : Array.isArray(req.texts) ? req.texts : undefined
  if (!Array.isArray(docs) || docs.length === 0) throw new Error('rerank requires a non-empty documents or texts array')
  const maxTokens = Number.isFinite(Number(req.max_tokens_per_doc)) ? Math.floor(Number(req.max_tokens_per_doc)) : undefined
  const documents: string[] = []
  let truncated = 0
  for (const doc of docs) {
    const formatted = formatStructuredDocument(doc)
    const t = truncateByApproxTokens(formatted, maxTokens)
    if (t.truncated) truncated++
    documents.push(t.text)
  }
  const topNRaw = req.top_n ?? req.top_k
  const topN = Number.isFinite(Number(topNRaw)) ? Math.max(1, Math.min(documents.length, Math.floor(Number(topNRaw)))) : undefined
  const evidenceMode = req.evidence_mode === 'all' || req.evidence_mode === 'top_n' ? req.evidence_mode : 'off'
  const minScore = Number.isFinite(Number(req.min_relevance_score)) ? Number(req.min_relevance_score) : undefined
  return {
    model: typeof req.model === 'string' && req.model.trim() ? req.model.trim() : 'auto',
    query,
    documents,
    originalDocuments: docs as RerankDocument[],
    top_n: topN,
    return_documents: req.return_documents !== false,
    normalize_scores: req.normalize_scores !== false && req.normalize !== false,
    raw_scores: req.raw_scores === true,
    max_tokens_per_doc: maxTokens,
    min_relevance_score: minScore,
    evidence_mode: evidenceMode,
    profile: detectRerankProfile(query, docs as RerankDocument[]),
    truncated_documents: truncated,
  }
}

function sigmoid(x: number): number {
  return 1 / (1 + Math.exp(-x))
}

function normalizeScore(score: number, normalize: boolean): number {
  if (!normalize) return score
  if (score >= 0 && score <= 1) return score
  return sigmoid(score)
}

function queryTerms(query: string): string[] {
  return Array.from(new Set(query.toLowerCase().split(/[^\p{L}\p{N}_]+/u).filter((t) => t.length >= 3))).slice(0, 16)
}

function evidenceFor(query: string, text: string): { evidence: string; contribution: string } {
  const terms = queryTerms(query)
  const sentences = text.split(/(?<=[.!?])\s+|\r?\n+/).map((s) => s.trim()).filter(Boolean)
  let best = sentences[0] ?? text.slice(0, 300)
  let bestHits = -1
  for (const sentence of sentences.slice(0, 80)) {
    const lower = sentence.toLowerCase()
    const hits = terms.reduce((n, t) => n + (lower.includes(t) ? 1 : 0), 0)
    if (hits > bestHits) {
      best = sentence
      bestHits = hits
    }
  }
  if (best.length > 600) best = best.slice(0, 600)
  const contribution = bestHits > 0 ? `Matches ${bestHits} query term${bestHits === 1 ? '' : 's'} in the selected passage.` : 'Highest-scoring candidate from the reranker.'
  return { evidence: best, contribution }
}

export function postprocessRerankResponse(raw: any, req: NormalizedRerankRequest, meta: Partial<RerankTraceMeta> = {}) {
  const rawResults = Array.isArray(raw?.results) ? raw.results : Array.isArray(raw?.data) ? raw.data : []
  let results = rawResults.map((item: any, fallbackIndex: number) => {
    const index = Number.isFinite(Number(item.index)) ? Number(item.index) : fallbackIndex
    const rawScore = Number(item.relevance_score ?? item.score ?? item.logit ?? 0)
    const score = normalizeScore(rawScore, req.normalize_scores && !req.raw_scores)
    const out: any = { index, relevance_score: score }
    if (req.raw_scores) out.raw_relevance_score = rawScore
    if (req.return_documents) out.document = req.originalDocuments[index]
    return out
  })
  results.sort((a: any, b: any) => Number(b.relevance_score) - Number(a.relevance_score))
  if (typeof req.min_relevance_score === 'number') {
    results = results.filter((r: any) => Number(r.relevance_score) >= req.min_relevance_score!)
  }
  if (req.top_n) results = results.slice(0, req.top_n)
  if (req.evidence_mode !== 'off') {
    for (const r of results) {
      const original = req.originalDocuments[r.index]
      const text = extractDocumentText(original)
      const ev = evidenceFor(req.query, text)
      r.evidence = ev.evidence
      r.contribution = ev.contribution
    }
  }
  const trace: RerankTraceMeta = {
    model: String(meta.model ?? raw?.model ?? req.model),
    profile: req.profile,
    provider: String(meta.provider ?? 'local_gguf'),
    fallback_used: Boolean(meta.fallback_used),
    fallback_reason: meta.fallback_reason,
    candidate_count: req.documents.length,
    returned_count: results.length,
    truncated_documents: req.truncated_documents,
    normalize_scores: req.normalize_scores,
    raw_scores: req.raw_scores,
    latency_ms: meta.latency_ms,
    model_load_ms: meta.model_load_ms,
    cache_hit: meta.cache_hit,
  }
  return { object: 'list', model: trace.model, results, meta: trace, usage: raw?.usage }
}

export function scoreRerankingModel(model: any, profile: RerankProfileName, loadedIds: Set<string>, preferred?: string): number {
  const id = String(model.id ?? '')
  const name = String(model.name ?? id)
  const haystack = `${id} ${name}`.toLowerCase()
  let score = 0
  if (preferred && id === preferred) score += 1000
  if (loadedIds.has(id)) score += 80
  if (profile === 'code' && /(code|coder|qwen|starcoder|deobfusc|asm|disassembl)/.test(haystack)) score += 60
  if (profile === 'multilingual' && /(multi|jina|bge|qwen|xlm|m3)/.test(haystack)) score += 60
  if (profile === 'long' && /(large|4b|7b|8b|long|m3)/.test(haystack)) score += 30
  if (/rerank|reranker|cross/.test(haystack)) score += 50
  const size = Number(model.sizeBytes ?? 0)
  if (size > 0) score += Math.max(0, 30 - Math.log10(size))
  return score
}
