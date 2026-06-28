import { create } from 'zustand'
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
