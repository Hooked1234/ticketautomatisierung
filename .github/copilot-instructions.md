# Excel-Jira Project Instructions

Before any implementation or review, read [docs/PROJECT_REQUIREMENTS.md](../docs/PROJECT_REQUIREMENTS.md). It is the authoritative functional and safety specification.

- Preserve the working `create_tickets.py` entry point, the Excel button, existing workbook data, and VBA macros.
- Default to dry run. Never perform a productive Jira write while developing or testing without explicit approval.
- Keep Jira communication and business logic in UI-independent Python; VBA is limited to interaction, display, and launching Python.
- Resolve Jira custom fields and selectable values from metadata; never hard-code custom-field, board, or sprint IDs.
- Keep `.env` compatibility during migration, enforce the `config.yaml` project allowlist, and never expose credentials or sensitive Jira responses.
- Test offline with mocked Jira requests. Process row-level failures independently.
