export type DetectedModelCapabilities = {
  reasoning: boolean
  web_search: boolean
  embeddings: boolean
  reranking: boolean
}

const REASONING_RE = /(?:\br1\b|deepseek[-_. ]?r\d|qwq|qvq|reason(?:ing)?|think(?:ing)?|o[13](?:[-_.]|$)|gpt-5|skywork)/i
const WEB_SEARCH_RE = /(?:sonar|perplexity|web[-_. ]?search|search[-_. ]?enabled|online)/i
const EMBEDDING_RE = /(?:embed(?:ding)?|bge[-_. ]?m3|nomic[-_. ]?embed|mxbai[-_. ]?embed|jina[-_. ]?embed|e5[-_. ]|gte[-_. ]|sentence[-_. ]?transformer)/i
const RERANKING_RE = /(?:rerank|reranker|cross[-_. ]?encoder|bge[-_. ]?reranker|jina[-_. ]?reranker|qwen3[-_. ]?reranker|mxbai[-_. ]?rerank)/i

export function detectModelCapabilities(modelId: string): DetectedModelCapabilities {
  const id = String(modelId ?? '')
  return {
    reasoning: REASONING_RE.test(id),
    web_search: WEB_SEARCH_RE.test(id),
    embeddings: EMBEDDING_RE.test(id),
    reranking: RERANKING_RE.test(id),
  }
}

export function hasDetectedCapabilities(caps: DetectedModelCapabilities): boolean {
  return caps.reasoning || caps.web_search || caps.embeddings || caps.reranking
}

export function mergeDetectedCapabilities(model: Model): string[] {
  const base = new Set(model.capabilities ?? [])
  if ((model as Model & { _userConfiguredCapabilities?: boolean })._userConfiguredCapabilities) {
    return Array.from(base)
  }
  const detected = detectModelCapabilities(model.id)
  if (detected.reasoning) base.add('reasoning')
  if (detected.web_search) base.add('web_search')
  if (detected.embeddings) {
    base.add('embedding')
    base.add('embeddings')
  }
  if (detected.reranking) {
    base.add('rerank')
    base.add('reranking')
    base.add('embedding')
  }
  return Array.from(base)
}
