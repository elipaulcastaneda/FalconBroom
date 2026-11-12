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
    SELECT 1
    FROM public.teams t
    WHERE t.owner = p_user_id
      AND (
        EXISTS (
          SELECT 1 FROM public.team_members tm
          WHERE tm.team_id = t.id AND tm.user_id IS DISTINCT FROM p_user_id
        )
        OR EXISTS (
          SELECT 1 FROM public.uploads u
          WHERE u.team_id = t.id AND u.owner IS DISTINCT FROM p_user_id
        )
      )
  ) OR EXISTS (
    SELECT 1
    FROM public.uploads u
    JOIN public.teams t ON t.id = u.team_id
    WHERE u.owner = p_user_id
      AND (
        t.owner IS DISTINCT FROM p_user_id
        OR EXISTS (
          SELECT 1 FROM public.team_members tm
          WHERE tm.team_id = t.id AND tm.user_id IS DISTINCT FROM p_user_id
        )
      )
  ) OR EXISTS (
    SELECT 1
    FROM public.explanations e
    JOIN public.uploads u ON u.id = e.upload_id
    WHERE u.owner = p_user_id AND e.author IS DISTINCT FROM p_user_id
  );
END;
$$;

REVOKE ALL ON FUNCTION public.falconbroom_account_has_shared_data(uuid) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.falconbroom_account_has_shared_data(uuid) TO service_role;

CREATE OR REPLACE FUNCTION public.falconbroom_delete_user_data(p_user_id uuid)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, storage, pg_temp
AS $$
DECLARE
  owned_objects jsonb;
BEGIN
  IF p_user_id IS NULL THEN
    RAISE EXCEPTION 'A user ID is required';
  END IF;

  LOCK TABLE public.teams, public.team_members, public.uploads, public.explanations
    IN SHARE ROW EXCLUSIVE MODE;

  IF public.falconbroom_account_has_shared_data(p_user_id) THEN
    RAISE EXCEPTION 'Account deletion requires shared data review';
  END IF;

  SELECT COALESCE(
    jsonb_agg(jsonb_build_object('bucket_id', bucket_id, 'name', name)),
    '[]'::jsonb
  )
  INTO owned_objects
  FROM storage.objects
  WHERE owner_id = p_user_id::text;

  DELETE FROM public.explanations
  WHERE author = p_user_id
     OR upload_id IN (SELECT id FROM public.uploads WHERE owner = p_user_id);

  DELETE FROM public.uploads
  WHERE owner = p_user_id;

  DELETE FROM public.team_members
  WHERE user_id = p_user_id;

  DELETE FROM public.teams t
  WHERE t.owner = p_user_id
    AND NOT EXISTS (
      SELECT 1 FROM public.team_members tm WHERE tm.team_id = t.id
    );

  UPDATE public.teams
  SET owner = NULL
  WHERE owner = p_user_id;

  RETURN jsonb_build_object('storage_objects', owned_objects);
END;
$$;

REVOKE ALL ON FUNCTION public.falconbroom_delete_user_data(uuid) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.falconbroom_delete_user_data(uuid) TO service_role;
