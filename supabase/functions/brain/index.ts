// Setup type definitions for built-in Supabase Runtime APIs
/// <reference types="https://esm.sh/@supabase/functions-js@2.4.1/src/edge-runtime.d.ts" />

import { Hono } from 'hono'
import { createServer } from './server.ts'
import { authenticate } from './auth.ts'

const app = new Hono()
const mcpApp = new Hono()

mcpApp.get('/health', (c) => c.json({ ok: true, service: 'brain' }))

// Stateless server: no server-initiated SSE stream. The Streamable HTTP spec
// says to answer GET with 405 so clients skip the stream instead of erroring.
mcpApp.get('/mcp', (c) => c.body(null, 405, { Allow: 'POST' }))

mcpApp.all('/mcp', async (c) => {
  const result = await authenticate(c.req.header('Authorization'), c.req.header('x-api-key'))
  if ('error' in result) {
    return c.json({ error: result.error }, result.status)
  }

  console.log(`brain auth ok: credential=${result.credential} user=${result.user.id}`)

  const httpHandler = createServer(result.user)
  return await httpHandler(c.req.raw)
})

app.route('/brain', mcpApp)
Deno.serve(app.fetch)

/* To invoke locally:

  1. Run `supabase start` (see: https://supabase.com/docs/reference/cli/supabase-start)
  2. Make an HTTP request:

  curl -i --location --request POST 'http://127.0.0.1:54321/functions/v1/brain/mcp' \
    --header 'Authorization: Bearer <api-key>' \
    --header 'Content-Type: application/json' \
    --data '{"jsonrpc":"2.0","method":"tools/list","id":1}'

  Or, using the x-api-key header instead (useful for clients that reserve
  the Authorization header, e.g. Claude's custom-connector dialog):

  curl -i --location --request POST 'http://127.0.0.1:54321/functions/v1/brain/mcp' \
    --header 'x-api-key: <api-key>' \
    --header 'Content-Type: application/json' \
    --data '{"jsonrpc":"2.0","method":"tools/list","id":1}'

  3. Test the health endpoint:

  curl 'http://127.0.0.1:54321/functions/v1/brain/health'

*/
