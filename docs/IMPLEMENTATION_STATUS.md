# Implementierungsstatus

Stand: 21.07.2026

## Produktziel

Persönliche Windows-Desktop-App auf Basis von PySide6 mit UI-unabhängigem Kern und lokaler
SQLite-Datenhaltung. Kein Excel-Frontend. Die reale Jira-Verbindung ist bewusst der nächste
Integrationsschritt; bis dahin arbeitet die Anwendung offline mit ausschließlich synthetischen
Daten und ohne Netzwerkzugriffe.

## Statusmatrix

| Bereich | Status | Nachweis |
|---|---|---|
| Projekt- und Buildgerüst | implementiert | `pyproject.toml`, `TicketPilot.spec`, `scripts/build_windows.ps1` |
| Domänenmodell und Feldregeln | implementiert | `src/ticketpilot/domain/` |
| Preview, Diff und sichere Aktionen | implementiert | `src/ticketpilot/application/` |
| Lokale Persistenz, Cache und Audit | implementiert | `src/ticketpilot/infrastructure/` |
| PySide6-Desktopoberfläche | implementiert | `src/ticketpilot/ui/` |
| Offline-/Demomodus | implementiert | lokaler Gateway-Adapter |
| Echte Jira-Verbindung | bewusst offen | nächster vom Nutzer angekündigter Schritt |
| Windows-EXE-Smoke-Test | extern offen | benötigt Windows-Laufzeit mit installierten Build-Abhängigkeiten |
| Code-Signing und produktiver Write-Test | extern offen | separate Autorisierung erforderlich |

## Sicherheitsstand

- Dry Run ist als sicherer Standard vorgesehen.
- Der aktuelle Build führt keinerlei Jira-Netzwerkzugriffe aus.
- Tokens dürfen nur im Credential-Store, niemals in SQLite oder Logs gespeichert werden.
- Delete- und Transition-Funktionen sind ausgeschlossen.

## Verifikation

Der abschließende Offline-Übergabelauf vom 21.07.2026 ergab:

- 131 bestandene Pytest-Tests einschließlich GUI-, Integrations-, Persistenz- und Sicherheitsfällen
- Ruff ohne Befund
- Mypy ohne Befund über 44 Quelldateien
- `compileall` und `pip check` ohne Fehler
- erfolgreich erzeugtes Python-Wheel `ticketpilot-0.1.0-py3-none-any.whl`
- visueller Offscreen-Startcheck der Status- und Dashboardansicht bei 1280 × 760 Pixeln

Die zugehörigen visuellen Referenzen liegen unter `docs/screenshots/`. Die Testanzahl beschreibt
diesen konkreten Übergabelauf und wird bei künftigen Änderungen neu ermittelt.

Noch extern offen sind der Windows-PyInstaller-Build samt EXE-Starttest, Code-Signing und alle
Tests gegen ein echtes Jira-System. Diese Prüfungen benötigen die passende Windows- bzw.
Jira-Umgebung und wurden lokal unter Linux nicht vorgetäuscht.

## Nächster Schritt

Den echten Jira-Data-Center-Adapter hinter den bestehenden Ports ergänzen und anschließend die
Pilot-Checkliste in `docs/operations.md` in einer freigegebenen Windows-/Jira-Testumgebung
abarbeiten.
