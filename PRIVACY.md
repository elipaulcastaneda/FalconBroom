PRIVACY & TEST DATA POLICY

Purpose
- Explain how consent/PII in `data/` is handled for partner testing.
- Provide a retention policy and safe deletion steps.
- Offer minimal partner-facing instructions and disclaimers.

Consent & PII
- The `data/` folder may contain consent records, user profiles, uploads and other production-like artifacts.
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
- To remove runtime data locally (non-recoverable):

  PowerShell:

    .\scripts\clear_test_data.ps1

  The script will prompt for confirmation before deleting files under `data/`.

Partner terms & disclaimers (minimal)
- This repository snapshot is provided for limited testing only.
- Do not use real customer or production data in partner test environments unless you have explicit consent and contractual permission.
- By using this snapshot partners agree to:
  - Use only synthetic or anonymized data unless explicit permission is granted.
  - Report any data exposure incidents immediately to the project owner.
  - Delete any copies of test data after testing or upon request.

Contact
- For questions or to request a sanitized snapshot, contact the project owner.
