import React from 'react'

type MermaidFallbackProps = {
  children?: React.ReactNode
  chart?: string
  code?: string
  value?: string
  className?: string
}

const textFromProps = (props: MermaidFallbackProps): string => {
  if (typeof props.chart === 'string') return props.chart
  if (typeof props.code === 'string') return props.code
  if (typeof props.value === 'string') return props.value
  if (typeof props.children === 'string') return props.children
  return ''
}

export function DisabledMermaidFallback(props: MermaidFallbackProps) {
  const text = textFromProps(props)
  return (
    <pre className={props.className ?? 'whitespace-pre-wrap rounded-md border border-border/50 bg-muted/30 p-3 text-xs text-muted-foreground'}>
      <code>{text || 'Mermaid diagram rendering is disabled to avoid a React update loop.'}</code>
    </pre>
  )
}

const identityPlugin = () => (tree: unknown) => tree
const noop = () => null

export const mermaidAPI = {
  initialize: noop,
  reset: noop,
  render: async (_id: string, text: string) => ({ svg: `<pre>${String(text ?? '')}</pre>`, bindFunctions: noop }),
  parse: async () => true,
}

export const Mermaid = DisabledMermaidFallback
export const MermaidBlock = DisabledMermaidFallback
export const MermaidConfig = noop
export const MermaidDiagram = DisabledMermaidFallback
export const MermaidPlugin = identityPlugin
export const MermaidProvider = DisabledMermaidFallback
export const MermaidRenderer = DisabledMermaidFallback
export const createMermaidPlugin = identityPlugin
export const createMermaidRenderer = identityPlugin
export const mermaid = mermaidAPI
export const mermaidPlugin = identityPlugin
export const rehypeMermaid = identityPlugin
export const remarkMermaid = identityPlugin
export const useMermaid = () => ({ enabled: false })

export default DisabledMermaidFallback
