-- Storage paths do not include a bucket identifier in the application schema.
-- Treat ambiguous ownership or references as requiring review rather than
-- risking the removal of someone else's content.
CREATE OR REPLACE FUNCTION public.falconbroom_account_has_shared_data(p_user_id uuid)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, storage, pg_temp
AS $$
BEGIN
  IF p_user_id IS NULL THEN
    RAISE EXCEPTION 'A user ID is required';
  END IF;

  RETURN EXISTS (
    SELECT 1 FROM public.teams t
    WHERE t.owner = p_user_id AND (
      EXISTS (SELECT 1 FROM public.team_members tm
              WHERE tm.team_id = t.id AND tm.user_id IS DISTINCT FROM p_user_id)
      OR EXISTS (SELECT 1 FROM public.uploads u
                 WHERE u.team_id = t.id AND u.owner IS DISTINCT FROM p_user_id)
    )
  ) OR EXISTS (
    SELECT 1 FROM public.uploads u
    JOIN public.teams t ON t.id = u.team_id
    WHERE u.owner = p_user_id AND (
      t.owner IS DISTINCT FROM p_user_id
      OR EXISTS (SELECT 1 FROM public.team_members tm
                 WHERE tm.team_id = t.id AND tm.user_id IS DISTINCT FROM p_user_id)
    )
  ) OR EXISTS (
    SELECT 1 FROM public.explanations e
    JOIN public.uploads u ON u.id = e.upload_id
    WHERE u.owner = p_user_id AND e.author IS DISTINCT FROM p_user_id
  ) OR EXISTS (
    SELECT 1 FROM public.uploads u
    WHERE u.owner = p_user_id AND u.storage_path IS NOT NULL
      AND (
        NOT EXISTS (SELECT 1 FROM storage.objects o
                    WHERE o.name = u.storage_path AND o.owner_id = p_user_id::text)
        OR EXISTS (SELECT 1 FROM storage.objects o
                   WHERE o.name = u.storage_path AND o.owner_id IS DISTINCT FROM p_user_id::text)
      )
  ) OR EXISTS (
    SELECT 1 FROM storage.objects o
    JOIN public.uploads u ON u.storage_path = o.name
    WHERE o.owner_id = p_user_id::text AND u.owner IS DISTINCT FROM p_user_id
  );
END;
$$;

REVOKE ALL ON FUNCTION public.falconbroom_account_has_shared_data(uuid) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.falconbroom_account_has_shared_data(uuid) TO service_role;
