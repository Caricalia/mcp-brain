// Optional semantic search support. Everything in this server must keep
// working without OPENAI_API_KEY; when it's absent, embed() is a no-op.

export const embeddingsEnabled = !!Deno.env.get('OPENAI_API_KEY')

export async function embed(text: string): Promise<number[] | null> {
  if (!embeddingsEnabled) return null

  const apiKey = Deno.env.get('OPENAI_API_KEY')!
  const response = await fetch('https://api.openai.com/v1/embeddings', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify({
      model: 'text-embedding-3-small',
      input: text.slice(0, 8000),
    }),
  })

  if (!response.ok) {
    const body = await response.text().catch(() => '')
    throw new Error(`OpenAI embeddings request failed (${response.status}): ${body}`)
  }

  const json = await response.json()
  const embedding = json?.data?.[0]?.embedding
  if (!Array.isArray(embedding)) {
    throw new Error('OpenAI embeddings response did not contain an embedding array')
  }

  return embedding as number[]
}
