---
description: "Use when writing or reviewing automated tests for Jira, configuration, ticket schemas, validators, Excel workbooks, XLSM migration, or VBA compatibility."
applyTo: "tests/**/*.py"
---

# Test Instructions

- Read sections 3, 17, 18, and 19 of `docs/PROJECT_REQUIREMENTS.md` first.
- Tests must be offline and deterministic. Mock every Jira HTTP call, including read-only calls.
- Never use a real token, productive project mutation, Excel installation, or live Jira as a test prerequisite.
- Cover safe defaults, allowlist rejection, all issue-type requirements, actions, legacy `.env`, row isolation, backup behavior, hyperlinks, and button/macro compatibility.
- Assert that diagnostics and failures cannot reveal tokens or complete Jira response bodies.
