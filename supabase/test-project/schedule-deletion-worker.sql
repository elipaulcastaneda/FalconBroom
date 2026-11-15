-- Experimental scheduler for the isolated disposable test project only.
-- Do not install until Storage and desktop safeguards are independently reviewed.
-- The job runs as postgres, calls the existing atomic operation directly, and
-- never sends credentials or requests through pg_net.
-- Before installation, deliberately set this session-only confirmation:
-- SET falconbroom.disposable_deletion_scheduler = 'zthtkoekvxplppxanwiu';
-- This confirmation does not verify the connected project; check it separately.
DO $setup$
BEGIN
  IF current_user <> 'postgres'
     OR current_setting('falconbroom.disposable_deletion_scheduler', true)
        IS DISTINCT FROM 'zthtkoekvxplppxanwiu' THEN
    RAISE EXCEPTION 'Confirm the disposable test project and install as postgres';
  END IF;
  IF EXISTS (
    SELECT 1 FROM unnest(ARRAY['anon', 'authenticated']) AS role_name
    WHERE pg_has_role(role_name, 'postgres', 'MEMBER')
       OR has_table_privilege(role_name, 'cron.job', 'INSERT, UPDATE, DELETE, TRUNCATE')
       OR has_any_column_privilege(role_name, 'cron.job', 'INSERT, UPDATE')
       OR has_table_privilege(role_name, 'public.falconbroom_deletion_jobs',
                              'INSERT, UPDATE, DELETE, TRUNCATE')
       OR has_any_column_privilege(role_name, 'public.falconbroom_deletion_jobs', 'INSERT, UPDATE')
       OR has_function_privilege(role_name, 'public.falconbroom_claim_deletion_job()', 'EXECUTE')
       OR has_function_privilege(role_name,
            'public.falconbroom_finish_deletion_job(uuid,uuid,text)', 'EXECUTE')
  ) OR EXISTS (
    SELECT 1 FROM unnest(ARRAY['anon', 'authenticated', 'service_role']) AS role_name
    WHERE has_function_privilege(role_name,
      'public.falconbroom_atomic_no_storage_deletion(uuid,uuid)', 'EXECUTE')
  ) THEN
    RAISE EXCEPTION 'Unsafe scheduler or deletion function privileges';
  END IF;
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'falconbroom-test-deletion-worker') THEN
    RAISE EXCEPTION 'Test deletion worker schedule already exists; review it before changing it';
  END IF;
  PERFORM cron.schedule(
    'falconbroom-test-deletion-worker',
    '* * * * *',
    $job$
      DO $run$
      DECLARE
        job jsonb;
        result text;
      BEGIN
        IF current_user <> 'postgres' OR EXISTS (
          SELECT 1 FROM unnest(ARRAY['anon', 'authenticated']) AS role_name
          WHERE pg_has_role(role_name, 'postgres', 'MEMBER')
             OR has_table_privilege(role_name, 'cron.job', 'INSERT, UPDATE, DELETE, TRUNCATE')
             OR has_any_column_privilege(role_name, 'cron.job', 'INSERT, UPDATE')
             OR has_table_privilege(role_name, 'public.falconbroom_deletion_jobs',
                                    'INSERT, UPDATE, DELETE, TRUNCATE')
             OR has_any_column_privilege(role_name, 'public.falconbroom_deletion_jobs', 'INSERT, UPDATE')
             OR has_function_privilege(role_name, 'public.falconbroom_claim_deletion_job()', 'EXECUTE')
             OR has_function_privilege(role_name,
                  'public.falconbroom_finish_deletion_job(uuid,uuid,text)', 'EXECUTE')
        ) OR EXISTS (
          SELECT 1 FROM unnest(ARRAY['anon', 'authenticated', 'service_role']) AS role_name
          WHERE has_function_privilege(role_name,
            'public.falconbroom_atomic_no_storage_deletion(uuid,uuid)', 'EXECUTE')
        ) THEN
          RAISE EXCEPTION 'Unsafe scheduler or deletion function privileges';
        END IF;
        job := public.falconbroom_claim_deletion_job();
        IF job IS NULL THEN RETURN; END IF;
        BEGIN
          result := public.falconbroom_atomic_no_storage_deletion(
            (job->>'job_id')::uuid, (job->>'lease_id')::uuid);
          IF result NOT IN ('completed', 'storage_review_required', 'shared_data_review_required')
             OR result IS NULL THEN
            RAISE EXCEPTION 'Unexpected atomic deletion result';
          END IF;
        EXCEPTION WHEN OTHERS THEN
          IF NOT public.falconbroom_finish_deletion_job(
            (job->>'job_id')::uuid, (job->>'lease_id')::uuid, 'retry') THEN
            RAISE EXCEPTION 'Deletion worker failed and could not schedule a retry';
          END IF;
          RAISE WARNING 'Deletion worker failed; retry scheduled';
        END;
      END
      $run$;
    $job$
  );
END
$setup$;
