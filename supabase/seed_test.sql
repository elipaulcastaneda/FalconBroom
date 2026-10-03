-- Seed data for testing RLS and basic relationships
-- Run this with elevated privileges (Supabase SQL editor or using the service_role key)
-- Replace the placeholder user UUIDs with real `auth.users` ids created via sign-up.

-- Placeholders to replace before running:
--  <USER_A_UUID>  : primary test user (will be team owner)
--  <USER_B_UUID>  : secondary user (team member)
--  <USER_C_UUID>  : non-member user (optional)
--  <TEAM_UUID>    : optional stable team id (or omit to let gen_random_uuid generate)
-- Example: run in psql with -v USER_A_UUID='...' -v USER_B_UUID='...'

BEGIN;

-- Create a team (id optional)
INSERT INTO public.teams (id, name, owner)
VALUES (
  COALESCE(NULLIF('<TEAM_UUID>','')::uuid, gen_random_uuid()),
  'Seed Test Team',
  '<USER_A_UUID>'::uuid
)
ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, owner = EXCLUDED.owner
RETURNING id INTO TEMP TABLE _seed_team_id;

-- If the DB doesn't support RETURNING INTO TEMP TABLE in your client, you can
-- read the created team id with a SELECT afterwards. For portability, also
-- compute a team id variable here:
-- (Use the explicit placeholder if you provided one)

-- Add team_members: owner as admin, second user as member
INSERT INTO public.team_members (id, team_id, user_id, role)
VALUES
  (gen_random_uuid(), (SELECT id FROM public.teams WHERE name = 'Seed Test Team' LIMIT 1), '<USER_A_UUID>'::uuid, 'admin')
ON CONFLICT (team_id, user_id) DO NOTHING;

INSERT INTO public.team_members (id, team_id, user_id, role)
VALUES
  (gen_random_uuid(), (SELECT id FROM public.teams WHERE name = 'Seed Test Team' LIMIT 1), '<USER_B_UUID>'::uuid, 'member')
ON CONFLICT (team_id, user_id) DO NOTHING;

-- Create an upload associated with the team
INSERT INTO public.uploads (id, team_id, owner, metadata, storage_path, status)
VALUES (
  gen_random_uuid(),
  (SELECT id FROM public.teams WHERE name = 'Seed Test Team' LIMIT 1),
  '<USER_A_UUID>'::uuid,
  jsonb_build_object('description','Seed CSV for RLS tests'),
  '/seed/test.csv',
  'ready'
)
ON CONFLICT (id) DO NOTHING
RETURNING id INTO TEMP TABLE _seed_upload_id;

-- Add an explanation/comment authored by USER_B_UUID
INSERT INTO public.explanations (id, upload_id, author, content)
VALUES (
  gen_random_uuid(),
  (SELECT id FROM public.uploads WHERE storage_path = '/seed/test.csv' LIMIT 1),
  '<USER_B_UUID>'::uuid,
  'This is a test explanation created by the seeded member.'
)
ON CONFLICT (id) DO NOTHING;

-- Optional: create a standalone upload owned by USER_C (non-member)
-- INSERT INTO public.uploads (id, team_id, owner, metadata, storage_path, status)
-- VALUES (gen_random_uuid(), NULL, '<USER_C_UUID>'::uuid, jsonb_build_object('note','non-team upload'), '/seed/other.csv', 'ready')
-- ON CONFLICT (id) DO NOTHING;

COMMIT;

-- Quick selects to verify inserted rows (run interactively)
-- SELECT * FROM public.teams WHERE name = 'Seed Test Team';
-- SELECT * FROM public.team_members WHERE team_id = (SELECT id FROM public.teams WHERE name = 'Seed Test Team');
-- SELECT * FROM public.uploads WHERE storage_path = '/seed/test.csv';
-- SELECT * FROM public.explanations WHERE upload_id = (SELECT id FROM public.uploads WHERE storage_path = '/seed/test.csv');
