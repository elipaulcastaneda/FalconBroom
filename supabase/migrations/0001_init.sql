-- Supabase initial schema + RLS policies
-- Run this against your Supabase Postgres (psql or supabase db push)

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Teams table
CREATE TABLE IF NOT EXISTS public.teams (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  owner uuid REFERENCES auth.users (id) ON DELETE SET NULL,
  created_at timestamptz DEFAULT now()
);

-- Team membership
CREATE TABLE IF NOT EXISTS public.team_members (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  team_id uuid REFERENCES public.teams (id) ON DELETE CASCADE,
  user_id uuid REFERENCES auth.users (id) ON DELETE CASCADE,
  role text NOT NULL DEFAULT 'member', -- member|admin
  created_at timestamptz DEFAULT now(),
  UNIQUE (team_id, user_id)
);

-- Uploads / Projects
CREATE TABLE IF NOT EXISTS public.uploads (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  team_id uuid REFERENCES public.teams (id) ON DELETE SET NULL,
  owner uuid REFERENCES auth.users (id) ON DELETE SET NULL,
  metadata jsonb,
  storage_path text,
  status text,
  created_at timestamptz DEFAULT now()
);

-- Explanations / Comments
CREATE TABLE IF NOT EXISTS public.explanations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  upload_id uuid REFERENCES public.uploads (id) ON DELETE CASCADE,
  author uuid REFERENCES auth.users (id) ON DELETE SET NULL,
  content text,
  created_at timestamptz DEFAULT now()
);

-- Enable Row Level Security
ALTER TABLE public.teams ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.team_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.uploads ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.explanations ENABLE ROW LEVEL SECURITY;

-- Helper functions to check membership / admin status
CREATE OR REPLACE FUNCTION public.is_team_member(p_team uuid, p_user uuid) RETURNS boolean AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.team_members tm WHERE tm.team_id = p_team AND tm.user_id = p_user
  );
$$ LANGUAGE sql STABLE;

CREATE OR REPLACE FUNCTION public.is_team_admin(p_team uuid, p_user uuid) RETURNS boolean AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.team_members tm WHERE tm.team_id = p_team AND tm.user_id = p_user AND tm.role = 'admin'
  );
$$ LANGUAGE sql STABLE;

-- Policies: teams
DROP POLICY IF EXISTS teams_select_if_member_or_owner ON public.teams;
CREATE POLICY teams_select_if_member_or_owner ON public.teams FOR SELECT USING (
  owner = auth.uid() OR public.is_team_member(id, auth.uid())
);
DROP POLICY IF EXISTS teams_insert_authenticated ON public.teams;
CREATE POLICY teams_insert_authenticated ON public.teams FOR INSERT WITH CHECK (
  auth.role() = 'authenticated'
);

DROP POLICY IF EXISTS teams_update_owner_only ON public.teams;
CREATE POLICY teams_update_owner_only ON public.teams FOR UPDATE USING (owner = auth.uid()) WITH CHECK (owner = auth.uid());

DROP POLICY IF EXISTS teams_delete_owner_only ON public.teams;
CREATE POLICY teams_delete_owner_only ON public.teams FOR DELETE USING (owner = auth.uid());

-- Policies: team_members
DROP POLICY IF EXISTS team_members_select_if_related ON public.team_members;
CREATE POLICY team_members_select_if_related ON public.team_members FOR SELECT USING (
  -- visible if you are a member of the team or if you are the team owner
  public.is_team_member(team_id, auth.uid()) OR (
    (SELECT owner FROM public.teams WHERE id = team_members.team_id) = auth.uid()
  )
);
DROP POLICY IF EXISTS team_members_insert_by_self_or_owner ON public.team_members;
CREATE POLICY team_members_insert_by_self_or_owner ON public.team_members FOR INSERT WITH CHECK (
  -- allow a user to add themself, or allow the team owner to add members
  user_id = auth.uid() OR (
    (SELECT owner FROM public.teams WHERE id = team_members.team_id) = auth.uid()
  )
);

DROP POLICY IF EXISTS team_members_update_by_owner ON public.team_members;
CREATE POLICY team_members_update_by_owner ON public.team_members FOR UPDATE USING (
  (SELECT owner FROM public.teams WHERE id = team_members.team_id) = auth.uid()
) WITH CHECK (
  (SELECT owner FROM public.teams WHERE id = team_members.team_id) = auth.uid()
);

DROP POLICY IF EXISTS team_members_delete_by_owner ON public.team_members;
CREATE POLICY team_members_delete_by_owner ON public.team_members FOR DELETE USING (
  (SELECT owner FROM public.teams WHERE id = team_members.team_id) = auth.uid()
);

-- Policies: uploads
DROP POLICY IF EXISTS uploads_select_owner_or_team ON public.uploads;
CREATE POLICY uploads_select_owner_or_team ON public.uploads FOR SELECT USING (
  owner = auth.uid() OR (team_id IS NOT NULL AND public.is_team_member(team_id, auth.uid()))
);
DROP POLICY IF EXISTS uploads_insert_owner_or_team ON public.uploads;
CREATE POLICY uploads_insert_owner_or_team ON public.uploads FOR INSERT WITH CHECK (
  owner = auth.uid() OR (team_id IS NOT NULL AND public.is_team_member(team_id, auth.uid()))
);

DROP POLICY IF EXISTS uploads_update_owner_or_admin ON public.uploads;
CREATE POLICY uploads_update_owner_or_admin ON public.uploads FOR UPDATE USING (
  owner = auth.uid() OR (team_id IS NOT NULL AND public.is_team_admin(team_id, auth.uid()))
) WITH CHECK (
  owner = auth.uid() OR (team_id IS NOT NULL AND public.is_team_admin(team_id, auth.uid()))
);

DROP POLICY IF EXISTS uploads_delete_owner_or_admin ON public.uploads;
CREATE POLICY uploads_delete_owner_or_admin ON public.uploads FOR DELETE USING (
  owner = auth.uid() OR (team_id IS NOT NULL AND public.is_team_admin(team_id, auth.uid()))
);

-- Policies: explanations
DROP POLICY IF EXISTS explanations_select_if_upload_accessible ON public.explanations;
CREATE POLICY explanations_select_if_upload_accessible ON public.explanations FOR SELECT USING (
  EXISTS (SELECT 1 FROM public.uploads u WHERE u.id = upload_id AND (u.owner = auth.uid() OR (u.team_id IS NOT NULL AND public.is_team_member(u.team_id, auth.uid()))))
);

DROP POLICY IF EXISTS explanations_insert_if_member ON public.explanations;
CREATE POLICY explanations_insert_if_member ON public.explanations FOR INSERT WITH CHECK (
  author = auth.uid() AND EXISTS (SELECT 1 FROM public.uploads u WHERE u.id = explanations.upload_id AND (u.owner = auth.uid() OR (u.team_id IS NOT NULL AND public.is_team_member(u.team_id, auth.uid()))))
);

DROP POLICY IF EXISTS explanations_update_author_or_admin ON public.explanations;
CREATE POLICY explanations_update_author_or_admin ON public.explanations FOR UPDATE USING (
  author = auth.uid() OR (EXISTS (SELECT 1 FROM public.uploads u WHERE u.id = explanations.upload_id AND u.team_id IS NOT NULL AND public.is_team_admin(u.team_id, auth.uid())))
) WITH CHECK (
  author = auth.uid() OR (EXISTS (SELECT 1 FROM public.uploads u WHERE u.id = explanations.upload_id AND u.team_id IS NOT NULL AND public.is_team_admin(u.team_id, auth.uid())))
);

DROP POLICY IF EXISTS explanations_delete_author_or_admin ON public.explanations;
CREATE POLICY explanations_delete_author_or_admin ON public.explanations FOR DELETE USING (
  author = auth.uid() OR (EXISTS (SELECT 1 FROM public.uploads u WHERE u.id = explanations.upload_id AND u.team_id IS NOT NULL AND public.is_team_admin(u.team_id, auth.uid())))
);

-- End of migration
