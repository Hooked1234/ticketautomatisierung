---
name: jira-excel-sync
description: "Use for CREATE, UPDATE, and IGNORE synchronization between ticket rows and Jira, including validation, previews, conflicts, and row-level results."
---

# Jira Excel Sync

1. Read sections 5-10 and 17-18 of `docs/PROJECT_REQUIREMENTS.md`.
2. Normalize legacy workbook values through a compatibility layer, then validate project, action, issue type, and required fields.
3. Treat blank UPDATE cells as unchanged and `<CLEAR>` as an explicit optional-field removal.
4. Never change project, issue type, reporter, Jira key, or status.
5. Preview and validate before a confirmed update; recheck Jira's `updated` timestamp immediately before writing.
6. Isolate failures per ticket and never retry creation blindly or remove existing links/attachments.
