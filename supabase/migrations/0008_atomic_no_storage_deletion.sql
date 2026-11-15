-- Experimental only: not executable by the Edge Function or API roles.
-- This transaction requires a project that has never used Storage.
CREATE OR REPLACE FUNCTION public.falconbroom_atomic_no_storage_deletion(
  p_job_id uuid, p_lease_id uuid)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, storage, auth, pg_temp
SET row_security = off
AS $$
DECLARE
  job public.falconbroom_deletion_jobs%ROWTYPE;
  outcome text;
BEGIN
  IF current_setting('transaction_isolation') <> 'read committed' THEN
    RAISE EXCEPTION 'Account deletion requires read committed isolation';
  END IF;

  SELECT * INTO job FROM public.falconbroom_deletion_jobs
  WHERE id = p_job_id AND status = 'processing' AND lease_id = p_lease_id
    AND lease_until > clock_timestamp()
  FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Deletion job lease is invalid'; END IF;

  LOCK TABLE storage.buckets, storage.objects IN SHARE ROW EXCLUSIVE MODE;
  IF row_security_active('storage.buckets'::regclass)
     OR row_security_active('storage.objects'::regclass) THEN
    RAISE EXCEPTION 'Storage inventory must not be filtered';
  END IF;
  IF job.lease_until <= clock_timestamp() THEN
    RAISE EXCEPTION 'Deletion job lease is invalid';
  END IF;

  outcome := public.falconbroom_delete_reserved_user_data(job.user_id);
  IF outcome IN ('storage_review_required', 'shared_data_review_required') THEN
    UPDATE public.falconbroom_deletion_jobs
    SET status = 'needs_review', last_error = outcome,
        lease_id = NULL, lease_until = NULL, updated_at = clock_timestamp()
    WHERE id = job.id;
    RETURN outcome;
  END IF;
  IF outcome <> 'completed' THEN RAISE EXCEPTION 'Unexpected deletion outcome'; END IF;
  IF job.lease_until <= clock_timestamp() THEN
    RAISE EXCEPTION 'Deletion job lease is invalid';
  END IF;

  DELETE FROM auth.users WHERE id = job.user_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'Auth account not found'; END IF;
  UPDATE public.falconbroom_deletion_jobs
  SET status = 'completed', database_deleted = true, last_error = NULL,
      lease_id = NULL, lease_until = NULL, updated_at = clock_timestamp()
  WHERE id = job.id;
  RETURN 'completed';
END;
$$;

REVOKE ALL ON FUNCTION public.falconbroom_atomic_no_storage_deletion(uuid, uuid)
  FROM PUBLIC, anon, authenticated, service_role;
