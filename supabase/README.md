# Supabase schema and migrations for FalconBroom

This folder contains SQL to provision core tables and Row-Level Security (RLS) policies for Supabase/Postgres.

Files
- `migrations/0001_init.sql` — creates `teams`, `team_members`, `uploads`, `explanations`, helper functions, and RLS policies.
- `migrations/0002_account_deletion.sql` — adds service-role-only shared-data checking and deletion RPCs. The deletion RPC removes the user's FalconBroom rows and returns their owned Storage objects for deletion through the Storage API; it refuses to modify those rows when it detects shared team data or another person's (or an unknown author's) explanation on an owned upload.
- `migrations/0003_deletion_storage_review.sql` — extends the shared-data check to flag missing or ambiguously owned referenced Storage objects and other users' upload references to the account's objects. Since `uploads.storage_path` does not identify a bucket, matching names across buckets are treated conservatively.
- `migrations/0004_deletion_reservations.sql` — experimental service-role-only account reservations and guards for the four application tables. This is not a deletion service and does not freeze Storage or local desktop writes; do not use a reservation as permission to delete.
- `migrations/0005_reservation_recheck.sql` — rechecks covered shared data on a reservation retry, retaining the reservation if review is required. This does not prevent subsequent Storage writes.
- `functions/delete-account/` — an authenticated, read-only preflight. It checks the signed-in user and password, then reports whether covered shared data was detected. It never deletes data or authorizes deletion.

Test project preparation

Create a separate Supabase project and use only disposable test accounts and
synthetic data. Note its project reference and confirm email/password sign-in
is enabled. Do not deploy the code or apply these migrations to production.
The existing files are not a complete deletion implementation. A reviewed
test-project deployment procedure and tests for the actual Supabase Auth and
Storage configuration are still required.

Run the preflight unit tests locally with:

```powershell
node --test supabase\functions\delete-account\core.test.mjs
```

Notes & next steps
- The migration assumes Supabase Auth is in use (references `auth.users`). Policies use `auth.uid()` and `auth.role()`.
- Desktop Supabase account deletion remains disabled. The Edge Function implements only a read-only preflight and is not connected to the desktop app. A successful preflight is not a reservation or permission to delete anything.
- The preflight authenticates the user, verifies the password with a separate short-lived Supabase session, and calls the shared-data check using server-side privileges. It needs testing against a real test project; it does not yet support the desktop app's cross-origin requests or MFA-specific reauthentication. Do not deploy it for customers.
- The `0002` and `0003` checks only detect sharing represented by the covered tables and Storage object metadata; they cannot identify every form of sharing, bucket access policy, external service, or differently configured database. A preflight does not freeze writes. The experimental `0004` reservation prevents covered application-table changes but not Storage or local writes, and the deletion RPC's guard does not protect subsequent Storage and Auth API calls.
- Storage RLS policies alone are insufficient to freeze all writes: Supabase's [signed-upload implementation](https://github.com/supabase/storage/blob/d0461d8748fc0f8346a25d3743155e15aa3ce091/src/http/routes/object/uploadSignedObject.ts) uses a privileged database connection. A retry recheck cannot prevent another write immediately afterward. Supabase [recommends against altering its Storage schema](https://supabase.com/docs/guides/storage/schema/design); a safe, supported coordination mechanism must be established and tested before enabling deletion.
- Before enabling desktop deletion, implement and test a trusted service with user authentication and reauthentication, durable retry and partial-failure reporting. Check for shared data before deleting local data, prevent new relevant writes throughout deletion, and handle Storage and Auth safely. Review all production tables, Storage ownership, shared-team policy and other data stores. Local files on other devices, backups and exports require separate handling.
- Apply the reviewed SQL to a disposable test project and run end-to-end tests before any production deployment. If migration `0002` was already applied, migration tools will not automatically rerun the edited file; a new follow-up migration is required for an existing deployment. No deployment has been performed by these repository changes.
- The existing non-desktop backend needs `SUPABASE_URL`, `SUPABASE_ANON_KEY`, and `SUPABASE_SERVICE_ROLE_KEY`; keep the service-role key server-side and never bundle it in the desktop app.
- Local output files referenced by account-owned history must have matching owner metadata before deletion can remove them. If legacy output ownership cannot be verified, account deletion reports a failure and retains the history record for investigation and retry; do not assign ownership based only on a filename.
- Review policies carefully and adapt the role semantics to your product (e.g. add `owner`, `admin`, `member` roles).
- For production, consider adding audit tables and more restrictive policies (and testing with a non-privileged `anon` role).
