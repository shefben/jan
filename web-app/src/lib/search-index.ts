import { getServiceHub } from '@/hooks/useServiceHub'
import { TEMPORARY_CHAT_ID } from '@/constants/chat'
import { ContentType, type ThreadContent } from '@janhq/core'

export interface ThreadSearchResult {
  thread: Thread
  matchSource: 'title' | 'content' | 'both'
  snippet?: string
}

interface CorpusEntry {
  thread: Thread
  contentText: string
}

const MAX_CONTENT_CHARS = 5000
const MAX_INDEXED_THREADS = 2000

export function extractTextFromContent(content: ThreadContent[] | undefined): string {
  if (!content) return ''
  const parts: string[] = []
  for (const c of content) {
    if (c.type === ContentType.Text && c.text?.value) {
      const clean = c.text.value.replace(/<(think|thinking|reasoning|analysis)[^>]*>[\s\S]*?<\/\1>/gi, '').trim()
      if (clean) parts.push(clean)
    }
  }
  return parts.join(' ')
}

export function extractSnippet(text: string, term: string): string | undefined {
  const idx = text.toLowerCase().indexOf(term.toLowerCase())
  if (idx === -1) return undefined
  const margin = 55
  const start = Math.max(0, idx - margin)
  const end = Math.min(text.length, idx + term.length + margin)
  let snippet = text.slice(start, end)
  if (start > 0) snippet = '…' + snippet
  if (end < text.length) snippet = snippet + '…'
  return snippet
}

async function buildEntryForThread(thread: Thread): Promise<CorpusEntry> {
  const messages = await getServiceHub().messages().fetchMessages(thread.id)
  const contentText = messages.map((m) => extractTextFromContent(m.content)).join(' ').slice(0, MAX_CONTENT_CHARS)
  return { thread, contentText }
}

class ThreadSearchIndex {
  private entriesByThreadId: Map<string, CorpusEntry> | null = null
  private staleThreadIds = new Set<string>()
  private deletedThreadIds = new Set<string>()
  private buildPromise: Promise<void> | null = null
  private latestThreads: Record<string, Thread> = {}

  private eligibleThreads(threads: Record<string, Thread>): Thread[] {
    const list = Object.values(threads).filter((t) => t.id !== TEMPORARY_CHAT_ID && t.title)
    if (list.length <= MAX_INDEXED_THREADS) return list
    return [...list].sort((a, b) => (b.updated ?? 0) - (a.updated ?? 0)).slice(0, MAX_INDEXED_THREADS)
  }

  async build(threads: Record<string, Thread>): Promise<void> {
    this.latestThreads = threads
    if (this.buildPromise) return this.buildPromise
    this.buildPromise = (async () => {
      try {
        do { await this.doBuild(this.latestThreads) } while (this.hasPendingWork(this.latestThreads))
      } finally { this.buildPromise = null }
    })()
    return this.buildPromise
  }

  private async doBuild(threads: Record<string, Thread>): Promise<void> {
    const isFirstBuild = this.entriesByThreadId === null
    if (!this.entriesByThreadId) this.entriesByThreadId = new Map()
    for (const id of this.deletedThreadIds) this.entriesByThreadId.delete(id)
    this.deletedThreadIds.clear()
    const threadList = this.eligibleThreads(threads)
    const toFetch: Thread[] = []
    for (const thread of threadList) {
      if (!this.entriesByThreadId.has(thread.id) || this.staleThreadIds.has(thread.id)) toFetch.push(thread)
    }
    this.staleThreadIds.clear()
    const liveIds = new Set(threadList.map((t) => t.id))
    for (const id of this.entriesByThreadId.keys()) if (!liveIds.has(id)) this.entriesByThreadId.delete(id)
    if (toFetch.length === 0 && !isFirstBuild) return
    for (let i = 0; i < toFetch.length; i += 10) {
      const results = await Promise.allSettled(toFetch.slice(i, i + 10).map(buildEntryForThread))
      for (const r of results) if (r.status === 'fulfilled') this.entriesByThreadId.set(r.value.thread.id, r.value)
    }
  }

  search(term: string): ThreadSearchResult[] {
    if (!term || !this.entriesByThreadId) return []
    const lowerTerm = term.toLowerCase()
    const results: ThreadSearchResult[] = []
    for (const entry of this.entriesByThreadId.values()) {
      const titleMatch = entry.thread.title?.toLowerCase().includes(lowerTerm)
      const contentMatch = entry.contentText.toLowerCase().includes(lowerTerm)
      if (!titleMatch && !contentMatch) continue
      results.push({ thread: entry.thread, matchSource: titleMatch && contentMatch ? 'both' : titleMatch ? 'title' : 'content', snippet: contentMatch ? extractSnippet(entry.contentText, term) : undefined })
    }
    results.sort((a, b) => {
      if (a.matchSource === 'title' && b.matchSource !== 'title') return -1
      if (a.matchSource !== 'title' && b.matchSource === 'title') return 1
      return (b.thread.updated ?? 0) - (a.thread.updated ?? 0)
    })
    return results
  }

  invalidateThread(threadId: string): void { this.staleThreadIds.add(threadId) }
  removeThread(threadId: string): void { this.deletedThreadIds.add(threadId); this.staleThreadIds.delete(threadId) }
  invalidate(): void { this.entriesByThreadId = null; this.staleThreadIds.clear(); this.deletedThreadIds.clear() }
  get isReady(): boolean { return this.entriesByThreadId !== null }
  hasPendingWork(threads: Record<string, Thread>): boolean {
    if (this.entriesByThreadId === null || this.staleThreadIds.size > 0 || this.deletedThreadIds.size > 0) return true
    const eligible = this.eligibleThreads(threads)
    for (const t of eligible) if (!this.entriesByThreadId.has(t.id)) return true
    return this.entriesByThreadId.size !== eligible.length
  }
}

let instance: ThreadSearchIndex | null = null
export function getThreadSearchIndex(): ThreadSearchIndex { if (!instance) instance = new ThreadSearchIndex(); return instance }
export function __resetThreadSearchIndexForTests(): void { instance = null }
