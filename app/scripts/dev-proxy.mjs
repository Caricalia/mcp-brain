// Tiny local proxy for manual/browser-driven testing of the memory manager
// MCP App: basic-host's SERVERS env var can only point at a URL, it cannot
// add custom headers, so this injects the Authorization header brain
// requires before forwarding the request.
//
// Usage:
//   BRAIN_TOKEN=brain_xxx node app/scripts/dev-proxy.mjs
//   SERVERS='["http://localhost:3001/mcp"]' npm start   # in basic-host
//
// The proxy is intentionally loopback-only by default. It injects the token
// into every upstream request, so do not expose it on a LAN or point BRAIN_URL
// at a service you do not control.

import http from 'node:http'

const PORT = process.env.PROXY_PORT ? Number(process.env.PROXY_PORT) : 3001
const HOST = process.env.PROXY_HOST ?? '127.0.0.1'
const TARGET = process.env.BRAIN_URL ?? 'http://127.0.0.1:54321/functions/v1/brain/mcp'
const TOKEN = process.env.BRAIN_TOKEN
const LOCAL_ORIGINS = new Set(['localhost', '127.0.0.1', '[::1]'])
if (!TOKEN) {
  console.error('Set BRAIN_TOKEN to the app-test@local token before starting the proxy.')
  process.exit(1)
}

function allowedOrigin(origin) {
  if (!origin) return undefined
  try {
    const url = new URL(origin)
    return url.protocol === 'http:' && LOCAL_ORIGINS.has(url.hostname) ? origin : undefined
  } catch {
    return undefined
  }
}

function corsHeaders(req) {
  const origin = allowedOrigin(req.headers.origin)
  return origin
    ? {
        'access-control-allow-origin': origin,
        vary: 'Origin',
        'access-control-allow-methods': 'GET, POST, OPTIONS',
        'access-control-allow-headers': 'content-type, accept, mcp-session-id, mcp-protocol-version',
        'access-control-expose-headers': 'mcp-session-id, mcp-protocol-version',
      }
    : {}
}

const server = http.createServer(async (req, res) => {
  if (req.method === 'OPTIONS') {
    res.writeHead(204, corsHeaders(req))
    res.end()
    return
  }

  const chunks = []
  for await (const chunk of req) chunks.push(chunk)
  const body = Buffer.concat(chunks)

  try {
    const outHeaders = {
      'content-type': req.headers['content-type'] ?? 'application/json',
      accept: req.headers['accept'] ?? 'application/json, text/event-stream',
      authorization: `Bearer ${TOKEN}`,
    }
    if (req.headers['mcp-session-id']) outHeaders['mcp-session-id'] = req.headers['mcp-session-id']
    if (req.headers['mcp-protocol-version']) outHeaders['mcp-protocol-version'] = req.headers['mcp-protocol-version']

    const upstream = await fetch(TARGET, {
      method: req.method,
      headers: outHeaders,
      body: req.method === 'GET' || req.method === 'HEAD' ? undefined : body,
    })

    const headers = corsHeaders(req)
    for (const key of ['content-type', 'mcp-session-id', 'mcp-protocol-version']) {
      const value = upstream.headers.get(key)
      if (value) headers[key] = value
    }
    res.writeHead(upstream.status, headers)
    const text = await upstream.text()
    res.end(text)
  } catch (e) {
    res.writeHead(502, { 'content-type': 'application/json', ...corsHeaders(req) })
    res.end(JSON.stringify({ error: String(e) }))
  }
})

server.listen(PORT, HOST, () => {
  console.log(`Dev proxy listening on http://${HOST}:${PORT}/mcp -> ${TARGET}`)
})
