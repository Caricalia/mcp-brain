// Reads the vite-bundled single-file HTML and writes it out as a TS string
// constant so the brain edge function can serve it without file IO
// (Supabase Edge Functions bundle only what's imported at build time).
import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const here = path.dirname(fileURLToPath(import.meta.url))
const distHtml = path.join(here, '..', 'dist', 'manager.html')
const outFile = path.join(here, '..', '..', 'supabase', 'functions', 'brain', 'ui', 'manager_html.ts')

const html = readFileSync(distHtml, 'utf-8')
const escaped = html
  .replace(/\\/g, '\\\\')
  .replace(/`/g, '\\`')
  .replace(/\$\{/g, '\\${')

const out = `// GENERATED FILE - do not edit by hand.
// Produced by \`npm run build:app\` from app/manager.html via vite (see app/vite.config.ts
// and app/scripts/emit-ts.mjs). Bundled as a single self-contained HTML file (no external
// fetches) so brain can serve it as an MCP App resource without file IO.
export const MANAGER_HTML = \`${escaped}\`
`

writeFileSync(outFile, out)
console.log(`Wrote ${outFile} (${html.length} bytes of HTML)`)
