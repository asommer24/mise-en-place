import { createClient } from "@supabase/supabase-js";

// Frontend uses the ANON/public key only. RLS (see scripts/schema.sql) limits it
// to: read recipes, read plans, and update the current/future week's plan.
// The service_role key must NEVER ship in the client bundle.
export const supabase = createClient(
  import.meta.env.VITE_SUPABASE_URL,
  import.meta.env.VITE_SUPABASE_ANON_KEY
);
