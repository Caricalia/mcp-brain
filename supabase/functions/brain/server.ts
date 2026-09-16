import { McpServer, StreamableHttpTransport } from 'mcp-lite'
import { z } from 'zod'
import type { User } from './types.ts'
import { registerProjectTools } from './tools/projects.ts'
import { registerMemoryTools } from './tools/memories.ts'
import { registerSearchTools } from './tools/search.ts'
import { MANAGER_RESOURCE_URI, registerAppTools } from './tools/app.ts'
import { MANAGER_HTML } from './ui/manager_html.ts'

export function createServer(user: User) {
  const mcp = new McpServer({
    name: 'brain',
    version: '1.0.0',
    schemaAdapter: (schema: unknown) => {
      const json = z.toJSONSchema(schema as z.ZodType, { io: 'input' }) as Record<string, unknown>
      delete json.$schema
      return json
    },
  })
  registerProjectTools(mcp, user)
  registerMemoryTools(mcp, user)
  registerSearchTools(mcp, user)
  registerAppTools(mcp, user)

  // Resource serving the memory manager MCP App UI (bundled single-file HTML).
  mcp.resource(
    MANAGER_RESOURCE_URI,
    { mimeType: 'text/html;profile=mcp-app' },
    // deno-lint-ignore require-await
    async () => ({
      contents: [
        { type: 'text' as const, uri: MANAGER_RESOURCE_URI, mimeType: 'text/html;profile=mcp-app', text: MANAGER_HTML },
      ],
    }),
  )

  return new StreamableHttpTransport().bind(mcp)
}
