// One-off backfill: generates embeddings for memories saved while
// OPENAI_API_KEY was not configured (embedding is null).
//
// Run once, manually, after enabling the key. Get local env values with:
//   supabase status -o env
//
// Run command:
//   deno run --allow-net --allow-env scripts/backfill-embeddings.ts
//
// Required env vars: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, OPENAI_API_KEY

import { createClient } from 'npm:@supabase/supabase-js@2'

const SUPABASE_URL = Deno.env.get('SUPABASE_URL')
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')
const OPENAI_API_KEY = Deno.env.get('OPENAI_API_KEY')

if (!SUPABASE_URL || !SUPABASE_SERVICE_ROLE_KEY || !OPENAI_API_KEY) {
  console.error(
    'Missing required env vars. Please set SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY and OPENAI_API_KEY.',
  )
  Deno.exit(1)
}

const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, {
  auth: { persistSession: false },
})

const BATCH_SIZE = 50

interface MemoryRow {
  id: string
  content: string
}

async function embedBatch(texts: string[]): Promise<number[][]> {
  const response = await fetch('https://api.openai.com/v1/embeddings', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${OPENAI_API_KEY}`,
    },
    body: JSON.stringify({
      model: 'text-embedding-3-small',
      input: texts.map((t) => t.slice(0, 8000)),
    }),
  })

  if (!response.ok) {
    const body = await response.text().catch(() => '')
    throw new Error(`OpenAI embeddings request failed (${response.status}): ${body}`)
  }

  const json = await response.json()
  const embeddings = json?.data
  if (!Array.isArray(embeddings)) {
    throw new Error('OpenAI embeddings response did not contain a data array')
  }
  return embeddings.map((d: { embedding: number[] }) => d.embedding)
}

async function main() {
  let totalProcessed = 0

  while (true) {
    const { data, error } = await supabase
      .from('memories')
      .select('id, content')
      .is('embedding', null)
      .limit(BATCH_SIZE)

    if (error) {
      console.error('Failed to fetch memories:', error.message)
      Deno.exit(1)
    }

    const rows = (data ?? []) as MemoryRow[]
    if (rows.length === 0) break

    const embeddings = await embedBatch(rows.map((r) => r.content))

    for (let i = 0; i < rows.length; i++) {
      const { error: updateError } = await supabase
        .from('memories')
        .update({ embedding: embeddings[i] })
        .eq('id', rows[i].id)
      if (updateError) {
        console.error(`Failed to update memory ${rows[i].id}:`, updateError.message)
        Deno.exit(1)
      }
    }

    totalProcessed += rows.length
    console.log(`Backfilled ${totalProcessed} memories so far...`)

    if (rows.length < BATCH_SIZE) break
  }

  console.log(`Done. Backfilled ${totalProcessed} memories.`)
}

await main()
