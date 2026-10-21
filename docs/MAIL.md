# Mail integration

The default installation uses an internal delivery ledger and makes no external mailbox connection. Real intake and acknowledgments are implemented in `docops/mail.py`; enable them only with an operator-owned account and configuration.

Copy `mail-profile.example.json` to `mail.json` in a private directory outside Git. It defines `client_id`, IMAP host/port/folder/username and SMTP host/port/TLS/sender/fixed recipient. Use absolute secret-file paths inside the mount, such as `/run/mail-secrets/refresh-token`. Configure verified TLS: IMAP implicit TLS, SMTP implicit TLS or STARTTLS. Insecure password transport and disabled certificate verification are unsupported.

For Google OAuth2, create your own authorized OAuth client, enable the mailbox access required by your account and obtain consent/refresh credentials using Google's supported process. Set `auth` to `google_oauth2`, `client_id` to that client's ID, and set `client_secret_file` and `refresh_token_file` to mounted files. Northstar exchanges a refresh token at Google's HTTPS token endpoint and authenticates with XOAUTH2. No OAuth application, client secret or user refresh token is distributed. An authorized app password is also supported with `auth: password` and `password_file`; do not put a password directly in JSON.

Set `DOCUMENT_MAIL_SECRETS_DIR` to this private directory, then include `compose.mail.yml` alongside the base and desired provider files. Confirm only API/worker receive this mount and that the parser still has no secrets or network. The worker polls on a bounded interval of at least 30 seconds. IMAP uses read-only selection and durable UIDVALIDITY/UID receipts; it neither deletes messages nor marks them read.

Test with fictional invoices in a dedicated mailbox first. The protocol tests cover OAuth exchange, TLS commands, receipts, retry and uncertain SMTP outcomes without requiring live account credentials. They do not certify the permissions or behavior of your particular provider account.

If SMTP delivery is uncertain, verify the stable Message-ID in the provider's sent/delivery logs. Resolve it with `python -m docops ack-resolve --outbox-id ID --outcome confirmed --evidence 'Provider receipt reference'`, or `--outcome not-sent` only after confirming non-delivery. Do not replay uncertain deliveries blindly. Record the evidence without placing mailbox credentials in command arguments.
