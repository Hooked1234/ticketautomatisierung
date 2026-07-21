---
name: jira-data-center-api
description: "Use for Jira Data Center REST API authentication, metadata discovery, field resolution, project validation, and safe issue payload work."
---

# Jira Data Center API

1. Read sections 2-8 and 10-11 of `docs/PROJECT_REQUIREMENTS.md`.
2. Use Personal Access Token authentication without logging the token or authorization header.
3. Prefer `/rest/api/2`; verify server details read-only through `serverInfo` only when needed.
4. Resolve custom fields by metadata, ID, schema, and context. Never rely on name alone or hard-code IDs.
5. Enforce the project allowlist and dry run immediately before writes. Do not retry POST requests automatically.
6. Return bounded, sanitized errors; tests must mock every request.
