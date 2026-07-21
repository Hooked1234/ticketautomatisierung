---
name: excel-xlsm-automation
description: "Use for creating or safely migrating macro-enabled Excel workbooks while preserving data, VBA, buttons, validation, and Jira hyperlinks."
---

# Excel XLSM Automation

1. Read sections 3, 9, 14, and 17-18 of `docs/PROJECT_REQUIREMENTS.md`.
2. Inspect the target before writing; never silently replace an existing XLSM.
3. Back up first, then migrate through Excel COM so VBA and controls remain intact.
4. Append missing columns and sheets without moving populated legacy columns or deleting hidden data.
5. Preserve `Tickets_Erstellen` and `btnTicketsErstellen`; keep business logic in Python.
6. Test schema planning, backups, dates, validation, and hyperlinks offline without requiring Excel.
