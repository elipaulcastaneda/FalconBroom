# FalconBroom Privacy Notice (Draft)

Last updated: November 11, 2026

This draft is provided for review and has not been finalized.

This notice describes the FalconBroom desktop application and the account and
backend services it is configured to use. The standard desktop app processes
files on your device. If you use a version connected to a remote FalconBroom
backend, or a separate FalconBroom marketing website, data handling may differ;
see **Remote services and the website** below.

FalconBroom is in beta. Please use synthetic or non-sensitive data. Do not
upload sensitive information or information about other people or organizations
until you have assessed whether the service is appropriate for it.

## FAQ

### Where are my files processed?

In the standard desktop configuration, the app runs its processing backend on
your device. Uploaded files, generated outputs, recipes, inspection results,
and processing history are stored in the app's local data directory. On
Windows, the installed app uses its operating-system application data
directory. You choose where to save exported or downloaded files.

If your app is configured to use a remote backend, files you upload are sent to
that backend for processing and may be stored on its server. A remote backend
configured with HTTP does not encrypt that connection. Check the backend URL
and your organization's configuration before uploading files.

### What information does FalconBroom collect?

Depending on the features you use and the app configuration, FalconBroom may
process:

- Account details such as your email address and username, and information
  needed to authenticate you.
- Files you upload, including their contents and filenames, plus file metadata
  and profiling or inspection results. The contents depend on what you choose.
- Your cleaning instructions, recipes, workflow settings, processing history,
  and generated outputs.
- Local app preferences and session information. The desktop app uses the
  operating system's credential store for sign-in information when available;
  if unavailable, the current session may be kept only in memory. Credentials
  from older versions may remain in device storage or backups. Updating may
  sign you out as older local webview credentials are removed.
- Privacy choices, consent history, and security or operational records, which
  can include an account identifier, user-agent information, and an IP address
  inferred from a request.
- Information you choose to provide when contacting support or making a
  privacy request.

### Does FalconBroom use my data to train AI models?

FalconBroom currently processes cleaning instructions with local rules and
does not send your file contents or instructions to an AI model for training.

### How do I request access to or deletion of my information?

Email [elipaulcastaneda100@gmail.com](mailto:elipaulcastaneda100@gmail.com).
Tell us the email address associated with your account and what you are
requesting. Do not send your password or sensitive file contents. We may ask
you to verify your identity.

For Supabase accounts, account deletion through the desktop app is not yet
available. Contact us to request assistance; do not assume that using the
account-deletion control has deleted your account. Some desktop records are
not associated with an account and may remain on the device.

The separately confirmed **Clear local workspace** control removes FalconBroom's
local uploads, outputs, recipes, inspections, history and snapshots for all
accounts on that device, along with abandoned lock-protected workspaces. It
does not remove active workspaces, older untracked temporary files, accounts,
consent records, exports saved elsewhere, backups, or copies on other devices.
Deletion is not secure erasure.

This process cannot remove copies you downloaded, data stored outside
FalconBroom-managed locations, third-party copies such as files exported to
Google Drive, service audit records retained for security or operational
purposes, or provider backups subject to their retention processes. Shared team
data needed by other members is not deleted as part of one member's request.
Contact us if you need help with those copies or a deletion job that needs
attention.

## How We Use Information

We use information processed by FalconBroom to:

- Create and authenticate accounts and maintain app sessions.
- Profile, clean, transform, preview, and export files at your direction.
- Save app data and history so you can continue or review your work.
- Record privacy choices and security or operational events.
- Respond to support and privacy requests and protect the service.

We do not use uploaded content for advertising or model training, and we do
not sell uploaded files or their contents.

## Remote Services and the Website

The app uses Supabase for account authentication, including sign-up, sign-in,
and account recovery. Supabase receives account details and authentication
information needed for those functions and handles them under its own privacy
and service terms. See
[Supabase's privacy notice](https://supabase.com/privacy).

Google Analytics is disabled in this desktop release. Previous versions may
have saved an analytics preference or cookies; disabling analytics does not
necessarily remove those older cookies.

Some integrations or remote-backend configurations may send information,
including file contents, instructions, or credentials as needed for the
requested feature, to the relevant service provider or backend operator.
The provider depends on the configuration and feature. The standard local
desktop cleaning flow does not require a hosted FalconBroom processing server.

For example, if you choose a remote source such as Google Drive, an
S3-compatible object store, or a web URL, FalconBroom accesses that source to
retrieve the file. The source provider may process connection and account
information under its own terms. These integrations are not required for local
file processing.

This notice covers the FalconBroom app and its account-related services. A
separate FalconBroom marketing website may have different hosting, logging,
cookie, or analytics practices; consult the privacy notice provided on that
website for those practices.

## Data Sharing

FalconBroom does not sell uploaded files or their contents. Account and
authentication information is shared with the authentication provider.
Information you send for support or a privacy request may be processed by
communications providers. Remote backend operators and integration providers
may receive the information needed for the features you use, as described
above. Information may also be disclosed when required by law or when necessary
to protect users, the service, or our legal rights.

When you use a remote backend or an integration, the applicable provider and
its handling practices depend on that deployment and feature. Review your
organization's configuration before uploading confidential information.

## Retention and Deletion

FalconBroom does not currently apply one fixed retention period to all local
app data. Uploaded files and generated data may remain in the app's data
directory until deleted. Deleting a saved upload removes that upload and
attempts to remove its associated metadata; it does not necessarily remove
related outputs, history, downloaded copies, backups, or synced copies.

Use **Uploads** to delete a saved upload, or **Clear local workspace** for the
broader local cleanup described above. Neither deletes your Supabase account.
Supabase and other providers may retain information under their own policies
and retention processes. Contact us for questions about account-related
information, support requests, or provider-held data.

## Cookies, Local Storage, and Analytics

The desktop app stores preferences and session-related information locally.
It uses the operating-system credential store where available, with an
in-memory fallback if secure storage is unavailable. Older versions may have
stored credentials or analytics cookies in webview storage or backups.
This notice does not establish the tracking practices of the separate website.

## Security

FalconBroom uses authentication and other safeguards, but no software or data
transmission can be guaranteed completely secure. Data stored by the desktop
app is on the device where the app runs; uploaded files, outputs, inspections
and snapshots may be stored locally. New Windows desktop inspection records
are protected using the current Windows user profile. Existing inspection
records remain plaintext unless you choose the separately confirmed migration
in Settings. Losing access to that Windows profile can make protected records
unreadable; other software running as that user may be able to access them.
The updated Windows desktop app encrypts managed uploads, normalized uploads,
their conversion metadata, recipes, history, snapshots and outputs from
supported processing routes. It migrates existing files in those managed
folders and the legacy text-generated default output on startup. Back up your
workspace before upgrading; if plaintext and encrypted copies conflict, the
app stops rather than replacing either copy. Earlier installers do not
necessarily include these protections.

Other outputs, consent and audit data, temporary files, backups and exports may
contain plaintext. File names and some operating-system or storage metadata
may also remain visible. Processing can create plaintext temporary files or
write plaintext before encrypting it, and deleted files are not securely erased.
Abandoned lock-protected workspaces are removed on startup, but active
workspaces, older untracked temporary files and other remnants may remain.
There is no recovery password for the Windows profile-bound key. Protect the
device and any exported files.
The app's security and storage configuration can differ when a remote
backend is used. Do not upload sensitive data during the beta unless you have
independently assessed the configuration and risks.

## Your Choices and Rights

Depending on applicable law, you may have rights to request access to,
correction of, deletion of, or a copy of personal information, or to object to
or restrict certain processing. Where the Utah Consumer Privacy Act applies,
these include rights to confirm processing, access, request correction, delete
personal data you provided to the controller, obtain a portable copy of data
you provided, and opt out of sale or targeted advertising, subject to the
law's conditions and exceptions.

To make a request, including an opt-out request, contact us using the details
below. We may need to verify your identity and may be unable to remove
information that must be retained by law or is held in a provider's systems
subject to its own processes. We may not have access to data stored only on
your device or controlled by another organization; contact that organization
where appropriate.

## Changes to This Notice

We may update this notice as FalconBroom changes. An updated app may include a
revised notice with a new "Last updated" date. Review the notice periodically.

## Contact Us

For privacy questions or requests, contact
[elipaulcastaneda100@gmail.com](mailto:elipaulcastaneda100@gmail.com).
