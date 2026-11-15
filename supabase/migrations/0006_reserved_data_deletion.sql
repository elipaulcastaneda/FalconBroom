-- Experimental database step only. This does not isolate Storage or authorize
-- account deletion. The deployment must prevent privileged Storage writes.
CREATE OR REPLACE FUNCTION public.falconbroom_reserve_deletion_without_storage(p_user_id uuid)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, storage, pg_temp
AS $$
BEGIN
  IF p_user_id IS NULL THEN
    RAISE EXCEPTION 'A user ID is required';
  END IF;
  PERFORM pg_advisory_xact_lock(189487503, 14469988);
  IF EXISTS (SELECT 1 FROM storage.buckets)
     OR EXISTS (SELECT 1 FROM storage.objects) THEN
    RETURN 'storage_review_required';
  END IF;
  RETURN public.falconbroom_reserve_account_deletion(p_user_id);
END;
$$;

REVOKE ALL ON FUNCTION public.falconbroom_reserve_deletion_without_storage(uuid)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.falconbroom_reserve_deletion_without_storage(uuid)
  TO service_role;

CREATE OR REPLACE FUNCTION public.falconbroom_delete_reserved_user_data(p_user_id uuid)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, storage, pg_temp
AS $$
DECLARE
  reserved_at timestamptz;
  deletion_result jsonb;
BEGIN
  IF p_user_id IS NULL THEN
    RAISE EXCEPTION 'A user ID is required';
  END IF;

  -- Acquire table locks before the advisory lock to avoid waiting on a writer
  -- that holds a table lock while it waits for the reservation lock.
  LOCK TABLE public.teams, public.team_members, public.uploads, public.explanations
    IN SHARE ROW EXCLUSIVE MODE;
  PERFORM pg_advisory_xact_lock(189487503, 14469988);

  SELECT created_at INTO reserved_at
  FROM public.falconbroom_deletion_reservations
  WHERE user_id = p_user_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Account deletion reservation is required';
  END IF;

  IF EXISTS (SELECT 1 FROM storage.buckets)
     OR EXISTS (SELECT 1 FROM storage.objects) THEN
    RETURN 'storage_review_required';
  END IF;
  IF public.falconbroom_account_has_shared_data(p_user_id) THEN
    RETURN 'shared_data_review_required';
  END IF;

  -- Other transactions cannot observe a committed gap in the reservation.
  -- Removing it inside this transaction lets the existing guard protect all
  -- other reserved accounts while the trusted deletion runs.
  DELETE FROM public.falconbroom_deletion_reservations WHERE user_id = p_user_id;
  deletion_result := public.falconbroom_delete_user_data(p_user_id);
  IF deletion_result IS DISTINCT FROM '{"storage_objects":[]}'::jsonb THEN
    RAISE EXCEPTION 'Unexpected Storage objects during account deletion';
  END IF;
  INSERT INTO public.falconbroom_deletion_reservations (user_id, created_at)
    VALUES (p_user_id, reserved_at);
  RETURN 'completed';
END;
$$;

REVOKE ALL ON FUNCTION public.falconbroom_delete_reserved_user_data(uuid)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.falconbroom_delete_reserved_user_data(uuid)
  TO service_role;
