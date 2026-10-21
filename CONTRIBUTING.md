# Contributing

Open an issue describing the operational problem, expected behavior and a minimal reproduction with fictional data. For security vulnerabilities, use the private reporting process in SECURITY.md instead.

Create a branch from `main`, make focused changes and add a regression test. Run the documented SQLite, PostgreSQL/Redis, parser and gateway suites relevant to the change. Explain changes to state transitions, schema initialization, lease fencing or external delivery semantics explicitly in the pull request. Preserve additive, repeat-safe initialization and compatibility with retained documents.

Do not submit real invoices, customer names, mailbox contents, credentials or provider transcripts. Keep code, comments and documentation in English. Use clear names and explain operational tradeoffs rather than adding unsupported availability claims.

Contributions are accepted under AGPL-3.0-or-later. By submitting a contribution, you confirm you have permission to contribute it under that license. No contributor license agreement or ownership transfer is required.
