---
name: Instrument observability and graceful errors
description: Use when building backend services, APIs, scheduled jobs, or anything going to production — to add structured logging, health checks, and graceful error handling so the app is debuggable in production without leaking internals to users.
---

**Activation:** On-demand — fires when building backend/production code. Guardrail + actionable: Agent adds the logging/error handling/health checks in code; reading the live logs/resources happens in the Publishing tool.

# Instructions

A production app you can't see into is a production app you can't fix. Build in observability and safe error handling from the start, so incidents are diagnosable without exposing sensitive detail to users.

- Log usefully and safely: emit structured logs at meaningful points (request start/finish, errors, key state changes) with a request/correlation id. Log identifiers, not payloads — never log secrets, tokens, passwords, or PII. On Replit these appear in the published app's Logs tab (retained 7 days); stream elsewhere if you need longer retention.
- Handle errors gracefully: catch errors, log the detail server-side, and return a generic message to the client — never a stack trace, DB error, or internal path. Use correct HTTP status codes (4xx for client errors, 5xx for server errors) so monitoring and clients can react.
- Add a health check: a lightweight endpoint (e.g. /health) that confirms the app and its critical dependencies (DB, required services) are reachable, so monitoring and the uptime checks have a real signal.
- Surface failures of background work: scheduled jobs and automations should log success/failure clearly and alert on repeated failure, not fail silently.
- Make logs actionable for the downtime workflow: include enough context (ids, operation, timing) that someone investigating a red uptime segment can find the cause fast.
- Watch resources: be mindful of CPU/memory; log or guard against obvious leaks. The Resources tab shows live utilization for tuning machine size.

When building backend/production code, state what logging, error handling, and health checks you added, and confirm no sensitive data is logged or returned in errors. Source: Replit Published App Monitoring + security checklist (error handling).
