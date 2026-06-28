const noop = () => undefined

export const mermaidAPI = {
  initialize: noop,
  reset: noop,
  parse: async () => true,
  render: async (_id: string, text: string) => ({
    svg: `<pre>${String(text ?? '')}</pre>`,
    bindFunctions: noop,
  }),
}

const mermaid = {
  initialize: noop,
  init: noop,
  run: async () => undefined,
  contentLoaded: noop,
  parse: async () => true,
  render: mermaidAPI.render,
  mermaidAPI,
}

export const initialize = mermaid.initialize
export const init = mermaid.init
export const run = mermaid.run
export const contentLoaded = mermaid.contentLoaded
export const parse = mermaid.parse
export const render = mermaid.render

export default mermaid
