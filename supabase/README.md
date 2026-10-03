# Supabase schema and migrations for FalconBroom

This folder contains SQL to provision core tables and Row-Level Security (RLS) policies for Supabase/Postgres.

Files
- `migrations/0001_init.sql` — creates `teams`, `team_members`, `uploads`, `explanations`, helper functions, and RLS policies.

How to apply

- Using the Supabase CLI (recommended):

  1. Install and login: `supabase login`
  2. Push the SQL (or run it manually):
     - `supabase db remote set <DB_URL>` (set your DB connection)
     - `psql <DB_URL> -f supabase/migrations/0001_init.sql`

- Or using `psql` directly:

  ```bash
  psql "${SUPABASE_DB_URL}" -f supabase/migrations/0001_init.sql
  ```

Notes & next steps
- The migration assumes Supabase Auth is in use (references `auth.users`). Policies use `auth.uid()` and `auth.role()`.
- Review policies carefully and adapt the role semantics to your product (e.g. add `owner`, `admin`, `member` roles).
- For production, consider adding audit tables and more restrictive policies (and testing with a non-privileged `anon` role).
