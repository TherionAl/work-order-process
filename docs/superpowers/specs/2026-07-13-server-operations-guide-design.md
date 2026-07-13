# Server Operations Guide Design

## Goal

Add a local Chinese runbook for operating the deployed project at
`/opt/work_order_process`. It must let an operator safely check service health,
run the supported import workflows, verify results, and diagnose common import
failures.

## Scope

The final guide will be `docs/server_operations.md` and will cover:

- SSH access through the configured private key, without embedding credentials.
- Project directory, `uv`, `.env`, MySQL, and `daily_runner` health checks.
- The automatic synchronization schedule and log location.
- Manual commands for API probing, single-ticket retry, month/year import,
  customer/contact import, partitions, and sync-log inspection.
- Indexed month-level reconciliation and failure-ID handling.
- Operational boundaries for unavailable or forbidden source tickets and for
  destructive commands.

## Content Design

The guide will use a task-oriented structure. Each workflow will state its
purpose, the command to run from `/opt/work_order_process`, the expected result,
and the follow-up verification query or command.

The manual import section will lead with the smallest safe action: one ticket,
then one month. Year-wide imports are documented as an explicit operator action,
not the default recovery path. When failure IDs are known, the guide directs the
operator to retry only those IDs rather than rerunning a whole month.

## Security and Safety

The guide will not include passwords, private-key material, or `.env` values.
It will identify `mysql-drop-tables` as destructive and explain that source API
responses such as "not found" or "forbidden" cannot be repaired by rerunning
the same import command.

## Verification

Before delivery, verify that the document is present, contains no credentials,
and that every CLI command is defined by the project help output or existing
project documentation. No application or database state will be changed.
