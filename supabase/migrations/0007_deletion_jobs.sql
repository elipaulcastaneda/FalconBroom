-- Experimental queue. Not wired to the desktop or a scheduler. A no-Storage
-- deployment must independently prevent bucket/object writes for the whole job.
CREATE TABLE public.falconbroom_deletion_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL UNIQUE,
  recovery_hash text NOT NULL CHECK (recovery_hash ~ '^[0-9a-f]{64}$'),
  status text NOT NULL CHECK (status IN
    ('awaiting_local', 'ready', 'processing', 'needs_review', 'completed')),
  database_deleted boolean NOT NULL DEFAULT false,
  lease_id uuid,
  lease_until timestamptz,
  attempts integer NOT NULL DEFAULT 0,
  retry_after timestamptz NOT NULL DEFAULT now(),
  last_error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.falconbroom_deletion_jobs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.falconbroom_deletion_jobs FROM PUBLIC, anon, authenticated;

CREATE OR REPLACE FUNCTION public.falconbroom_create_deletion_job(p_user_id uuid, p_recovery_hash text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, storage, pg_temp AS $$
DECLARE
  job public.falconbroom_deletion_jobs%ROWTYPE;
  outcome text;
BEGIN
  IF p_user_id IS NULL THEN RAISE EXCEPTION 'A user ID is required'; END IF;
  IF p_recovery_hash IS NULL OR p_recovery_hash !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'A recovery hash is required';
  END IF;
  PERFORM pg_advisory_xact_lock(189487503, 14469988);
  SELECT * INTO job FROM public.falconbroom_deletion_jobs WHERE user_id = p_user_id;
  IF FOUND THEN
    IF job.recovery_hash <> p_recovery_hash THEN
      RETURN jsonb_build_object('status', 'recovery_review_required');
    END IF;
    IF job.status = 'awaiting_local' THEN
      outcome := public.falconbroom_reserve_deletion_without_storage(p_user_id);
      IF outcome <> 'reserved' THEN
        RETURN jsonb_build_object('status', outcome);
      END IF;
    END IF;
    RETURN jsonb_build_object('status', job.status, 'job_id', job.id);
  END IF;
  outcome := public.falconbroom_reserve_deletion_without_storage(p_user_id);
  IF outcome <> 'reserved' THEN
    RETURN jsonb_build_object('status', outcome);
  END IF;
  INSERT INTO public.falconbroom_deletion_jobs (user_id, status, recovery_hash)
    VALUES (p_user_id, 'awaiting_local', p_recovery_hash) RETURNING * INTO job;
  RETURN jsonb_build_object('status', job.status, 'job_id', job.id);
END $$;

CREATE OR REPLACE FUNCTION public.falconbroom_deletion_job_status(p_job_id uuid, p_recovery_hash text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE job public.falconbroom_deletion_jobs%ROWTYPE;
BEGIN
  IF p_recovery_hash IS NULL OR p_recovery_hash !~ '^[0-9a-f]{64}$' THEN
    RETURN NULL;
  END IF;
  SELECT * INTO job FROM public.falconbroom_deletion_jobs
    WHERE id = p_job_id AND recovery_hash = p_recovery_hash;
  IF NOT FOUND THEN RETURN NULL; END IF;
  RETURN jsonb_build_object('job_id', job.id, 'status', job.status);
END $$;

CREATE OR REPLACE FUNCTION public.falconbroom_deletion_local_complete(
  p_user_id uuid, p_job_id uuid, p_recovery_hash text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  UPDATE public.falconbroom_deletion_jobs
  SET status = 'ready', updated_at = now()
  WHERE id = p_job_id AND user_id = p_user_id AND recovery_hash = p_recovery_hash
    AND status = 'awaiting_local';
  IF FOUND THEN RETURN true; END IF;
  RETURN EXISTS (SELECT 1 FROM public.falconbroom_deletion_jobs
                 WHERE id = p_job_id AND user_id = p_user_id AND recovery_hash = p_recovery_hash
                   AND status IN ('ready', 'processing', 'needs_review', 'completed'));
END $$;

CREATE OR REPLACE FUNCTION public.falconbroom_claim_deletion_job()
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE job public.falconbroom_deletion_jobs%ROWTYPE;
BEGIN
  SELECT * INTO job FROM public.falconbroom_deletion_jobs
  WHERE (status = 'ready' AND retry_after <= now())
     OR (status = 'processing' AND lease_until < now())
  ORDER BY updated_at FOR UPDATE SKIP LOCKED LIMIT 1;
  IF NOT FOUND THEN RETURN NULL; END IF;
  UPDATE public.falconbroom_deletion_jobs
  SET status = 'processing', lease_id = gen_random_uuid(),
      lease_until = now() + interval '5 minutes', attempts = attempts + 1,
      updated_at = now()
  WHERE id = job.id RETURNING * INTO job;
  RETURN jsonb_build_object('job_id', job.id, 'user_id', job.user_id,
                            'lease_id', job.lease_id, 'database_deleted', job.database_deleted);
END $$;

CREATE OR REPLACE FUNCTION public.falconbroom_deletion_database_step(p_job_id uuid, p_lease_id uuid)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, storage, pg_temp AS $$
DECLARE job public.falconbroom_deletion_jobs%ROWTYPE;
DECLARE outcome text;
BEGIN
  SELECT * INTO job FROM public.falconbroom_deletion_jobs
  WHERE id = p_job_id AND status = 'processing' AND lease_id = p_lease_id
    AND lease_until > now() FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Deletion job lease is invalid'; END IF;
  outcome := public.falconbroom_delete_reserved_user_data(job.user_id);
  IF outcome = 'completed' THEN
    UPDATE public.falconbroom_deletion_jobs SET database_deleted = true, updated_at = now()
      WHERE id = job.id;
  ELSIF outcome IN ('storage_review_required', 'shared_data_review_required') THEN
    UPDATE public.falconbroom_deletion_jobs SET status = 'needs_review',
      last_error = outcome, lease_id = NULL, lease_until = NULL, updated_at = now()
      WHERE id = job.id;
  ELSE
    RAISE EXCEPTION 'Unexpected database deletion outcome';
  END IF;
  RETURN outcome;
END $$;

-- A recheck can detect changes but does not freeze privileged Storage writes.
CREATE OR REPLACE FUNCTION public.falconbroom_deletion_before_auth(p_job_id uuid, p_lease_id uuid)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, storage, pg_temp AS $$
DECLARE job public.falconbroom_deletion_jobs%ROWTYPE;
DECLARE outcome text;
BEGIN
  SELECT * INTO job FROM public.falconbroom_deletion_jobs
  WHERE id = p_job_id AND status = 'processing' AND lease_id = p_lease_id
    AND lease_until > now() AND database_deleted FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Deletion job lease is invalid'; END IF;
  PERFORM pg_advisory_xact_lock(189487503, 14469988);
  IF NOT EXISTS (SELECT 1 FROM public.falconbroom_deletion_reservations WHERE user_id = job.user_id) THEN
    RAISE EXCEPTION 'Account deletion reservation is required';
  END IF;
  outcome := CASE
    WHEN EXISTS (SELECT 1 FROM storage.buckets) OR EXISTS (SELECT 1 FROM storage.objects)
      THEN 'storage_review_required'
    WHEN public.falconbroom_account_has_shared_data(job.user_id)
      THEN 'shared_data_review_required'
    ELSE 'ready_for_auth'
  END;
  IF outcome <> 'ready_for_auth' THEN
    UPDATE public.falconbroom_deletion_jobs SET status = 'needs_review',
      last_error = outcome, lease_id = NULL, lease_until = NULL, updated_at = now()
      WHERE id = job.id;
  END IF;
  RETURN outcome;
END $$;

CREATE OR REPLACE FUNCTION public.falconbroom_finish_deletion_job(
  p_job_id uuid, p_lease_id uuid, p_result text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  IF p_result IS NULL OR p_result NOT IN ('completed', 'retry') THEN
    RAISE EXCEPTION 'Invalid job result';
  END IF;
  UPDATE public.falconbroom_deletion_jobs
  SET status = CASE WHEN p_result = 'completed' THEN 'completed' ELSE 'ready' END,
      last_error = CASE WHEN p_result = 'retry' THEN 'provider_unavailable' ELSE NULL END,
      retry_after = CASE WHEN p_result = 'retry'
        THEN now() + make_interval(secs => LEAST(3600, 30 * (2 ^ LEAST(attempts - 1, 7))::integer))
        ELSE now() END,
      lease_id = NULL, lease_until = NULL, updated_at = now()
  WHERE id = p_job_id AND status = 'processing' AND lease_id = p_lease_id
    AND lease_until > now() AND (p_result <> 'completed' OR database_deleted);
  RETURN FOUND;
END $$;

REVOKE ALL ON FUNCTION public.falconbroom_create_deletion_job(uuid, text) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.falconbroom_deletion_job_status(uuid, text) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.falconbroom_deletion_local_complete(uuid, uuid, text) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.falconbroom_claim_deletion_job() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.falconbroom_deletion_database_step(uuid, uuid) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.falconbroom_deletion_before_auth(uuid, uuid) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.falconbroom_finish_deletion_job(uuid, uuid, text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.falconbroom_create_deletion_job(uuid, text) TO service_role;
GRANT EXECUTE ON FUNCTION public.falconbroom_deletion_job_status(uuid, text) TO service_role;
GRANT EXECUTE ON FUNCTION public.falconbroom_deletion_local_complete(uuid, uuid, text) TO service_role;
GRANT EXECUTE ON FUNCTION public.falconbroom_claim_deletion_job() TO service_role;
GRANT EXECUTE ON FUNCTION public.falconbroom_deletion_database_step(uuid, uuid) TO service_role;
GRANT EXECUTE ON FUNCTION public.falconbroom_deletion_before_auth(uuid, uuid) TO service_role;
GRANT EXECUTE ON FUNCTION public.falconbroom_finish_deletion_job(uuid, uuid, text) TO service_role;
