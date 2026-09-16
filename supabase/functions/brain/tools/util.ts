// Small helpers shared by tool implementations.

export function ok(result: unknown) {
  return {
    content: [{ type: 'text' as const, text: JSON.stringify(result) }],
  }
}

export function fail(message: string) {
  return {
    content: [{ type: 'text' as const, text: JSON.stringify({ error: message }) }],
    isError: true,
  }
}

// Use for unexpected/internal errors (caught exceptions, third-party SDK
// failures such as the OpenAI client). Logs the real detail server-side
// (visible in `supabase functions logs brain`) and returns a generic message
// to the MCP client - never `e.message`, which can carry sensitive detail
// (e.g. a redacted API key or org id in an OpenAI error body).
export function failInternal(genericMessage: string, e: unknown, context: string) {
  const detail = e instanceof Error ? e.message : String(e)
  console.log(`${context}: ${detail}`)
  return fail(genericMessage)
}

export const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
