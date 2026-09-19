# Security and privacy

This is an experimental local dictation integration, not an official Google product.
See [the audit](docs/security-audit.md) and [privacy boundaries](docs/privacy.md).

Do not post real recordings, transcripts, credentials, account emails, runtime logs,
local build bundles, or `.git` metadata in public issues or pull requests.
Use synthetic examples and redacted diagnostics. After a public GitHub repository is
created, enable GitHub private vulnerability reporting before requesting reports.
No dedicated security contact or response-time promise is configured yet.

Automated tests must not use a real microphone, type into user applications, grant
permissions, or change a logged-in account. Keep the installed user service separate.
