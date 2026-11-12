PRIVACY & TEST DATA POLICY

Purpose
- Explain how consent/PII in `data/` is handled for partner testing.
- Provide a retention policy and safe deletion steps.
- Offer minimal partner-facing instructions and disclaimers.
- This is a test-data policy, not the customer privacy notice. The desktop app's
  draft notice is in `docs/customer-privacy-notice-draft.md`.

Consent & PII
- The `data/` folder may contain consent records, user profiles, uploads and other production-like artifacts.
- On the updated Windows desktop app, managed uploads and their metadata,
  recipes, history, snapshots and supported outputs are encrypted using a key
  protected by the current Windows user profile. New inspections are protected;
  older inspections require the separately confirmed Settings action. Other
  data and temporary files may remain plaintext. See the draft notice for limits.
- Workflow drafts for uploaded files are stored in that upload's `.meta.json` sidecar. Drafts can include the user's instruction, recipe JSON, and editor settings; they do not duplicate the uploaded file contents.
- Deleting an upload through the app also deletes its metadata sidecar and saved workflow draft.
- Before sharing the repository or a snapshot with partners, ensure consent records and PII are either:
  - replaced by synthetic records (preferred), or
  - removed entirely from the distribution.

Sanitization tool
- Use `scripts/sanitize_data.py` to create a sanitized copy of runtime data into `samples/sanitized/`.
- The script redacts known PII fields (emails, usernames) and replaces them with safe placeholders.
- It does not overwrite original files by default. To apply in-place, run with `--inplace` and confirm the prompt.

Retention policy (test environment)
- Default retention for partner test data: 30 days.
- After 30 days the following should occur:
  - manual review and explicit deletion of any data that contains real PII, or
  - automatic deletion using `scripts/clear_test_data.ps1` (or equivalent) after confirmation.

How to delete test data
- To remove repository test runtime data locally, after checking the target:

  PowerShell:

    .\scripts\clear_test_data.ps1

  The script operates on `data/` under the current working directory, not
  necessarily the installed desktop app's data directory. It prompts for
  confirmation and suppresses some deletion errors; verify the result. It
  does not guarantee secure erasure or remove backups and other copies.

Partner terms & disclaimers (minimal)
- This repository snapshot is provided for limited testing only.
- Do not use real customer or production data in partner test environments unless you have explicit consent and contractual permission.
- By using this snapshot partners agree to:
  - Use only synthetic or anonymized data unless explicit permission is granted.
  - Report any data exposure incidents immediately to the project owner.
  - Delete any copies of test data after testing or upon request.

Contact
- For questions or to request a sanitized snapshot, contact the project owner.
