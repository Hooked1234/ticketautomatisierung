# Betrieb, Upgrade und Pilotfreigabe

## Installation und Build

Für den Entwicklungsstart wird Python 3.11 oder neuer benötigt. Der Windows-Referenzbuild nutzt
64-Bit Python 3.12 und wird isoliert aus einer eigenen `.venv-build`-Umgebung erzeugt:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
```

Das Skript installiert die in `pyproject.toml` festgelegten Versionsbereiche, prüft die
aufgelösten Abhängigkeiten, führt Tests, Linting, Typprüfung und `compileall` aus und erzeugt
anschließend `dist\TicketPilot\TicketPilot.exe`. Bei einem Fehler eines externen Befehls bricht
es mit einem Fehlercode ab. Die konkret aufgelösten Paketversionen werden für die Nachvollziehbarkeit
in `dist\TicketPilot-build-requirements.txt` festgehalten; dies ist kein kryptografisch
reproduzierbarer oder signierter Build.

Der komplette Ordner `dist\TicketPilot` gehört zum Programm und muss gemeinsam kopiert werden;
die EXE allein ist nicht lauffähig. Der Build muss auf Windows ausgeführt werden. Ein Linux-Build
ist kein gültiger EXE-Smoke-Test.

## Lokale Daten und Sicherung

TicketPilot schreibt unabhängig vom Installations- oder Buildordner ausschließlich Nutzerdaten
unter `%LOCALAPPDATA%\TicketPilot`. Dort liegen `ticketpilot.sqlite3`, die zugehörigen
SQLite-WAL-Dateien und rotierende, bereinigte Logdateien. Vor einem Upgrade sollte die
Anwendung beendet und der gesamte Ordner in ein datiertes Sicherungsverzeichnis kopiert werden.
Die SQLite-Datei enthält Entwürfe, Ergebnisse, Auditdaten und Metadaten-Cache, aber keine Tokens.
Gespeicherte Tokens liegen getrennt im Windows-Anmeldeinformationsmanager.

Wiederherstellung:

1. TicketPilot vollständig beenden.
2. Den aktuellen Datenordner zusätzlich sichern.
3. Die gesicherte `ticketpilot.sqlite3` in `%LOCALAPPDATA%\TicketPilot` zurückkopieren.
4. TicketPilot starten und Status-, Ticket- und Auditansicht prüfen.

## Upgrade und Deinstallation

Eine neue Programmversion ersetzt nur die Programmdateien. Das SQLite-Schema wird beim Start
versioniert migriert; Nutzerdaten werden nicht durch den Build überschrieben. Vor jedem Upgrade
gilt trotzdem die oben beschriebene Sicherung.

Zum Deinstallieren werden die Programmdateien entfernt. Lokale Nutzerdaten und Einträge im
Windows-Anmeldeinformationsmanager werden nicht automatisch gelöscht. Eine Löschung dieser Daten
ist eine getrennte, ausdrückliche Nutzeraktion.

## Fehlerbehebung

- **App startet nicht:** Windows-Ereignisanzeige und lokale, bereinigte Logdateien prüfen; niemals
  Token oder Authorization-Header in ein Support-Ticket kopieren.
- **Metadaten veraltet:** Unter *Einstellungen & Audit* „Metadaten aktualisieren“ ausführen. Ein
  erzwungener Refresh fällt vor einem Schreibvorgang nicht auf veraltete Daten zurück.
- **Konflikt:** Jira-Daten neu laden, Diff erneut prüfen und eine neue Vorschau bestätigen.
- **Unklarer Zustand:** Nicht erneut ausführen. Zuerst in Jira beziehungsweise über den künftigen
  Reconcile-Flow prüfen, ob die Operation angewendet wurde.
- **Token vergessen:** In der Einrichtung neu eingeben. Tokens werden nicht aus SQLite oder Logs
  wiederhergestellt.

## Pilot-Checkliste

- [ ] Windows-EXE auf einem frischen Windows-Benutzerprofil gebaut und gestartet
- [ ] Build und EXE-Start aus einem Projekt-/Programmordner mit Leerzeichen geprüft
- [ ] Vollständigen `dist\TicketPilot`-Ordner kopiert und aus dem Zielordner gestartet
- [ ] Versionsanzeige und lokaler Datenpfad geprüft
- [ ] Dry Run beim ersten Start aktiv
- [ ] Token ausschließlich im Windows-Anmeldeinformationsmanager gespeichert
- [ ] Read-only Verbindungstest mit freigegebenem Konto erfolgreich
- [ ] Projekt-Allowlist und Zielprojekt sichtbar und korrekt
- [ ] CREATE, UPDATE, IGNORE zunächst ausschließlich im Dry Run geprüft
- [ ] UPDATE-Konflikt und `<CLEAR>` mit synthetischen beziehungsweise freigegebenen Testdaten geprüft
- [ ] Fehler einer Zeile blockiert andere Zeilen nicht
- [ ] Dashboard und Kommentare bleiben read-only
- [ ] Backup und Restore der lokalen Daten praktisch geprüft
- [ ] Produktiver Schreib-Smoke-Test und Code-Signing separat autorisiert und dokumentiert

Bis diese Punkte mit der realen Jira-Anbindung nachgewiesen sind, bleibt der Pilotstatus
„technisch vorbereitet, externe Freigaben offen“.
