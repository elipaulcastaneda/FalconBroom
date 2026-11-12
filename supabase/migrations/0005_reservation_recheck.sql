-- A reservation does not freeze Storage. Recheck on retries, without releasing
-- an existing reservation, but do not use this as authorization to delete.
CREATE OR REPLACE FUNCTION public.falconbroom_reserve_account_deletion(p_user_id uuid)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, storage, pg_temp
AS $$
BEGIN
  IF p_user_id IS NULL THEN RAISE EXCEPTION 'A user ID is required'; END IF;
  PERFORM pg_advisory_xact_lock(189487503, 14469988);
  IF NOT EXISTS (SELECT 1 FROM auth.users WHERE id = p_user_id) THEN
    RAISE EXCEPTION 'Account not found';
  END IF;
  IF public.falconbroom_account_has_shared_data(p_user_id) THEN
    RETURN 'shared_data_review_required';
  END IF;
  INSERT INTO public.falconbroom_deletion_reservations (user_id)
    VALUES (p_user_id) ON CONFLICT (user_id) DO NOTHING;
  RETURN 'reserved';
END;
$$;

REVOKE ALL ON FUNCTION public.falconbroom_reserve_account_deletion(uuid) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.falconbroom_reserve_account_deletion(uuid) TO service_role;
