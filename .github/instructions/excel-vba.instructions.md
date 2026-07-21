---
description: "Use when changing Excel workbook generation, XLSM migration, VBA modules, buttons, sheets, columns, validation, or hyperlinks."
applyTo:
  - "build_excel_macro.py"
  - "excel_workbook.py"
  - "**/*.bas"
  - "**/*.cls"
  - "**/*.vba"
---

# Excel and VBA Instructions

- Read sections 3, 9, 14, 17, and 18 of `docs/PROJECT_REQUIREMENTS.md` first.
- Never silently delete or overwrite an existing XLSM. Require an explicit migration path and create a backup before structural changes.
- Preserve existing cells, sheets, VBA project content, `Tickets_Erstellen`, and `btnTicketsErstellen`.
- Append missing schema elements during migration; do not reorder populated legacy columns.
- Keep Jira communication and validation in Python, not VBA.
- Represent dates as real Excel dates and Jira keys as `JIRA_URL/browse/KEY` hyperlinks.
