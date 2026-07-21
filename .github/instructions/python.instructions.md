---
description: "Use when creating or changing Python modules for configuration, validation, Excel processing, or Jira Data Center integration."
applyTo: "**/*.py"
---

# Python Instructions

- Read `docs/PROJECT_REQUIREMENTS.md` first.
- Keep core modules UI-independent and preserve the three existing entry points.
- Use typed, focused functions and explicit validation errors; continue processing after row-level failures.
- Keep dry run enabled by default and make all Jira writes deliberate, non-retried, and testable.
- Never log tokens, authorization headers, or complete sensitive Jira responses.
- Preserve legacy `.env` and workbook fields through explicit compatibility adapters rather than duplicated logic.
- Do not hard-code Jira custom-field, board, or sprint IDs.
