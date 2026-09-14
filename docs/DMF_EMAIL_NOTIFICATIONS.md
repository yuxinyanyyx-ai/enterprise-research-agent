# DMF Email Notifications

DMF checks and email delivery run independently. Each active watchlist can select
`immediate` or `weekly_digest` and maintain its own recipient list through the
existing `/api/watchlists` API. This remains a team-shared resource, not a personal
subscription or authenticated mailbox-verification system. Restrict API access
to trusted operators before enabling external delivery.

## Configuration

| Environment variable | Default | Meaning |
| --- | --- | --- |
| `DMF_HISTORY_ENABLED` | `false` | Must be enabled |
| `DMF_WATCHLIST_ENABLED` | `false` | Must be enabled |
| `DMF_NOTIFICATION_ENABLED` | `false` | Global dispatch switch |
| `SMTP_HOST` | empty | SMTP server hostname |
| `SMTP_FROM_EMAIL` | empty | Sender mailbox |
| `SMTP_SECURITY` | `starttls` | `starttls` or `ssl`; certificate verification is enabled |
| `SMTP_PORT` | `587` / `465` | STARTTLS / implicit TLS default port |
| `SMTP_USER` | empty | Optional username for an authenticated SMTP relay |
| `SMTP_PASSWORD` | empty | Password; configure together with `SMTP_USER` |
| `SMTP_TIMEOUT_SECONDS` | `30` | SMTP socket timeout |
| `DMF_NOTIFICATION_WEEKLY_DAY` | `0` | Monday=0 through Sunday=6 |
| `DMF_NOTIFICATION_WEEKLY_HOUR` | `9` | Local hour, 0 through 23 |
| `DMF_NOTIFICATION_TIMEZONE` | `Asia/Taipei` | IANA timezone for weekly boundaries |
| `DMF_NOTIFICATION_MAX_RETRIES` | `3` | Retries after the first attempt; default maximum is four attempts |
| `DMF_NOTIFICATION_RETRY_SECONDS` | `60` | Initial retry delay; doubles, capped at 24 hours |
| `DMF_WATCHLIST_POLL_SECONDS` | `60` | Worker polling interval |
| `DATABASE_URL` | existing application default | Shared database for API and all workers |

Supply credentials through deployment secrets or the local untracked environment.
Never put SMTP passwords in source control. Passwords are excluded from Settings
representations; dispatch error logs contain delivery IDs and exception types,
not SMTP responses, recipient addresses or message bodies. Mail requires an SMTP
endpoint that supports the configured authentication; Microsoft Graph/OAuth is
not implemented by this transport.

## Deployment

Stop API/workers and back up the database before upgrading. Set `DATABASE_URL`
explicitly for the migration and the application so they target the same database.
The following PowerShell commands use the project's `work` interpreter:

```powershell
& 'C:/BITrusted/work/python.exe' -m alembic upgrade head
& 'C:/BITrusted/work/python.exe' -m src.dmf_watchlist.worker
```

The regular worker checks DMFs, then schedules and sends notifications. For prompt
delivery while DMF queries are slow, run an additional notification-only worker:

```powershell
& 'C:/BITrusted/work/python.exe' -m src.dmf_watchlist.worker --notifications-only
```

Use `--once` for a single iteration and `--limit 20` to bound delivery claims per
iteration. A notification-only worker requires `DMF_NOTIFICATION_ENABLED=true`.
These commands can send real email when configured; they are not dry runs.
In production, run workers under a process supervisor and give them the same
database and schedule configuration. Changing the global weekly schedule while
old deliveries are pending should be coordinated by an operator.

Example watchlist creation request:

```json
{
  "dmf_no": "DMF-001",
  "interval_hours": 24,
  "notification_enabled": true,
  "notification_emails": ["recipient@example.com"],
  "notification_mode": "weekly_digest"
}
```

Enabling notifications requires at least one recipient. `interval_hours` controls
DMF checks, not the email schedule. There is no new web configuration screen.

## Chat Configuration

The agent supports `configure_notifications` for an existing single team-shared
watchlist, and notification options on `add`. Requests can enable or disable
notifications, replace the complete recipient list, or select `immediate` versus
`weekly_digest`. Changing only the mode preserves recipients, enablement and the
DMF check interval. Enabling an existing watchlist reuses its saved recipients.

For example: follow DMF-001 and email user@example.com when it changes; change
DMF-001 to weekly email digests; enable DMF-001 email notifications; disable
DMF-001 email notifications; or list the shared watchlist notification settings.

Missing recipients require clarification before any write. New enabled
watchlists also require an explicit mode. A short mailbox-only or mode-only
reply completes the pending request; a new explicit query must not inherit it.
The agent never guesses a personal mailbox. Recipient lists are normalized and
validated through the same schemas as the API. An empty list cannot be saved
while notifications remain enabled. Omitted values are unchanged; explicit false
and empty lists are preserved. Re-adding a deleted watchlist without notification
options keeps the saved recipient list/mode but disables notifications.

Configuration replies display masked recipients and indicate shared scope.
Saving configuration does not send email or verify worker/SMTP availability.
Actual delivery depends on the global switch and backend worker. Batch changes,
one-off forwarding, immediate send commands and partial recipient append/remove
operations are not supported; provide a complete replacement list instead.
The existing team-shared access model is unchanged; there is no mailbox ownership
verification. Restrict chat access as well as API access to trusted operators.
Structured intent extraction still depends on the configured LLM; automated
chat tests stub that model and do not verify live natural-language accuracy.

## Delivery Semantics

- Immediate notifications are enqueued in the same transaction as a new change
  event. They are sent after the change is detected, not at the instant the upstream
  DMF site changes. No-change checks do not create immediate mail.
- Every recipient has a separate row in `dmf_notification_deliveries`. SMTP failure
  for one address does not cause successful addresses to receive a retry.
- The outbox snapshots recipients and message data. Retries reuse that content
  and a stable Message-ID. Event acknowledgement is independent of email status.
- Weekly reports use half-open `[start, end)` calendar windows. The first window
  starts when notification configuration becomes active and ends at the next
  configured weekly boundary. Each report is per watchlist and per recipient.
- Weekly scheduling transactionally advances `last_digest_enqueued_at` when
  creating outbox rows; it is not a successful-delivery timestamp. Per-recipient
  `sent_at` records SMTP acceptance. Failed weekly mail remains independently
  retryable without expanding its event window.
- A weekly report is also generated when no change events were recorded. It states
  that this does not imply a successful check, and includes the latest check time
  and consecutive failure count available when the report was generated.
- A worker started after the scheduled hour catches up. After a long outage it
  enqueues at most one outstanding weekly window per watchlist per iteration.
- Historical events predating this migration are not emailed. Existing weekly
  configurations begin their reporting window when the scheduler first sees them.
- Disabling global dispatch pauses consumption without deleting pending deliveries.
  Re-enabling it resumes valid queued deliveries.
- Changes to recipient list, mode, per-watchlist enablement or watchlist status
  invalidate old queued work and start a new reporting generation. Re-adding a
  deleted watchlist cannot revive earlier delivery tasks. Identical configuration
  updates do not invalidate tasks. An SMTP call already in progress cannot be
  recalled by a later configuration change.
- Atomic database claims and expiring leases prevent ordinary concurrent duplicate
  dispatch and permit recovery after process interruption. Leases are at least
  15 minutes, or ten times the SMTP timeout. Interrupted attempts consume the
  retry budget too.
- SMTP is not an exactly-once protocol: acceptance followed by a process crash or
  a lost server response can lead to duplicate mail on retry. Stable Message-ID
  is not a guarantee that a mailbox will deduplicate. `sent` means SMTP accepted
  the message, not that it reached the inbox. Bounce processing is not implemented.

## Operations And Verification

Inspect `dmf_notification_deliveries` for `pending`, `sending`, `sent`, `failed` and
`cancelled` statuses. Rows expose `attempts`, `next_attempt_at`, `sent_at` and a
sanitized `error_code`. Failed tasks stop automatically at the retry limit and
require operator investigation; there is no automatic unlimited resend or retry
management API. Outbox payloads contain email addresses and DMF snapshots: apply
the same access restrictions and retention policy as the underlying database.

Migration `e91a26b7c804` adds the outbox and reporting-generation state. Downgrading
to `d8e4c0a1b2f3` removes notification delivery history, but preserves watchlists,
their notification configuration and existing DMF events. Stop workers before
any downgrade.

Offline tests use temporary SQLite databases and mocked SMTP, without contacting
the DMF site or sending actual email:

```powershell
& 'C:/BITrusted/work/python.exe' -m pytest -q tests/test_email_service.py tests/test_notification_dispatcher.py tests/test_notification_migration.py tests/test_notification_worker.py
```

Real SMTP credentials, certificate/network access, sender authorization and final
mailbox receipt must still be checked in the target deployment. Concurrency tests
cover SQLite; production database and SMTP integration are separate checks.