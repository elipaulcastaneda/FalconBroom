"""Integration tests for the deletion migration against a disposable PostgreSQL DB.

Set FALCONBROOM_DELETION_TEST_DSN to an empty local database named
falconbroom_deletion_test and FALCONBROOM_DELETION_TEST_PSQL to psql's path.
The test creates schemas and roles inside a transaction, then rolls it back.
"""
import os
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest


ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("FALCONBROOM_DELETION_TEST_DSN")
PSQL = os.environ.get("FALCONBROOM_DELETION_TEST_PSQL", "psql")

SETUP = """
CREATE ROLE anon;
CREATE ROLE authenticated;
CREATE ROLE service_role;
CREATE SCHEMA auth;
CREATE SCHEMA storage;
CREATE TABLE auth.users (id uuid PRIMARY KEY);
CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql AS
  'SELECT NULLIF(current_setting(''request.jwt.claim.sub'', true), '''')::uuid';
CREATE FUNCTION auth.role() RETURNS text LANGUAGE sql AS 'SELECT NULL::text';
CREATE TABLE storage.buckets (id text PRIMARY KEY);
CREATE TABLE storage.objects (bucket_id text, name text, owner_id text);
"""

CHECKS = """
INSERT INTO auth.users (id) VALUES
  ('00000000-0000-0000-0000-000000000001'),
  ('00000000-0000-0000-0000-000000000002');
INSERT INTO storage.objects VALUES
  ('uploads', 'mine.csv', '00000000-0000-0000-0000-000000000001'),
  ('uploads', 'theirs.csv', '00000000-0000-0000-0000-000000000002');

-- An owned team with another member cannot be deleted.
INSERT INTO public.teams (id, name, owner) VALUES
  ('10000000-0000-0000-0000-000000000001', 'shared',
   '00000000-0000-0000-0000-000000000001');
INSERT INTO public.team_members (team_id, user_id) VALUES
  ('10000000-0000-0000-0000-000000000001',
   '00000000-0000-0000-0000-000000000002');

DO $$
BEGIN
  IF NOT public.falconbroom_account_has_shared_data('00000000-0000-0000-0000-000000000001') THEN
    RAISE EXCEPTION 'preflight did not identify shared team';
  END IF;
  BEGIN
    PERFORM public.falconbroom_delete_user_data('00000000-0000-0000-0000-000000000001');
    RAISE EXCEPTION 'expected shared-team deletion to fail';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'Account deletion requires shared data review' THEN RAISE; END IF;
  END;
  IF NOT EXISTS (SELECT 1 FROM public.teams WHERE name = 'shared') THEN
    RAISE EXCEPTION 'shared team was deleted';
  END IF;
END $$;
DELETE FROM public.team_members;

-- An owned team with another person's upload is also shared.
INSERT INTO public.uploads (id, team_id, owner) VALUES
  ('20000000-0000-0000-0000-000000000002',
   '10000000-0000-0000-0000-000000000001',
   '00000000-0000-0000-0000-000000000002');
DO $$
BEGIN
  IF NOT public.falconbroom_account_has_shared_data('00000000-0000-0000-0000-000000000001') THEN
    RAISE EXCEPTION 'preflight did not identify shared upload';
  END IF;
  BEGIN
    PERFORM public.falconbroom_delete_user_data('00000000-0000-0000-0000-000000000001');
    RAISE EXCEPTION 'expected shared-upload deletion to fail';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'Account deletion requires shared data review' THEN RAISE; END IF;
  END;
  IF NOT EXISTS (SELECT 1 FROM public.uploads WHERE id = '20000000-0000-0000-0000-000000000002') THEN
    RAISE EXCEPTION 'other user upload was changed';
  END IF;
END $$;
DELETE FROM public.uploads;
DELETE FROM public.teams;

-- Another person's explanation must not be removed by the upload cascade.
INSERT INTO public.uploads (id, owner) VALUES
  ('20000000-0000-0000-0000-000000000001',
   '00000000-0000-0000-0000-000000000001');
INSERT INTO public.explanations (upload_id, author) VALUES
  ('20000000-0000-0000-0000-000000000001',
   '00000000-0000-0000-0000-000000000002');
DO $$
BEGIN
  IF NOT public.falconbroom_account_has_shared_data('00000000-0000-0000-0000-000000000001') THEN
    RAISE EXCEPTION 'preflight did not identify shared explanation';
  END IF;
  BEGIN
    PERFORM public.falconbroom_delete_user_data('00000000-0000-0000-0000-000000000001');
    RAISE EXCEPTION 'expected shared explanation deletion to fail';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'Account deletion requires shared data review' THEN RAISE; END IF;
  END;
  IF NOT EXISTS (SELECT 1 FROM public.explanations) OR
     NOT EXISTS (SELECT 1 FROM public.uploads WHERE id = '20000000-0000-0000-0000-000000000001') THEN
    RAISE EXCEPTION 'shared content was deleted';
  END IF;
END $$;
DELETE FROM public.explanations;

-- Unknown explanation ownership is not safe to treat as the deleting user's.
INSERT INTO public.explanations (upload_id, author) VALUES
  ('20000000-0000-0000-0000-000000000001', NULL);
DO $$
BEGIN
  BEGIN
    PERFORM public.falconbroom_delete_user_data('00000000-0000-0000-0000-000000000001');
    RAISE EXCEPTION 'expected unknown author deletion to fail';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'Account deletion requires shared data review' THEN RAISE; END IF;
  END;
END $$;
DELETE FROM public.explanations;

-- An upload in someone else's team must be reviewed even without members.
INSERT INTO public.teams (id, name, owner) VALUES
  ('10000000-0000-0000-0000-000000000002', 'other team',
   '00000000-0000-0000-0000-000000000002');
UPDATE public.uploads SET team_id = '10000000-0000-0000-0000-000000000002'
  WHERE id = '20000000-0000-0000-0000-000000000001';
DO $$
BEGIN
  BEGIN
    PERFORM public.falconbroom_delete_user_data('00000000-0000-0000-0000-000000000001');
    RAISE EXCEPTION 'expected other-team upload deletion to fail';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'Account deletion requires shared data review' THEN RAISE; END IF;
  END;
END $$;
UPDATE public.uploads SET team_id = NULL;

-- A storage reference must resolve unambiguously to this user's object, and
-- another user's upload must not reference an object about to be deleted.
UPDATE public.uploads SET storage_path = 'theirs.csv'
  WHERE id = '20000000-0000-0000-0000-000000000001';
DO $$
BEGIN
  IF NOT public.falconbroom_account_has_shared_data('00000000-0000-0000-0000-000000000001') THEN
    RAISE EXCEPTION 'other owner storage reference was not flagged';
  END IF;
  BEGIN
    PERFORM public.falconbroom_delete_user_data('00000000-0000-0000-0000-000000000001');
    RAISE EXCEPTION 'expected storage reference deletion to fail';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'Account deletion requires shared data review' THEN RAISE; END IF;
  END;
END $$;
UPDATE public.uploads SET storage_path = 'missing.csv'
  WHERE id = '20000000-0000-0000-0000-000000000001';
DO $$
BEGIN
  IF NOT public.falconbroom_account_has_shared_data('00000000-0000-0000-0000-000000000001') THEN
    RAISE EXCEPTION 'missing storage object was not flagged';
  END IF;
END $$;
UPDATE public.uploads SET storage_path = 'mine.csv'
  WHERE id = '20000000-0000-0000-0000-000000000001';
INSERT INTO storage.objects VALUES ('other-bucket', 'mine.csv', NULL);
DO $$
BEGIN
  IF NOT public.falconbroom_account_has_shared_data('00000000-0000-0000-0000-000000000001') THEN
    RAISE EXCEPTION 'ambiguous bucket ownership was not flagged';
  END IF;
END $$;
DELETE FROM storage.objects WHERE bucket_id = 'other-bucket' AND name = 'mine.csv';
INSERT INTO public.uploads (owner, storage_path) VALUES
  ('00000000-0000-0000-0000-000000000002', 'mine.csv');
DO $$
BEGIN
  IF NOT public.falconbroom_account_has_shared_data('00000000-0000-0000-0000-000000000001') THEN
    RAISE EXCEPTION 'other owner reference to storage was not flagged';
  END IF;
END $$;
DELETE FROM public.uploads WHERE owner = '00000000-0000-0000-0000-000000000002';

-- Unshared data can be removed, and storage inventory is scoped to the user.
DO $$
DECLARE result jsonb;
BEGIN
  IF public.falconbroom_account_has_shared_data('00000000-0000-0000-0000-000000000001') THEN
    RAISE EXCEPTION 'preflight incorrectly reported shared data';
  END IF;
  result := public.falconbroom_delete_user_data('00000000-0000-0000-0000-000000000001');
  IF result <> '{"storage_objects":[{"bucket_id":"uploads","name":"mine.csv"}]}'::jsonb THEN
    RAISE EXCEPTION 'unexpected storage inventory: %', result;
  END IF;
  IF EXISTS (SELECT 1 FROM public.uploads WHERE owner = '00000000-0000-0000-0000-000000000001')
     OR NOT EXISTS (SELECT 1 FROM public.teams WHERE name = 'other team') THEN
    RAISE EXCEPTION 'unexpected deletion result';
  END IF;
  IF has_function_privilege('authenticated', 'public.falconbroom_delete_user_data(uuid)', 'EXECUTE')
     OR NOT has_function_privilege('service_role', 'public.falconbroom_delete_user_data(uuid)', 'EXECUTE') THEN
    RAISE EXCEPTION 'incorrect function privileges';
  END IF;
  IF has_function_privilege('authenticated', 'public.falconbroom_account_has_shared_data(uuid)', 'EXECUTE')
     OR NOT has_function_privilege('service_role', 'public.falconbroom_account_has_shared_data(uuid)', 'EXECUTE') THEN
    RAISE EXCEPTION 'incorrect preflight privileges';
  END IF;
  BEGIN
    PERFORM public.falconbroom_delete_user_data(NULL);
    RAISE EXCEPTION 'expected NULL user ID to fail';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'A user ID is required' THEN RAISE; END IF;
  END;
END $$;
"""

RESERVATION_CHECKS = """
INSERT INTO public.teams (id, name, owner) VALUES
  ('10000000-0000-0000-0000-000000000003', 'owned',
   '00000000-0000-0000-0000-000000000001');
INSERT INTO public.team_members (team_id, user_id) VALUES
  ('10000000-0000-0000-0000-000000000003',
   '00000000-0000-0000-0000-000000000002');
DO $$
BEGIN
  IF public.falconbroom_reserve_account_deletion('00000000-0000-0000-0000-000000000001')
      <> 'shared_data_review_required'
     OR EXISTS (SELECT 1 FROM public.falconbroom_deletion_reservations) THEN
    RAISE EXCEPTION 'shared account must not be reserved';
  END IF;
END $$;
DELETE FROM public.team_members;
INSERT INTO public.uploads (id, owner, storage_path) VALUES
  ('20000000-0000-0000-0000-000000000003',
   '00000000-0000-0000-0000-000000000001', 'mine.csv');
DO $$
BEGIN
  IF public.falconbroom_reserve_account_deletion('00000000-0000-0000-0000-000000000001') <> 'reserved'
     OR public.falconbroom_reserve_account_deletion('00000000-0000-0000-0000-000000000001') <> 'reserved' THEN
    RAISE EXCEPTION 'reservation must be idempotent';
  END IF;
  IF has_function_privilege('authenticated', 'public.falconbroom_reserve_account_deletion(uuid)', 'EXECUTE')
     OR has_function_privilege('anon', 'public.falconbroom_cancel_deletion_reservation(uuid)', 'EXECUTE')
     OR NOT has_function_privilege('service_role', 'public.falconbroom_reserve_account_deletion(uuid)', 'EXECUTE')
     OR has_table_privilege('authenticated', 'public.falconbroom_deletion_reservations', 'SELECT') THEN
    RAISE EXCEPTION 'reservation privileges incorrect';
  END IF;
  BEGIN
    INSERT INTO public.team_members (team_id, user_id) VALUES
      ('10000000-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000002');
    RAISE EXCEPTION 'expected member change to fail';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'Account deletion reservation prevents this change' THEN RAISE; END IF;
  END;
  BEGIN
    INSERT INTO public.explanations (upload_id, author) VALUES
      ('20000000-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000002');
    RAISE EXCEPTION 'expected explanation change to fail';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'Account deletion reservation prevents this change' THEN RAISE; END IF;
  END;
  BEGIN
    INSERT INTO public.uploads (owner, storage_path) VALUES
      ('00000000-0000-0000-0000-000000000002', 'mine.csv');
    RAISE EXCEPTION 'expected shared storage reference to fail';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'Account deletion reservation prevents this change' THEN RAISE; END IF;
  END;
END $$;
-- Storage is not covered by the application-table triggers: a retry must not
-- report a stale reservation as safe after object ownership changes.
UPDATE storage.objects SET owner_id = '00000000-0000-0000-0000-000000000002'
  WHERE bucket_id = 'uploads' AND name = 'mine.csv';
DO $$
BEGIN
  IF public.falconbroom_reserve_account_deletion('00000000-0000-0000-0000-000000000001')
      <> 'shared_data_review_required' THEN
    RAISE EXCEPTION 'reservation retry failed to recheck changed storage';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM public.falconbroom_deletion_reservations
                 WHERE user_id = '00000000-0000-0000-0000-000000000001') THEN
    RAISE EXCEPTION 'review must not release the existing reservation';
  END IF;
END $$;
UPDATE storage.objects SET owner_id = '00000000-0000-0000-0000-000000000001'
  WHERE bucket_id = 'uploads' AND name = 'mine.csv';
-- Unrelated writes remain possible.
INSERT INTO public.uploads (owner) VALUES ('00000000-0000-0000-0000-000000000002');
SELECT set_config('request.jwt.claim.sub', '00000000-0000-0000-0000-000000000001', true);
DO $$
BEGIN
  BEGIN
    INSERT INTO public.teams (name, owner) VALUES ('new', NULL);
    RAISE EXCEPTION 'expected reserved actor to be blocked';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'Account deletion reservation prevents this change' THEN RAISE; END IF;
  END;
END $$;
SELECT set_config('request.jwt.claim.sub', '', true);
DO $$
BEGIN
  IF NOT public.falconbroom_cancel_deletion_reservation('00000000-0000-0000-0000-000000000001')
     OR public.falconbroom_cancel_deletion_reservation('00000000-0000-0000-0000-000000000001') THEN
    RAISE EXCEPTION 'reservation cancellation failed';
  END IF;
END $$;
INSERT INTO public.team_members (team_id, user_id) VALUES
  ('10000000-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000002');
"""

RESERVED_DELETION_CHECKS = """
DELETE FROM public.team_members;
DELETE FROM storage.objects;
UPDATE public.uploads SET storage_path = NULL
  WHERE owner = '00000000-0000-0000-0000-000000000001';
INSERT INTO public.team_members (team_id, user_id) VALUES
  ('10000000-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000002');
INSERT INTO public.falconbroom_deletion_reservations (user_id)
  VALUES ('00000000-0000-0000-0000-000000000001');
DO $$
BEGIN
  IF public.falconbroom_delete_reserved_user_data('00000000-0000-0000-0000-000000000001')
      <> 'shared_data_review_required'
     OR NOT EXISTS (SELECT 1 FROM public.team_members)
     OR NOT EXISTS (SELECT 1 FROM public.falconbroom_deletion_reservations) THEN
    RAISE EXCEPTION 'shared data must stop deletion without removing the reservation';
  END IF;
END $$;
DELETE FROM public.falconbroom_deletion_reservations;
DELETE FROM public.team_members;
INSERT INTO storage.buckets VALUES ('test');
DO $$
BEGIN
  IF public.falconbroom_reserve_deletion_without_storage('00000000-0000-0000-0000-000000000001')
      <> 'storage_review_required'
     OR EXISTS (SELECT 1 FROM public.falconbroom_deletion_reservations) THEN
    RAISE EXCEPTION 'Storage must stop reservation';
  END IF;
END $$;
DELETE FROM storage.buckets;
DO $$
DECLARE
  target uuid := '00000000-0000-0000-0000-000000000001';
BEGIN
  BEGIN
    PERFORM public.falconbroom_delete_reserved_user_data(target);
    RAISE EXCEPTION 'expected a reservation to be required';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'Account deletion reservation is required' THEN RAISE; END IF;
  END;
  IF public.falconbroom_reserve_deletion_without_storage(target) <> 'reserved' THEN
    RAISE EXCEPTION 'failed to reserve account';
  END IF;
END $$;
INSERT INTO storage.buckets VALUES ('test');
DO $$
BEGIN
  IF public.falconbroom_delete_reserved_user_data('00000000-0000-0000-0000-000000000001')
      <> 'storage_review_required'
     OR public.falconbroom_reserve_deletion_without_storage('00000000-0000-0000-0000-000000000001')
      <> 'storage_review_required'
     OR NOT EXISTS (SELECT 1 FROM public.falconbroom_deletion_reservations)
     OR NOT EXISTS (SELECT 1 FROM public.teams WHERE name = 'owned') THEN
    RAISE EXCEPTION 'bucket must stop deletion without removing the reservation or data';
  END IF;
END $$;
DELETE FROM storage.buckets;
INSERT INTO storage.objects VALUES ('test', 'object', NULL);
DO $$
BEGIN
  IF public.falconbroom_delete_reserved_user_data('00000000-0000-0000-0000-000000000001')
      <> 'storage_review_required' THEN
    RAISE EXCEPTION 'objects must stop deletion';
  END IF;
END $$;
DELETE FROM storage.objects;

CREATE FUNCTION public.test_fail_deletion() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'simulated deletion failure';
END $$;
CREATE TRIGGER test_fail_deletion BEFORE DELETE ON public.teams
  FOR EACH ROW EXECUTE FUNCTION public.test_fail_deletion();
DO $$
BEGIN
  BEGIN
    PERFORM public.falconbroom_delete_reserved_user_data('00000000-0000-0000-0000-000000000001');
    RAISE EXCEPTION 'expected simulated deletion failure';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'simulated deletion failure' THEN RAISE; END IF;
  END;
  IF NOT EXISTS (SELECT 1 FROM public.falconbroom_deletion_reservations)
     OR NOT EXISTS (SELECT 1 FROM public.teams WHERE name = 'owned')
     OR NOT EXISTS (SELECT 1 FROM public.uploads WHERE owner = '00000000-0000-0000-0000-000000000001') THEN
    RAISE EXCEPTION 'failed deletion did not roll back';
  END IF;
END $$;
DROP TRIGGER test_fail_deletion ON public.teams;
DO $$
DECLARE result text;
BEGIN
  result := public.falconbroom_delete_reserved_user_data('00000000-0000-0000-0000-000000000001');
  IF result <> 'completed'
     OR NOT EXISTS (SELECT 1 FROM public.falconbroom_deletion_reservations
                    WHERE user_id = '00000000-0000-0000-0000-000000000001')
     OR EXISTS (SELECT 1 FROM public.teams WHERE owner = '00000000-0000-0000-0000-000000000001')
     OR EXISTS (SELECT 1 FROM public.uploads WHERE owner = '00000000-0000-0000-0000-000000000001')
     OR NOT EXISTS (SELECT 1 FROM public.teams WHERE name = 'other team')
     OR NOT EXISTS (SELECT 1 FROM public.uploads WHERE owner = '00000000-0000-0000-0000-000000000002') THEN
    RAISE EXCEPTION 'reserved deletion did not preserve the reservation or other user data';
  END IF;
  IF has_function_privilege('authenticated', 'public.falconbroom_reserve_deletion_without_storage(uuid)', 'EXECUTE')
     OR has_function_privilege('authenticated', 'public.falconbroom_delete_reserved_user_data(uuid)', 'EXECUTE')
     OR NOT has_function_privilege('service_role', 'public.falconbroom_delete_reserved_user_data(uuid)', 'EXECUTE') THEN
    RAISE EXCEPTION 'reserved deletion privileges incorrect';
  END IF;
  IF public.falconbroom_delete_reserved_user_data('00000000-0000-0000-0000-000000000001') <> 'completed' THEN
    RAISE EXCEPTION 'reserved deletion must be retryable';
  END IF;
END $$;
"""

DELETION_JOB_CHECKS = """
-- The preceding checks retain a reservation for user 1.
DO $$
DECLARE
  job jsonb;
  claim jsonb;
  next_claim jsonb;
  job_uuid uuid;
  recovery_hash text := repeat('a', 64);
  user_id uuid := '00000000-0000-0000-0000-000000000001';
BEGIN
  job := public.falconbroom_create_deletion_job(user_id, recovery_hash);
  job_uuid := (job->>'job_id')::uuid;
  IF job->>'status' <> 'awaiting_local'
     OR (public.falconbroom_create_deletion_job(user_id, recovery_hash)->>'job_id')::uuid <> job_uuid
     OR public.falconbroom_create_deletion_job(user_id, repeat('b', 64))->>'status' <> 'recovery_review_required'
     OR public.falconbroom_deletion_job_status(job_uuid, repeat('b', 64)) IS NOT NULL
     OR public.falconbroom_deletion_job_status(job_uuid, recovery_hash)->>'status' <> 'awaiting_local'
     OR public.falconbroom_claim_deletion_job() IS NOT NULL THEN
    RAISE EXCEPTION 'job must wait for local cleanup and creation must be idempotent';
  END IF;
  INSERT INTO storage.buckets VALUES ('created-after-reservation');
  IF public.falconbroom_create_deletion_job(user_id, recovery_hash)->>'status' <> 'storage_review_required'
     OR public.falconbroom_deletion_job_status(job_uuid, recovery_hash)->>'status' <> 'awaiting_local' THEN
    RAISE EXCEPTION 'existing jobs must recheck storage before local cleanup';
  END IF;
  DELETE FROM storage.buckets;
  IF public.falconbroom_deletion_local_complete('00000000-0000-0000-0000-000000000002', job_uuid, recovery_hash)
     OR public.falconbroom_deletion_local_complete(user_id, job_uuid, repeat('b', 64))
     OR NOT public.falconbroom_deletion_local_complete(user_id, job_uuid, recovery_hash)
     OR NOT public.falconbroom_deletion_local_complete(user_id, job_uuid, recovery_hash) THEN
    RAISE EXCEPTION 'local completion must be user-scoped and idempotent';
  END IF;
  claim := public.falconbroom_claim_deletion_job();
  IF claim->>'job_id' <> job_uuid::text OR public.falconbroom_claim_deletion_job() IS NOT NULL THEN
    RAISE EXCEPTION 'job must be claimed once';
  END IF;
  IF public.falconbroom_finish_deletion_job(job_uuid, (claim->>'lease_id')::uuid, 'completed') THEN
    RAISE EXCEPTION 'job cannot complete before database deletion';
  END IF;
  BEGIN
    PERFORM public.falconbroom_deletion_before_auth(job_uuid, (claim->>'lease_id')::uuid);
    RAISE EXCEPTION 'expected pre-Auth check before database deletion to fail';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'Deletion job lease is invalid' THEN RAISE; END IF;
  END;
  IF public.falconbroom_deletion_database_step(job_uuid, (claim->>'lease_id')::uuid) <> 'completed'
     OR NOT public.falconbroom_finish_deletion_job(job_uuid, (claim->>'lease_id')::uuid, 'retry') THEN
    RAISE EXCEPTION 'database step or retry failed';
  END IF;
  IF public.falconbroom_claim_deletion_job() IS NOT NULL THEN
    RAISE EXCEPTION 'provider failure must delay retry';
  END IF;
  UPDATE public.falconbroom_deletion_jobs SET retry_after = now() - interval '1 second'
    WHERE falconbroom_deletion_jobs.id = job_uuid;
  next_claim := public.falconbroom_claim_deletion_job();
  IF claim->>'lease_id' = next_claim->>'lease_id'
     OR public.falconbroom_finish_deletion_job(job_uuid, (claim->>'lease_id')::uuid, 'completed') THEN
    RAISE EXCEPTION 'stale lease must not complete a job';
  END IF;
  INSERT INTO storage.buckets VALUES ('unexpected');
  IF public.falconbroom_deletion_database_step(job_uuid, (next_claim->>'lease_id')::uuid)
       <> 'storage_review_required'
     OR public.falconbroom_claim_deletion_job() IS NOT NULL THEN
    RAISE EXCEPTION 'storage on retry must require review';
  END IF;
  DELETE FROM storage.buckets;
  IF has_table_privilege('authenticated', 'public.falconbroom_deletion_jobs', 'SELECT')
     OR has_function_privilege('authenticated', 'public.falconbroom_claim_deletion_job()', 'EXECUTE')
     OR has_function_privilege('anon', 'public.falconbroom_deletion_local_complete(uuid, uuid, text)', 'EXECUTE')
     OR has_function_privilege('authenticated', 'public.falconbroom_deletion_job_status(uuid, text)', 'EXECUTE')
     OR has_function_privilege('authenticated', 'public.falconbroom_deletion_before_auth(uuid, uuid)', 'EXECUTE')
     OR NOT has_function_privilege('service_role', 'public.falconbroom_claim_deletion_job()', 'EXECUTE') THEN
    RAISE EXCEPTION 'job privileges incorrect';
  END IF;

  job := public.falconbroom_create_deletion_job('00000000-0000-0000-0000-000000000002', recovery_hash);
  job_uuid := (job->>'job_id')::uuid;
  PERFORM public.falconbroom_deletion_local_complete('00000000-0000-0000-0000-000000000002', job_uuid, recovery_hash);
  claim := public.falconbroom_claim_deletion_job();
  UPDATE public.falconbroom_deletion_jobs SET lease_until = now() - interval '1 second'
    WHERE falconbroom_deletion_jobs.id = job_uuid;
  next_claim := public.falconbroom_claim_deletion_job();
  IF next_claim->>'job_id' <> job_uuid::text OR next_claim->>'lease_id' = claim->>'lease_id' THEN
    RAISE EXCEPTION 'expired job must be reclaimed with a new lease';
  END IF;
  BEGIN
    PERFORM public.falconbroom_deletion_database_step(job_uuid, (claim->>'lease_id')::uuid);
    RAISE EXCEPTION 'expected stale database lease to fail';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'Deletion job lease is invalid' THEN RAISE; END IF;
  END;
  IF public.falconbroom_deletion_database_step(job_uuid, (next_claim->>'lease_id')::uuid) <> 'completed'
     OR public.falconbroom_deletion_before_auth(job_uuid, (next_claim->>'lease_id')::uuid) <> 'ready_for_auth' THEN
    RAISE EXCEPTION 'valid lease must pass the pre-Auth check';
  END IF;
  INSERT INTO storage.buckets VALUES ('created-before-auth');
  IF public.falconbroom_deletion_before_auth(job_uuid, (next_claim->>'lease_id')::uuid)
       <> 'storage_review_required'
     OR public.falconbroom_deletion_job_status(job_uuid, recovery_hash)->>'status' <> 'needs_review' THEN
    RAISE EXCEPTION 'Storage created after database deletion must pause before Auth deletion';
  END IF;
  DELETE FROM storage.buckets;
END $$;
"""

SCHEDULER_SETUP = """
CREATE SCHEMA cron;
CREATE SCHEMA net;
CREATE TABLE cron.job (jobname text, schedule text, command text);
CREATE TABLE net.http_request_queue (id bigint);
GRANT ALL ON net.http_request_queue TO PUBLIC;
CREATE FUNCTION cron.schedule(text, text, text) RETURNS bigint LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO cron.job VALUES ($1, $2, $3);
  RETURN 1;
END $$;
UPDATE public.falconbroom_deletion_jobs SET status = 'needs_review';
SET LOCAL falconbroom.disposable_deletion_scheduler = 'zthtkoekvxplppxanwiu';
CREATE FUNCTION public.test_fail_scheduled_auth_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'synthetic deletion failure'; END $$;
"""

SCHEDULER_CHECKS = """
DO $$
DECLARE
  scheduled_command text;
  target uuid := '00000000-0000-0000-0000-000000000001';
BEGIN
  SELECT command INTO STRICT scheduled_command FROM cron.job
    WHERE jobname = 'falconbroom-test-deletion-worker' AND schedule = '* * * * *';
  IF scheduled_command LIKE '%http_post%' OR scheduled_command LIKE '%secret%' THEN
    RAISE EXCEPTION 'scheduler must not send requests or credentials through pg_net';
  END IF;
  EXECUTE scheduled_command;
  UPDATE public.falconbroom_deletion_jobs SET status = 'ready', retry_after = now() + interval '1 minute'
    WHERE user_id = target;
  EXECUTE scheduled_command;
  IF NOT EXISTS (SELECT 1 FROM auth.users WHERE id = target) THEN
    RAISE EXCEPTION 'scheduler ignored retry backoff or review state';
  END IF;
  UPDATE public.falconbroom_deletion_jobs SET retry_after = now() WHERE user_id = target;
  CREATE TRIGGER test_fail_scheduled_auth_delete BEFORE DELETE ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.test_fail_scheduled_auth_delete();
  EXECUTE scheduled_command;
  IF NOT EXISTS (SELECT 1 FROM auth.users WHERE id = target)
     OR NOT EXISTS (SELECT 1 FROM public.falconbroom_deletion_jobs
                    WHERE user_id = target AND status = 'ready'
                      AND last_error = 'provider_unavailable' AND retry_after > now()) THEN
    RAISE EXCEPTION 'failed atomic deletion must roll back and schedule a retry';
  END IF;
  DROP TRIGGER test_fail_scheduled_auth_delete ON auth.users;
  UPDATE public.falconbroom_deletion_jobs
    SET status = 'processing', lease_until = now() - interval '1 second' WHERE user_id = target;
  INSERT INTO storage.buckets VALUES ('scheduler-review');
  EXECUTE scheduled_command;
  IF NOT EXISTS (SELECT 1 FROM auth.users WHERE id = target)
     OR NOT EXISTS (SELECT 1 FROM public.falconbroom_deletion_jobs
                    WHERE user_id = target AND status = 'needs_review'
                      AND last_error = 'storage_review_required') THEN
    RAISE EXCEPTION 'expired job with Storage must pause for review';
  END IF;
  DELETE FROM storage.buckets;
  UPDATE public.falconbroom_deletion_jobs SET status = 'ready', retry_after = now() WHERE user_id = target;
  GRANT UPDATE (command) ON cron.job TO authenticated;
  BEGIN
    EXECUTE scheduled_command;
    RAISE EXCEPTION 'scheduler accepted unsafe permissions';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'Unsafe scheduler or deletion function privileges'
      THEN RAISE; END IF;
  END;
  REVOKE UPDATE (command) ON cron.job FROM authenticated;
  EXECUTE scheduled_command;
  IF EXISTS (SELECT 1 FROM auth.users WHERE id = target)
     OR NOT EXISTS (SELECT 1 FROM auth.users WHERE id = '00000000-0000-0000-0000-000000000002')
     OR NOT EXISTS (SELECT 1 FROM public.falconbroom_deletion_jobs
                    WHERE user_id = target AND status = 'completed')
     OR EXISTS (SELECT 1 FROM net.http_request_queue) THEN
    RAISE EXCEPTION 'scheduler did not complete only the eligible job without pg_net';
  END IF;
END $$;
"""


@pytest.mark.skipif(not DSN, reason="Disposable PostgreSQL test database not configured")
def test_account_deletion_migration_blocks_shared_data(tmp_path):
    parsed = urlparse(DSN)
    if parsed.scheme not in {"postgres", "postgresql"} or parsed.hostname not in {
        "localhost", "127.0.0.1", "::1",
    } or parsed.path != "/falconbroom_deletion_test":
        pytest.fail("Deletion migration test requires the named disposable local database")

    setup = tmp_path / "setup.sql"
    checks = tmp_path / "checks.sql"
    reservation_checks = tmp_path / "reservation_checks.sql"
    reserved_deletion_checks = tmp_path / "reserved_deletion_checks.sql"
    deletion_job_checks = tmp_path / "deletion_job_checks.sql"
    scheduler_setup = tmp_path / "scheduler_setup.sql"
    scheduler_checks = tmp_path / "scheduler_checks.sql"
    setup.write_text(SETUP, encoding="utf-8")
    checks.write_text(CHECKS, encoding="utf-8")
    reservation_checks.write_text(RESERVATION_CHECKS, encoding="utf-8")
    reserved_deletion_checks.write_text(RESERVED_DELETION_CHECKS, encoding="utf-8")
    deletion_job_checks.write_text(DELETION_JOB_CHECKS, encoding="utf-8")
    scheduler_setup.write_text(SCHEDULER_SETUP, encoding="utf-8")
    scheduler_checks.write_text(SCHEDULER_CHECKS, encoding="utf-8")
    result = subprocess.run(
        [
            PSQL, "-X", "-v", "ON_ERROR_STOP=1", "-d", DSN,
            "-c", "BEGIN",
            "-f", str(setup),
            "-f", str(ROOT / "supabase" / "migrations" / "0001_init.sql"),
            "-f", str(ROOT / "supabase" / "migrations" / "0002_account_deletion.sql"),
            "-f", str(ROOT / "supabase" / "migrations" / "0003_deletion_storage_review.sql"),
            "-f", str(checks),
            "-f", str(ROOT / "supabase" / "migrations" / "0004_deletion_reservations.sql"),
            "-f", str(ROOT / "supabase" / "migrations" / "0005_reservation_recheck.sql"),
            "-f", str(reservation_checks),
            "-f", str(ROOT / "supabase" / "migrations" / "0006_reserved_data_deletion.sql"),
            "-f", str(reserved_deletion_checks),
            "-f", str(ROOT / "supabase" / "migrations" / "0007_deletion_jobs.sql"),
            "-f", str(deletion_job_checks),
            "-f", str(ROOT / "supabase" / "migrations" / "0008_atomic_no_storage_deletion.sql"),
            "-f", str(scheduler_setup),
            "-f", str(ROOT / "supabase" / "test-project" / "schedule-deletion-worker.sql"),
            "-f", str(scheduler_checks),
            "-c", "ROLLBACK",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(not DSN, reason="Disposable PostgreSQL test database not configured")
@pytest.mark.parametrize(
    ("seed", "confirm", "message"),
    [
        ("", False, "Confirm the disposable test project"),
        ("INSERT INTO cron.job VALUES ('falconbroom-test-deletion-worker', NULL);", True,
         "Test deletion worker schedule already exists"),
        ("GRANT UPDATE (command) ON cron.job TO anon;", True,
         "Unsafe scheduler or deletion function privileges"),
        ("GRANT INSERT ON public.falconbroom_deletion_jobs TO authenticated;", True,
         "Unsafe scheduler or deletion function privileges"),
        ("GRANT EXECUTE ON FUNCTION public.falconbroom_claim_deletion_job() TO anon;", True,
         "Unsafe scheduler or deletion function privileges"),
        ("GRANT EXECUTE ON FUNCTION public.falconbroom_atomic_no_storage_deletion(uuid, uuid) TO service_role;", True,
         "Unsafe scheduler or deletion function privileges"),
    ],
)
def test_scheduler_rejects_bad_configuration(seed, confirm, message):
    parsed = urlparse(DSN)
    if parsed.scheme not in {"postgres", "postgresql"} or parsed.hostname not in {
        "localhost", "127.0.0.1", "::1",
    } or parsed.path != "/falconbroom_deletion_test":
        pytest.fail("Deletion scheduler test requires the named disposable local database")
    setup = (
        "CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role; "
        "CREATE SCHEMA cron; "
        "CREATE TABLE cron.job (jobname text, command text); "
        "CREATE TABLE public.falconbroom_deletion_jobs (id uuid); "
        "CREATE FUNCTION public.falconbroom_atomic_no_storage_deletion(uuid, uuid) "
        "RETURNS text LANGUAGE sql AS 'SELECT ''completed'''; "
        "REVOKE ALL ON FUNCTION public.falconbroom_atomic_no_storage_deletion(uuid, uuid) FROM PUBLIC; "
        "CREATE FUNCTION public.falconbroom_claim_deletion_job() "
        "RETURNS jsonb LANGUAGE sql AS 'SELECT NULL::jsonb'; "
        "CREATE FUNCTION public.falconbroom_finish_deletion_job(uuid, uuid, text) "
        "RETURNS boolean LANGUAGE sql AS 'SELECT false'; "
        "REVOKE ALL ON FUNCTION public.falconbroom_claim_deletion_job() FROM PUBLIC; "
        "REVOKE ALL ON FUNCTION public.falconbroom_finish_deletion_job(uuid, uuid, text) FROM PUBLIC; "
        + ("SET LOCAL falconbroom.disposable_deletion_scheduler = 'zthtkoekvxplppxanwiu'; " if confirm else "")
        + seed
    )
    result = subprocess.run(
        [PSQL, "-X", "-v", "ON_ERROR_STOP=1", "-d", DSN, "-c", "BEGIN", "-c", setup,
         "-f", str(ROOT / "supabase" / "test-project" / "schedule-deletion-worker.sql"),
         "-c", "ROLLBACK"],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode != 0
    assert message in result.stderr


@pytest.mark.skipif(
    not DSN or os.environ.get("FALCONBROOM_PRIVATE_PG_CLUSTER") != "1",
    reason="Requires an explicitly designated private disposable PostgreSQL cluster",
)
def test_reservations_serialize_concurrent_application_writes():
    parsed = urlparse(DSN)
    if parsed.scheme not in {"postgres", "postgresql"} or parsed.hostname not in {
        "localhost", "127.0.0.1", "::1",
    } or parsed.path != "/falconbroom_deletion_test":
        pytest.fail("Concurrency test requires the named disposable local database")

    def run(sql, *, check=True):
        return subprocess.run(
            [PSQL, "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "-d", DSN, "-c", sql],
            capture_output=True, text=True, encoding="utf-8", check=check, timeout=15,
        )

    def start(sql, name):
        env = os.environ.copy()
        env["PGAPPNAME"] = name
        return subprocess.Popen(
            [PSQL, "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "-d", DSN, "-c", sql],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8",
        )

    def wait_for_sleep(name, process):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                pytest.fail(f"{name} exited before synchronization:\n{stdout}{stderr}")
            result = run(
                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                f"WHERE application_name = '{name}' AND wait_event = 'PgSleep')"
            )
            if result.stdout.strip() == "t":
                return
            time.sleep(0.05)
        pytest.fail(f"{name} did not reach its synchronization point")

    # This test commits schema and role changes; the opt-in must be used only
    # for a private cluster created specifically for this test.
    migration_paths = [
        ROOT / "supabase" / "migrations" / name
        for name in (
            "0001_init.sql", "0002_account_deletion.sql",
            "0003_deletion_storage_review.sql", "0004_deletion_reservations.sql",
            "0005_reservation_recheck.sql", "0006_reserved_data_deletion.sql",
            "0007_deletion_jobs.sql", "0008_atomic_no_storage_deletion.sql",
        )
    ]
    run(SETUP)
    for path in migration_paths:
        run(path.read_text(encoding="utf-8"))
    run("""
    INSERT INTO auth.users VALUES ('00000000-0000-0000-0000-000000000001'),
                                  ('00000000-0000-0000-0000-000000000002');
    INSERT INTO public.teams (id, name, owner) VALUES
      ('10000000-0000-0000-0000-000000000001', 'race',
       '00000000-0000-0000-0000-000000000001');
    """)
    member_insert = """
      INSERT INTO public.team_members (team_id, user_id) VALUES
      ('10000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000002');
    """
    reserve = "SELECT public.falconbroom_reserve_account_deletion('00000000-0000-0000-0000-000000000001');"
    writer = start("BEGIN; " + member_insert + " SELECT pg_sleep(4); COMMIT;", "fb-race-writer")
    try:
        wait_for_sleep("fb-race-writer", writer)
        reservation = run(reserve)
        stdout, stderr = writer.communicate(timeout=10)
        assert writer.returncode == 0, stdout + stderr
        assert reservation.stdout.strip() == "shared_data_review_required"

        run("DELETE FROM public.team_members")
        reserver = start("BEGIN; " + reserve + " SELECT pg_sleep(4); COMMIT;", "fb-race-reserver")
        try:
            wait_for_sleep("fb-race-reserver", reserver)
            attempted_write = run(member_insert, check=False)
            stdout, stderr = reserver.communicate(timeout=10)
            assert reserver.returncode == 0, stdout + stderr
            assert "reserved" in stdout
            assert attempted_write.returncode != 0
            assert "Account deletion reservation prevents this change" in attempted_write.stderr
        finally:
            if reserver.poll() is None:
                reserver.kill()
                reserver.communicate()

        deletion = start(
            "BEGIN; SELECT public.falconbroom_delete_reserved_user_data"
            "('00000000-0000-0000-0000-000000000001'); SELECT pg_sleep(4); COMMIT;",
            "fb-race-deletion",
        )
        try:
            wait_for_sleep("fb-race-deletion", deletion)
            attempted_write = run(
                "SELECT set_config('request.jwt.claim.sub', "
                "'00000000-0000-0000-0000-000000000001', true); "
                "INSERT INTO public.teams (name, owner) VALUES ('late', NULL);",
                check=False,
            )
            stdout, stderr = deletion.communicate(timeout=10)
            assert deletion.returncode == 0, stdout + stderr
            assert "completed" in stdout
            assert attempted_write.returncode != 0
            assert "Account deletion reservation prevents this change" in attempted_write.stderr
        finally:
            if deletion.poll() is None:
                deletion.kill()
                deletion.communicate()

        run("""
          DELETE FROM public.falconbroom_deletion_reservations;
          INSERT INTO public.uploads (id, owner) VALUES
            ('20000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001'),
            ('20000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000002');
          INSERT INTO public.teams (id, name, owner)
          VALUES ('10000000-0000-0000-0000-000000000003', 'atomic',
                  '00000000-0000-0000-0000-000000000001');
          SELECT public.falconbroom_reserve_account_deletion('00000000-0000-0000-0000-000000000001');
          INSERT INTO public.falconbroom_deletion_jobs (id, user_id, recovery_hash, status, lease_id, lease_until)
          VALUES ('30000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001',
                  repeat('a', 64), 'processing', '40000000-0000-0000-0000-000000000001',
                  clock_timestamp() + interval '5 minutes');
        """)
        atomic = (
            "SELECT public.falconbroom_atomic_no_storage_deletion("
            "'30000000-0000-0000-0000-000000000001', '40000000-0000-0000-0000-000000000001');"
        )
        privilege = run("""
          SELECT has_function_privilege('service_role',
            'public.falconbroom_atomic_no_storage_deletion(uuid,uuid)', 'EXECUTE');
        """)
        assert privilege.stdout.strip() == "f"
        invalid_isolation = run("BEGIN ISOLATION LEVEL REPEATABLE READ; " + atomic, check=False)
        assert invalid_isolation.returncode != 0
        assert "requires read committed" in invalid_isolation.stderr

        run("""
          ALTER TABLE public.team_members DISABLE TRIGGER falconbroom_deletion_reservation_guard;
          INSERT INTO public.team_members (team_id, user_id)
          VALUES ('10000000-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000002');
          ALTER TABLE public.team_members ENABLE TRIGGER falconbroom_deletion_reservation_guard;
        """)
        assert run(atomic).stdout.strip() == "shared_data_review_required"
        assert run("SELECT count(*) FROM auth.users").stdout.strip() == "2"
        assert run("SELECT count(*) FROM public.team_members").stdout.strip() == "1"
        run("""
          ALTER TABLE public.team_members DISABLE TRIGGER falconbroom_deletion_reservation_guard;
          DELETE FROM public.team_members;
          ALTER TABLE public.team_members ENABLE TRIGGER falconbroom_deletion_reservation_guard;
          UPDATE public.falconbroom_deletion_jobs
          SET status = 'processing', lease_id = '40000000-0000-0000-0000-000000000001',
              lease_until = clock_timestamp() + interval '5 minutes';
        """)

        run("""
          CREATE FUNCTION public.test_fail_auth_delete() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN RAISE EXCEPTION 'simulated auth failure'; END $$;
          CREATE TRIGGER test_fail_auth_delete BEFORE DELETE ON auth.users
            FOR EACH ROW EXECUTE FUNCTION public.test_fail_auth_delete();
        """)
        failed = run(atomic, check=False)
        assert failed.returncode != 0
        assert "simulated auth failure" in failed.stderr
        assert run("""
          SELECT EXISTS (SELECT 1 FROM auth.users WHERE id = '00000000-0000-0000-0000-000000000001')
            AND EXISTS (SELECT 1 FROM public.uploads WHERE id = '20000000-0000-0000-0000-000000000001')
            AND EXISTS (SELECT 1 FROM public.falconbroom_deletion_reservations
                        WHERE user_id = '00000000-0000-0000-0000-000000000001')
            AND EXISTS (SELECT 1 FROM public.falconbroom_deletion_jobs WHERE status = 'processing');
        """).stdout.strip() == "t"
        run("DROP TRIGGER test_fail_auth_delete ON auth.users")

        storage_writer = start(
            "BEGIN; INSERT INTO storage.buckets VALUES ('concurrent'); SELECT pg_sleep(4); COMMIT;",
            "fb-storage-writer",
        )
        try:
            wait_for_sleep("fb-storage-writer", storage_writer)
            reviewed = run(atomic)
            stdout, stderr = storage_writer.communicate(timeout=10)
            assert storage_writer.returncode == 0, stdout + stderr
            assert reviewed.stdout.strip() == "storage_review_required"
            assert run("SELECT count(*) FROM auth.users").stdout.strip() == "2"
        finally:
            if storage_writer.poll() is None:
                storage_writer.kill()
                storage_writer.communicate()
        run("""
          DELETE FROM storage.buckets;
          UPDATE public.falconbroom_deletion_jobs
          SET status = 'processing', lease_id = '40000000-0000-0000-0000-000000000001',
              lease_until = clock_timestamp() + interval '5 minutes';
        """)
        blocker = start(
            "BEGIN; INSERT INTO storage.buckets VALUES ('lease-blocker'); SELECT pg_sleep(4); ROLLBACK;",
            "fb-storage-lease-blocker",
        )
        try:
            wait_for_sleep("fb-storage-lease-blocker", blocker)
            run("UPDATE public.falconbroom_deletion_jobs SET lease_until = clock_timestamp() + interval '1 second'")
            expired = run(atomic, check=False)
            stdout, stderr = blocker.communicate(timeout=10)
            assert blocker.returncode == 0, stdout + stderr
            assert expired.returncode != 0
            assert "Deletion job lease is invalid" in expired.stderr
            assert run("SELECT count(*) FROM auth.users").stdout.strip() == "2"
        finally:
            if blocker.poll() is None:
                blocker.kill()
                blocker.communicate()
        run("UPDATE public.falconbroom_deletion_jobs SET lease_until = clock_timestamp() + interval '5 minutes'")

        locked_deletion = start(
            "BEGIN; " + atomic + " SELECT pg_sleep(4); ROLLBACK;", "fb-atomic-deletion",
        )
        try:
            wait_for_sleep("fb-atomic-deletion", locked_deletion)
            for statement in (
                "INSERT INTO storage.buckets VALUES ('late')",
                "INSERT INTO storage.objects VALUES ('late', 'late', NULL)",
            ):
                blocked = run("SET lock_timeout = '300ms'; " + statement, check=False)
                assert blocked.returncode != 0
                assert "lock timeout" in blocked.stderr
            stdout, stderr = locked_deletion.communicate(timeout=10)
            assert locked_deletion.returncode == 0, stdout + stderr
            assert "completed" in stdout
        finally:
            if locked_deletion.poll() is None:
                locked_deletion.kill()
                locked_deletion.communicate()

        assert run("SELECT count(*) FROM auth.users").stdout.strip() == "2"
        assert run("SELECT count(*) FROM public.uploads").stdout.strip() == "2"
        assert run(atomic).stdout.strip() == "completed"
        assert run("""
          SELECT public.falconbroom_finish_deletion_job(
            '30000000-0000-0000-0000-000000000001',
            '40000000-0000-0000-0000-000000000001', 'retry');
        """).stdout.strip() == "f"
        assert run("""
          SELECT NOT EXISTS (SELECT 1 FROM auth.users WHERE id = '00000000-0000-0000-0000-000000000001')
            AND EXISTS (SELECT 1 FROM auth.users WHERE id = '00000000-0000-0000-0000-000000000002')
            AND NOT EXISTS (SELECT 1 FROM public.uploads WHERE id = '20000000-0000-0000-0000-000000000001')
            AND EXISTS (SELECT 1 FROM public.uploads WHERE id = '20000000-0000-0000-0000-000000000002')
            AND EXISTS (SELECT 1 FROM public.falconbroom_deletion_reservations
                        WHERE user_id = '00000000-0000-0000-0000-000000000001')
            AND EXISTS (SELECT 1 FROM public.falconbroom_deletion_jobs
                        WHERE status = 'completed' AND database_deleted);
        """).stdout.strip() == "t"
    finally:
        if writer.poll() is None:
            writer.kill()
            writer.communicate()
