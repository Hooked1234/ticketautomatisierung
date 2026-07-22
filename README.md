# TicketPilot

TicketPilot ist eine kleine persönliche Windows-Desktop-App zur sicheren Vorbereitung,
Validierung und späteren Synchronisation von Jira-Tickets. Die Anwendung ist bewusst kein
Excel-Frontend. Sie besitzt einen UI-unabhängigen Python-Kern, eine lokale SQLite-Datenhaltung
und eine deutschsprachige PySide6-Oberfläche.

Der aktuelle Stand arbeitet vollständig offline. Der mitgelieferte Demoadapter stellt
synthetische Metadaten, Tickets und Kommentare bereit. Es erfolgen **keine Jira-Netzwerk- oder
Schreibzugriffe**. Der echte Jira-Data-Center-Adapter wird im nächsten Integrationsschritt hinter
den bereits definierten Ports ergänzt.

## Funktionen

- CREATE, UPDATE und IGNORE mit zeilen-/ticketbezogenen Ergebnissen
- Epic, Story, Bug, Service Request und Incident mit kontextabhängigen Feldern
- Validierung, Vorschau, Feld-Diff, `<CLEAR>`-Semantik und Konfliktschutz
- Dry Run standardmäßig aktiv und explizite Bestätigung vor späteren Schreibvorgängen
- Anhänge, Issue Links, Epic/Parent, Board und Sprint im lokalen Arbeitsmodell
- lokales Dashboard, Filter, Kommentarübersicht, Metadatenstatus und Auditprotokoll
- Tokens ausschließlich über Credential-Store; niemals in SQLite oder Konfigurationsdateien
- asynchrone UI-Aktionen, Doppelklickschutz und Windows-DPI-Unterstützung

## Schnellstart unter Windows

Voraussetzung ist Python 3.11 oder neuer; der referenzierte Windows-Build verwendet 64-Bit
Python 3.12.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
ticketpilot
```

Ohne Aktivierung der virtuellen Umgebung kann die App auch direkt gestartet werden:

```powershell
.venv\Scripts\python.exe -m ticketpilot
```

Beim ersten Start wird der lokale Demomodus verwendet. Ein Jira-Token ist dafür nicht nötig.
Lokale Daten liegen standardmäßig unter `%LOCALAPPDATA%\TicketPilot`.

## Offline-Tests

Die vollständige Offline-Suite benötigt weder ein Jira-Konto noch Netzwerkzugriff:

```powershell
python -m pytest
python -m ruff check src tests
python -m mypy src/ticketpilot
python -m compileall -q src
```

## Windows-Build

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
```

Das ausführbare Programm wird unter `dist\TicketPilot\TicketPilot.exe` erzeugt. Es handelt sich
um einen One-folder-Build: Für Start oder Weitergabe muss der **vollständige** Ordner
`dist\TicketPilot` zusammenbleiben. Das Skript bricht bei jedem fehlgeschlagenen Prüf- oder
Buildschritt ab und schreibt die tatsächlich aufgelösten Paketversionen nach
`dist\TicketPilot-build-requirements.txt`.

Code-Signing, Starttest der erzeugten EXE auf einem frischen Windows-Profil und ein produktiver
Jira-Smoke-Test sind externe Freigabeschritte und werden nicht vorgetäuscht.

## Architektur und Sicherheit

Siehe [ADR 0001](docs/adr/0001-local-desktop-architecture.md),
[Sicherheitskonzept](docs/security.md), [Jira-Integrationsvertrag](docs/jira-integration.md) und
[Betriebs-/Pilot-Handbuch](docs/operations.md).
