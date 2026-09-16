import { createClient } from '@supabase/supabase-js'

// Service-role client: injected by the Supabase Edge Functions runtime.
// All permission/visibility logic lives in SQL functions called via rpc(),
// so it is safe to use the service role here.
export const supabase = createClient(
  Deno.env.get('SUPABASE_URL')!,
  Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!,
  { auth: { persistSession: false } },
)
