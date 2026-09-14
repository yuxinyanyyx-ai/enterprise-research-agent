# Agent Execution Logs

Web and CLI Agent execution writes local JSONL automatically on the first event.
No root `logging.basicConfig()` setup is required. Restart an already running
application to load this instrumentation.

## Location

`<project root>/logs/agent/agent-<PID>.jsonl`

Each line is one JSON event with a UTC timestamp and process ID. Separate process
files avoid concurrent rotation by multiple Web workers. Each file rotates at
5 MiB, retaining three backups (`.1`, `.2`, `.3`). Old process files are not
automatically deleted. These are local diagnostics, not a durable audit database.

From the project directory in PowerShell, list the most recently written files:

```powershell
Get-ChildItem .\logs\agent\*.jsonl | Sort-Object LastWriteTime -Descending
```

Follow the most recently written process log:

```powershell
$log = Get-ChildItem .\logs\agent\*.jsonl | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Get-Content $log.FullName -Tail 30 -Wait
```

Filter a request across current files and backups:

```powershell
Get-Content .\logs\agent\*.jsonl* | ForEach-Object { $_ | ConvertFrom-Json } |
    Where-Object request_id -eq 'REQUEST_ID' |
    Sort-Object timestamp |
    Format-Table timestamp,event,operation,tool_name,status,duration_ms
```

## Events

- `request.started` / `request.finished`: outer ReAct request boundaries.
- `tool.selected`: model-selected tool name, call ID, round, and argument count.
- `execution.started` / `execution.finished`: function execution or instrumented
  model/business operation. `execution_id` pairs the events; `duration_ms` is
  elapsed execution time, not time spent awaiting human confirmation.
- `tool.rejected`: unknown/disallowed tool, repeated batch, or policy rejection.
- `loop.stopped`: round limit reached.
- `workflow.handoff` / `workflow.returned`: entry and return of the named document
  or Watchlist workflow. Return status is `completed`, `needs_clarification`,
  `cancelled`, `failed`, or `rejected`; notification configuration completion is
  not proof of email delivery.
- `tool.reused`: an export result was reused within the current request.
- `document.confirmation_reached` / `document.resumed`: confirmation checkpoint
  reached and a validated confirm/edit/reject response received. LangGraph replays
  the confirmation node on resume, so `confirmation_reached` may appear again.
  It does not mean another FDA query was executed.

Instrumented business operations include direct DMF queries, confirmed document
queries, Watchlist handlers, and Excel export. This is not a trace of every inner
graph node. On an unhandled failure a start may have no matching request finish.

## Privacy and Failure Behavior

Logs contain request/call/execution IDs, tool/operation names, argument counts,
status, exception class names, and timing. Raw argument values, prompts, emails,
credentials, document content, result records, and exception messages are not
written by this logger. Tool parameters are intentionally omitted, not partially
redacted. Existing application loggers and terminal output are independent.

`completed` means the operation returned; `business_failure` means a query/tool
explicitly returned `success=False`; `error` means an exception or execution
error. Logs are best effort: an unavailable log directory does not fail the
business operation. Host log retention and file permissions remain operational
responsibilities. Pytest and Agent Eval redirect audit logs to temporary directories
and close handlers before cleanup on Windows. Existing workspace logs are not deleted.