-- Experimental reservation only. It must not be used to authorize deletion:
-- Storage writes and local desktop writes are not yet frozen by this migration.
CREATE TABLE public.falconbroom_deletion_reservations (
  user_id uuid PRIMARY KEY,
  created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.falconbroom_deletion_reservations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.falconbroom_deletion_reservations FROM PUBLIC, anon, authenticated;

CREATE OR REPLACE FUNCTION public.falconbroom_guard_reserved_data()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, storage, pg_temp
AS $$
DECLARE
  before_row jsonb := CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) ELSE '{}'::jsonb END;
  after_row jsonb := CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) ELSE '{}'::jsonb END;
BEGIN
  -- The exclusive reservation lock waits for earlier writes to commit.
  PERFORM pg_advisory_xact_lock_shared(189487503, 14469988);
  IF EXISTS (
    SELECT 1 FROM public.falconbroom_deletion_reservations r
    WHERE r.user_id = auth.uid()
      OR r.user_id::text IN (
        before_row->>'owner', after_row->>'owner',
        before_row->>'user_id', after_row->>'user_id',
        before_row->>'author', after_row->>'author'
      )
      OR EXISTS (
        SELECT 1 FROM public.teams t
        WHERE t.owner = r.user_id
          AND (t.id::text IN (before_row->>'team_id', after_row->>'team_id')
            OR (TG_TABLE_NAME = 'teams'
                AND t.id::text IN (before_row->>'id', after_row->>'id')))
      )
      OR EXISTS (
        SELECT 1 FROM public.uploads u
        WHERE u.owner = r.user_id
          AND u.id::text IN (before_row->>'upload_id', after_row->>'upload_id')
      )
      OR (TG_TABLE_NAME = 'uploads' AND EXISTS (
        SELECT 1 FROM storage.objects o
        WHERE o.owner_id = r.user_id::text
          AND o.name IN (before_row->>'storage_path', after_row->>'storage_path')
      ))
  ) THEN
    RAISE EXCEPTION 'Account deletion reservation prevents this change';
  END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.falconbroom_guard_reserved_data() FROM PUBLIC, anon, authenticated;

CREATE TRIGGER falconbroom_deletion_reservation_guard
  BEFORE INSERT OR UPDATE OR DELETE ON public.teams
  FOR EACH ROW EXECUTE FUNCTION public.falconbroom_guard_reserved_data();
CREATE TRIGGER falconbroom_deletion_reservation_guard
  BEFORE INSERT OR UPDATE OR DELETE ON public.team_members
  FOR EACH ROW EXECUTE FUNCTION public.falconbroom_guard_reserved_data();
CREATE TRIGGER falconbroom_deletion_reservation_guard
  BEFORE INSERT OR UPDATE OR DELETE ON public.uploads
  FOR EACH ROW EXECUTE FUNCTION public.falconbroom_guard_reserved_data();
CREATE TRIGGER falconbroom_deletion_reservation_guard
  BEFORE INSERT OR UPDATE OR DELETE ON public.explanations
  FOR EACH ROW EXECUTE FUNCTION public.falconbroom_guard_reserved_data();

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
  IF EXISTS (SELECT 1 FROM public.falconbroom_deletion_reservations WHERE user_id = p_user_id) THEN
    RETURN 'reserved';
  END IF;
  IF public.falconbroom_account_has_shared_data(p_user_id) THEN
    RETURN 'shared_data_review_required';
  END IF;
  INSERT INTO public.falconbroom_deletion_reservations (user_id) VALUES (p_user_id);
  RETURN 'reserved';
END;
$$;

CREATE OR REPLACE FUNCTION public.falconbroom_cancel_deletion_reservation(p_user_id uuid)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  IF p_user_id IS NULL THEN RAISE EXCEPTION 'A user ID is required'; END IF;
  PERFORM pg_advisory_xact_lock(189487503, 14469988);
  DELETE FROM public.falconbroom_deletion_reservations WHERE user_id = p_user_id;
  RETURN FOUND;
END;
$$;

REVOKE ALL ON FUNCTION public.falconbroom_reserve_account_deletion(uuid) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.falconbroom_cancel_deletion_reservation(uuid) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.falconbroom_reserve_account_deletion(uuid) TO service_role;
GRANT EXECUTE ON FUNCTION public.falconbroom_cancel_deletion_reservation(uuid) TO service_role;
