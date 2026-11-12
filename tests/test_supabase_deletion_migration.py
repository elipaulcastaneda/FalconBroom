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
    setup.write_text(SETUP, encoding="utf-8")
    checks.write_text(CHECKS, encoding="utf-8")
    reservation_checks.write_text(RESERVATION_CHECKS, encoding="utf-8")
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
            "-c", "ROLLBACK",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stdout + result.stderr


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

    def wait_for_sleep(name):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
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
            "0005_reservation_recheck.sql",
        )
    ]
    schema = SETUP + "\n" + "\n".join(path.read_text(encoding="utf-8") for path in migration_paths)
    schema += """
    INSERT INTO auth.users VALUES ('00000000-0000-0000-0000-000000000001'),
                                  ('00000000-0000-0000-0000-000000000002');
    INSERT INTO public.teams (id, name, owner) VALUES
      ('10000000-0000-0000-0000-000000000001', 'race',
       '00000000-0000-0000-0000-000000000001');
    """
    run(schema)
    member_insert = """
      INSERT INTO public.team_members (team_id, user_id) VALUES
      ('10000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000002');
    """
    reserve = "SELECT public.falconbroom_reserve_account_deletion('00000000-0000-0000-0000-000000000001');"
    writer = start("BEGIN; " + member_insert + " SELECT pg_sleep(4); COMMIT;", "fb-race-writer")
    try:
        wait_for_sleep("fb-race-writer")
        reservation = run(reserve)
        stdout, stderr = writer.communicate(timeout=10)
        assert writer.returncode == 0, stdout + stderr
        assert reservation.stdout.strip() == "shared_data_review_required"

        run("DELETE FROM public.team_members")
        reserver = start("BEGIN; " + reserve + " SELECT pg_sleep(4); COMMIT;", "fb-race-reserver")
        try:
            wait_for_sleep("fb-race-reserver")
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
    finally:
        if writer.poll() is None:
            writer.kill()
            writer.communicate()
