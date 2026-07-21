---
name: jira-excel-testing
description: "Use for offline regression tests of configuration, Jira request handling, ticket schemas, validators, Excel migration, hyperlinks, and VBA entry-point compatibility."
---

# Jira Excel Testing

1. Read sections 3 and 17-19 of `docs/PROJECT_REQUIREMENTS.md`.
2. Run only repository-provided test commands; mock all Jira traffic and use temporary workbook files.
3. Cover DAH allowlisting, dry-run defaults, five issue types, exact required fields, actions, and legacy `.env` behavior.
4. Verify existing XLSM files require explicit migration, backups are byte-identical before changes, and hyperlinks use `/browse/KEY`.
5. Check that macro/button names remain stable and no output contains tokens or full Jira response bodies.
6. Report unavailable platform checks, such as real Excel COM validation, separately from test failures.
