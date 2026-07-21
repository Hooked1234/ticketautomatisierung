# Projektanforderungen: Excel-Jira-Ticketautomatisierung

Dieses Dokument ist die verbindliche fachliche Quelle fuer das Projekt. Vor jeder Implementierung ist es vollstaendig zu lesen. Bei Abweichungen zwischen bestehendem Code und diesem Dokument gelten diese Anforderungen, sofern eine Migration die bestehende, funktionierende Ticket-Erstellung und Excel-Schaltflaeche nicht regressiv beschaedigt.

Stand der Anforderungsaufnahme: 21.07.2026.

## 1. Bestehendes Projekt

Das Projekt enthaelt derzeit:

- `build_excel_macro.py`
  - erzeugt `tickets.xlsm`
  - erstellt Jira-Dropdowns
  - enthaelt VBA-Makros und eine funktionierende Schaltflaeche
  - ueber die Schaltflaeche werden Tickets bereits in Jira erstellt
- `create_tickets.py`
  - liest Tickets aus der Excel-Datei
  - erstellt oder aktualisiert Jira-Issues ueber die Jira-API
- `diagnose_jira_auth.py`
  - prueft Jira-Verbindung und Authentifizierung
- `.env`
  - enthaelt die persoenliche lokale Konfiguration
- `.env.example`
  - enthaelt ausschliesslich Platzhalter
- `requirements.txt`
- `.gitignore`

Die vorhandene Ticket-Erstellung und die Excel-Schaltflaeche funktionieren bereits und duerfen nicht beschaedigt werden.

## 2. Systemumgebung

### Jira

- Basis-URL: `https://jira.dfd-hamburg.de`
- Eigene Jira-Instanz, vermutlich Jira Data Center.
- Bestehender Code deutet auf Jira Data Center 10.3.x hin.
- Die genaue Version ist ueber `/rest/api/2/serverInfo` zu verifizieren.
- Authentifizierung erfolgt ueber einen persoenlichen Personal Access Token.
- Authentifizierung und Ticket-Erstellung funktionieren bereits.
- Es sind ausschliesslich produktive Jira-Projekte vorhanden.
- Keine schreibenden Testaufrufe ohne ausdrueckliche Freigabe.

### Excel

- Microsoft Excel fuer Microsoft 365 Desktop.
- Version 2605.
- Build 16.0.20026.20166.
- Windows 64 Bit.
- Makros duerfen ausgefuehrt werden.

### Projekt

Zunaechst wird ausschliesslich folgendes Projekt unterstuetzt:

- Project Key: `DAH`
- Project Name: `Data & Analytics Hub`

Weitere Projekte werden spaeter ueber `config.yaml` ergaenzt. Project Keys muessen ueber eine Allowlist begrenzt werden.

### Benutzer

- Waehrend der Entwicklung arbeitet der Entwickler mit Python und VS Code.
- Spaeter soll zunaechst ein kleines Team die Loesung verwenden.
- Langfristig soll die Loesung breiter verteilt werden koennen.
- Jeder Nutzer verwendet eine eigene lokale Kopie der Excel-Datei.
- Jeder Nutzer verwendet seinen eigenen persoenlichen Jira-Token.
- Reporter und Berechtigungen muessen dadurch dem angemeldeten Jira-Nutzer entsprechen.

## 3. Verbindliche Sicherheitsregeln

- `DRY_RUN` ist standardmaessig `true`.
- Keine Ticketloeschung.
- Keine Statusaenderung in Version 1.
- Kein Projektwechsel bestehender Tickets.
- Kein Wechsel des Issue Types bestehender Tickets.
- Keine Aenderung des Reporters.
- Keine Aenderung des Jira Keys.
- Keine ungeprueften Wiederholungen von POST-Anfragen.
- Keine Zugangsdaten in Excel, `config.yaml`, Logs oder Quellcode.
- `.env` muss durch `.gitignore` ausgeschlossen bleiben.
- `.env.example` darf nur Platzhalter enthalten.
- Keine vollstaendigen sensiblen Jira-Antworten protokollieren.
- Keine vorhandene XLSM-Datei ungefragt loeschen oder ueberschreiben.
- Vor Migration oder struktureller Aenderung einer XLSM ist eine Sicherung beziehungsweise eine sichere Migrationsstrategie zu verwenden.
- Ein Fehler bei einem Ticket darf die Verarbeitung aller uebrigen Tickets nicht stoppen.
- Keine produktiven Jira-Schreibzugriffe waehrend Phase 1.
- Read-only-Aufrufe duerfen nur bei Bedarf verwendet werden.
- Tokens oder vertrauliche Jira-Antworten duerfen niemals in der Ausgabe erscheinen.

Fuer die spaetere Teamversion soll der persoenliche Token vorzugsweise im Windows-Anmeldeinformationsmanager gespeichert werden. Die bestehende `.env`-Unterstuetzung bleibt fuer die Entwicklungsphase erhalten.

## 4. Issue Types und Metadaten

Unterstuetzte Issue Types:

- Epic
- Story
- Bug
- Service Request
- Incident

Pflichtfelder muessen sowohl durch das definierte Schema als auch dynamisch ueber Jira-Metadaten geprueft werden.

Jira-Custom-Field-IDs duerfen nicht fest codiert werden. Custom Fields muessen anhand von Jira-Metadaten, Field-ID, Schema und Kontext aufgeloest werden. Ein Feldname allein ist nicht zwingend eindeutig.

## 5. Feldschema

### Epic

Pflichtfelder:

- Project
- Issue Type
- Summary
- Epic Name

Optionale beziehungsweise konfigurationsabhaengige Felder:

- Priority
- Products and Services
- Component/s
- Account
- Labels
- Attachment
- Due Date
- Start Date
- End Date
- Description
- Reporter
- Assignee
- Beteiligte
- Responsible Team
- Parent Link
- Linked Issues
- Markiert / Impediment
- Fix Version/s

Regeln:

- Markiert ist ein Ja/Nein-Feld fuer Impediment.
- Standardwert ist Nein.
- Beteiligte ist eine Mehrfachauswahl.
- Reporter wird automatisch bestimmt.
- Parent Link wird metadatengesteuert behandelt.
- Epic Name ist beim Epic verpflichtend.

### Story

Pflichtfelder:

- Project
- Issue Type
- Summary
- Component/s

Optionale beziehungsweise konfigurationsabhaengige Felder:

- Priority
- Products and Services
- Account
- Labels
- Description
- Attachment
- Story Points
- Due Date
- Start Date
- End Date
- Assignee
- Beteiligte
- Mitarbeitende Teams
- Epic Link
- Linked Issues
- Original Estimate
- Remaining Estimate

### Bug

Pflichtfelder:

- Project
- Issue Type
- Summary
- Component/s

Optionale beziehungsweise konfigurationsabhaengige Felder:

- Priority
- Products and Services
- Account
- Labels
- Description
- Attachment
- Story Points
- Due Date
- Start Date
- End Date
- Assignee
- Beteiligte
- Mitarbeitende Teams
- Epic Link
- Linked Issues
- Original Estimate
- Remaining Estimate

### Service Request

Pflichtfelder:

- Project
- Issue Type
- Summary

Optionale beziehungsweise konfigurationsabhaengige Felder:

- Priority
- Products and Services
- Component/s
- Account
- Labels
- Description
- Attachment
- Story Points
- Due Date
- Start Date
- End Date
- Assignee
- Mitarbeitende Teams
- Epic Link
- Linked Issues
- Original Estimate
- Remaining Estimate

Beteiligte wird beim Service Request aktuell nicht angeboten.

### Incident

Pflichtfelder:

- Project
- Issue Type
- Summary

Optionale beziehungsweise konfigurationsabhaengige Felder:

- Priority
- Products and Services
- Component/s
- Account
- Labels
- Description
- Attachment
- Story Points
- Due Date
- Start Date
- End Date
- Assignee
- Mitarbeitende Teams
- Epic Link
- Linked Issues
- Original Estimate
- Remaining Estimate

Beteiligte wird beim Incident aktuell nicht angeboten.

## 6. Globale Feldregeln

### Reporter

- Reporter ist immer der aktuell authentifizierte Jira-Nutzer.
- Reporter wird nicht manuell in Excel geaendert.
- Falls Jira den Reporter automatisch setzt, soll kein unnoetiger Reporter-Wert uebertragen werden.

### Assignee

Excel-Auswahl:

- Unassigned
- Assign to me
- konkrete zuweisbare Person

Konkrete Assignees muessen dynamisch ueber Jira gesucht werden. Eine vollstaendige statische Nutzerliste darf nicht vorausgesetzt werden.

### Description

- Einfacher Fliesstext.
- Aktuell keine Vorlagen pro Issue Type.

### Beteiligte

- Mehrfachauswahl von Personen.

### Mitarbeitende Teams

- Jira-Mehrfachauswahl.
- Erlaubte Werte dynamisch aus Jira laden.

### Products and Services

- Grosse dynamische Jira-Suchauswahl.
- Nicht als vollstaendiges statisches Excel-Dropdown implementieren.

### Account

- Dynamische Jira-Suchauswahl.
- `None` muss unterstuetzt werden.

### Labels

- Vorhandene Vorschlaege anzeigen.
- Freie Eingabe neuer Labels erlauben.
- Mehrere Labels unterstuetzen.

### Components

- Jira-Auswahlwerte dynamisch laden.
- Mehrfachauswahl unterstuetzen.
- Pflichtstatus abhaengig vom Issue Type beachten.

### Dates

- In Excel echte Datumswerte verwenden.
- Gewuenschte Darstellung: `DD.MM.YY` beziehungsweise lokales Excel-Datumsformat.
- Fuer die Jira-API korrekt serialisieren.

### Story Points

- Numerisch validieren.
- Nur uebertragen, wenn das Feld fuer Projekt und Issue Type verfuegbar ist.

### Time Tracking

- Jira-Zeitformat verwenden.
- Gueltige Beispiele: `30m`, `2h`, `3d`, `4w`.

### Attachments

- Mehrere Anhaenge pro Ticket ermoeglichen.
- Separates Blatt `Attachments` verwenden.
- Anhaenge erst nach erfolgreicher Ticketerstellung hochladen.
- Fehler eines einzelnen Anhangs getrennt protokollieren.
- Ein fehlgeschlagener Anhang darf das bereits erstellte Ticket nicht loeschen.
- Bei Updates Anhaenge nur hinzufuegen.
- Anhaenge in Version 1 nicht loeschen.

Das in der Jira-Maske unter Linked Issues angezeigte Feld `Issue` ist kein eigenstaendiges Ticketfeld. Es ist Teil der Suche nach zu verknuepfenden Tickets und darf nicht als eigene Excel-Spalte modelliert werden.

## 7. Epic- und Ticketverknuepfungen

Excel soll zwei Arten von Links unterstuetzen.

### 7.1 Anklickbare Jira-Links

- Jira Keys in Excel werden als Hyperlink dargestellt.
- Beispiel: `https://jira.dfd-hamburg.de/browse/DAH-4905`.
- Nutzer sollen keine vollstaendige URL eingeben muessen.

### 7.2 Echte Jira-Issue-Links

- Echte Jira-Beziehungen zwischen Tickets erstellen.
- Link-Typen dynamisch aus Jira laden.
- Keine feste Beschraenkung auf wenige Link-Typen.

Beispiele vorhandener Link-Typen:

- blocks
- is blocked by
- depends on
- duplicates
- is duplicated by
- has defect
- is defect of
- consists of
- has to be finished together with
- has to be done before
- has to be done after
- has impact on
- is influenced by

Die tatsaechlich verfuegbaren Typen sind ueber Jira zu ermitteln.

Bedienung:

- Zielprojekt auswaehlen, zunaechst nur DAH.
- Ticket ueber Jira Key oder Summary suchen.
- Treffer als `KEY - Summary` anzeigen.
- Keine vollstaendige URL verlangen.
- Mehrere Beziehungen pro Ticket unterstuetzen.
- Separates Blatt `Ticket_Links` verwenden.

### Epic Link

- Vorhandene Epics ueber Jira Key oder Epic-Titel suchen.
- Wegen der grossen Anzahl keine vollstaendige statische Liste laden.
- Epic Link ueber die tatsaechlich verfuegbare Jira-Custom-Field-ID setzen.

Bestehende Links duerfen in Version 1 ergaenzt, aber nicht automatisch entfernt werden.

## 8. Sprint- und Boardregeln

- Excel erstellt keine Boards.
- Excel erstellt keine Sprints.
- Boards fuer DAH automatisch ueber die Jira Agile API ermitteln.
- Ein Jira-Projekt kann mehrere Boards besitzen.
- Bei mehreren DAH-Boards Auswahl im Blatt `Einstellungen` ermoeglichen.
- Board-ID intern speichern.
- Sprint-ID intern speichern.
- Nutzern hauptsaechlich den Sprintnamen anzeigen.
- Beispiel eines vorhandenen Sprintnamens: `2026 - Q1 - Sprint 7 - BD`.
- Nur aktive und zukuenftige Sprints fuer neue Zuweisungen anbieten.
- Abgeschlossene Sprints spaeter fuer Reporting laden.
- Sprint bleibt optional.
- Epics erhalten keinen Sprint.
- Story, Bug, Service Request und Incident koennen einem Sprint zugewiesen werden.

## 9. Excel-Bedienung

Das Blatt `Tickets` verwendet eine Zeile pro Ticket.

### Action-Dropdown

- `CREATE`
- `UPDATE`
- `IGNORE`

### CREATE

- Erstellt ein neues Ticket.
- Project Key zunaechst ausschliesslich DAH.
- Jira Key wird nach erfolgreicher Erstellung gespeichert.
- Jira Key wird als anklickbarer Link formatiert.

### UPDATE

- Vorhandenes Ticket ueber Key oder Summary suchen.
- Aktuelle Werte zunaechst aus Jira laden.
- Aenderungen vor Uebertragung vergleichen.
- Dry Run ausfuehren.
- Erst nach Bestaetigung tatsaechlich uebertragen.

### IGNORE

- Zeile wird nicht verarbeitet.

### Vorgesehene Blaetter

- Tickets
- Attachments
- Ticket_Links
- Einstellungen
- Metadaten
- Sprints
- Kommentare
- Sync_Log
- Konflikte
- Dashboard

Nicht alle Blaetter muessen in Phase 1 vollstaendig implementiert werden. Das Schema soll ihre spaetere Ergaenzung aber ermoeglichen.

### Standardmaessig sichtbare Spalten

- Action
- Project
- Issue Type
- Summary
- Description
- Priority
- Component/s
- Assignee
- Epic Link
- Sprint
- Jira Key
- Result
- Error Message

Interne Spaltennamen duerfen konsistent als `IssueType`, `Components`, `EpicLink`, `JiraKey` und `ErrorMessage` gefuehrt werden, wie in der Ausgangskonfiguration vorgegeben.

Optionale Spalten werden ueber das Blatt `Einstellungen` ein- und ausgeblendet. Ausblenden darf keine Inhalte loeschen.

## 10. Sicherer Update-Ablauf

Verbindlicher Ablauf:

1. Action `UPDATE` waehlen.
2. Ticket suchen.
3. Aktuelle Daten aus Jira laden.
4. Gesperrte Felder nicht editierbar darstellen.
5. Gewuenschte Aenderungen eintragen.
6. Aenderungsvorschau erzeugen.
7. Dry Run validieren.
8. Erst nach Bestaetigung aktualisieren.
9. Unmittelbar vor dem Update den Jira-`updated`-Zeitstempel pruefen.
10. Bei zwischenzeitlicher Aenderung Update blockieren und erneutes Laden verlangen.

Nicht aenderbar:

- Project
- Issue Type
- Reporter
- Jira Key
- Jira Status

Jira Status wird in Version 1 nur angezeigt; es wird keine Transition ausgefuehrt.

Bei UPDATE bedeutet eine leere Zelle, dass der vorhandene Jira-Wert nicht veraendert wird. Der ausdrueckliche Wert `<CLEAR>` bedeutet, dass ein optionaler vorhandener Jira-Wert entfernt wird.

Keine Ticketloeschung, kein Schliessen und keine Statusaenderung.

## 11. Konfiguration und Metadatenstrategie

Administrative Einstellungen liegen in `config.yaml`. Persoenliche Einstellungen wie sichtbare Excel-Spalten liegen im Blatt `Einstellungen`.

Empfohlene Ausgangskonfiguration:

```yaml
jira:
  allowed_projects:
    - key: DAH
      name: Data & Analytics Hub

safety:
  dry_run_default: true
  allow_delete: false
  allow_project_change: false
  allow_issue_type_change: false
  allow_status_transition: false

metadata:
  refresh_after_hours: 24

excel:
  default_visible_columns:
    - Action
    - Project
    - IssueType
    - Summary
    - Description
    - Priority
    - Components
    - Assignee
    - EpicLink
    - Sprint
    - JiraKey
    - Result
    - ErrorMessage
```

Regeln:

- `PROJECT_KEY` aus `.env` langfristig durch `allowed_projects` in `config.yaml` ersetzen.
- Bestehende `PROJECT_KEY`-Unterstuetzung waehrend der Migration rueckwaertskompatibel behandeln.
- Schaltflaeche `Jira-Daten aktualisieren` vorsehen.
- Cache nach 24 Stunden als veraltet markieren.
- Keine unnoetige Aktualisierung bei jedem Excel-Start.
- Kritische Werte vor dem Jira-Schreibvorgang erneut validieren.

Kleine Listen cachen:

- Issue Types
- Priorities
- Components
- Mitarbeitende Teams
- Link-Typen
- Boards
- aktive und zukuenftige Sprints
- Fix Versions

Grosse Datenmengen bei Bedarf suchen:

- Assignees
- Beteiligte
- Epics
- Tickets
- Products and Services
- Accounts

## 12. Reporting in einer spaeteren Phase

Das Reporting soll read-only alle zugaenglichen DAH-Tickets ueber Jira/JQL auswerten, nicht nur Tickets der lokalen Excel-Datei.

Standardansicht:

- aktueller Sprint
- Zeitraum frei aenderbar

Kennzahlen:

- Anzahl aller Tickets
- offene Tickets
- Tickets in Bearbeitung
- erledigte Tickets
- Erledigungsquote
- ueberfaellige Tickets
- Tickets ohne Assignee
- Tickets ohne Sprint
- blockierte Tickets beziehungsweise Impediments
- Tickets pro Issue Type
- Tickets pro Priority
- Tickets pro Component
- Tickets pro Team
- Tickets pro Sprint
- geplante Story Points
- abgeschlossene Story Points
- offene Story Points
- Tickets mit ueberschrittenem Due Date

Statusauswertung moeglichst ueber Jira-Statuskategorien:

- To Do
- In Progress
- Done

Filter:

- Projekt
- Sprint
- Zeitraum
- Status
- Statuskategorie
- Issue Type
- Assignee
- Reporter
- Priority
- Component
- Team
- Epic
- Label
- Impediment
- Jira Key
- Freitext

Reporting wird nicht in Phase 1 vollstaendig implementiert.

## 13. Kommentare

Keine KI-Zusammenfassung implementieren.

Spaeter kann eine einfache read-only Kommentaruebersicht ergaenzt werden:

- Autor
- Erstellungsdatum
- Aenderungsdatum
- Kommentartext
- Markierung neuer Kommentare seit der letzten Pruefung

Keine externe KI-API und keine Abhaengigkeit von VS Code oder GitHub Copilot zur Laufzeit.

## 14. Spaetere Benutzeroberflaeche und Verteilung

Die aktuelle Excel-Datei ist Version 1 des Frontends. Spaeter soll optional eine eigenstaendige Windows- oder Weboberflaeche moeglich sein. Deshalb:

- Jira-Kommunikation nicht in VBA implementieren.
- Geschaeftslogik nicht ausschliesslich in VBA implementieren.
- Python-Kernmodule UI-unabhaengig halten.
- Excel/VBA nur fuer Bedienung, Anzeige und Start der Python-Verarbeitung verwenden.

Moegliche spaetere Pilotverteilung:

- `tickets.xlsm`
- `jira-ticket-automation.exe`
- `config.yaml`
- lokale Logs

Langfristig sollen Nutzer weder Python noch VS Code installieren muessen. Diese Verpackung ist nicht Teil von Phase 1, die Architektur muss sie aber ermoeglichen.

## 15. Copilot-Einrichtungsphase

Folgende Dateien sind projektspezifisch bereitzustellen:

- `docs/PROJECT_REQUIREMENTS.md`
- `.github/copilot-instructions.md`
- `.github/agents/excel-jira-reviewer.agent.md`
- `.github/instructions/python.instructions.md`
- `.github/instructions/excel-vba.instructions.md`
- `.github/instructions/tests.instructions.md`
- `.github/skills/jira-data-center-api/SKILL.md`
- `.github/skills/excel-xlsm-automation/SKILL.md`
- `.github/skills/jira-excel-sync/SKILL.md`
- `.github/skills/jira-excel-testing/SKILL.md`

Anforderungen:

- Dieses Dokument muss alle Anforderungen der abgeschlossenen Anforderungsaufnahme vollstaendig enthalten.
- Dieses Dokument ist die verbindliche fachliche Quelle.
- `.github/copilot-instructions.md` muss auf dieses Dokument verweisen.
- Vor jeder Implementierung muss dieses Dokument gelesen werden.
- Skills kurz, spezialisiert und ohne unnoetige Wiederholungen halten.
- Skills duerfen auf relevante Abschnitte dieses Dokuments verweisen.
- Keine ausfuehrbaren Skill-Skripte hinzufuegen.
- Keine Plugins installieren.
- Keine MCP-Abhaengigkeiten hinzufuegen.
- Keine fremden Agents ungeprueft kopieren.
- `excel-jira-reviewer` muss read-only sein.
- Instructions mit passenden `applyTo`-Mustern versehen.
- Frontmatter und Discovery validieren.

Nach dem Erstellen:

1. Die neu erstellten `SKILL.md`-Dateien ausdruecklich erneut einlesen.
2. Fuer Phase 1 insbesondere `jira-data-center-api`, `excel-xlsm-automation` und `jira-excel-testing` verwenden.
3. Nicht darauf verlassen, dass VS Code neue Skills im selben Chat automatisch entdeckt.
4. Skills direkt ueber ihre Workspace-Pfade lesen, bevor Anwendungscode geaendert wird.

## 16. Zielarchitektur

Bestehende Einstiegspunkte zunaechst erhalten:

- `build_excel_macro.py`
- `create_tickets.py`
- `diagnose_jira_auth.py`

Vorgesehene neue Kernmodule:

- `config_loader.py`
- `ticket_schema.py`
- `jira_client.py`
- `metadata_cache.py`
- `excel_workbook.py`
- `validators.py`
- `audit_log.py`

Spaetere Module:

- `sync_tickets.py`
- `generate_report.py`
- `comment_overview.py`

In Phase 1 nur Module erstellen, die tatsaechlich benoetigt werden. Keine leeren Platzhalterdateien ohne Nutzen anlegen.

## 17. Phase 1

Nach der Copilot-Einrichtung unmittelbar implementieren:

1. `config.yaml`
2. `config_loader.py`
3. `ticket_schema.py`
4. zentrale Felddefinitionen fuer alle fuenf Issue Types
5. exakte Pflichtfeldregeln
6. DAH als zunaechst einzig erlaubtes Projekt
7. Action-Feld `CREATE`, `UPDATE`, `IGNORE`
8. Validierung der Action-Werte
9. rueckwaertskompatible Behandlung der bestehenden `.env`-Konfiguration
10. sichere Erweiterung beziehungsweise Migration der bestehenden XLSM-Struktur
11. Erhalt vorhandener Daten und VBA-Makros
12. Standardspalten und vorbereitete optionale Spalten
13. Jira-Key-Hyperlinks
14. verstaendliche Validierungs- und Fehlermeldungen
15. automatisierte Offline-Tests

Phase 1 schafft die Grundlage fuer dynamische Jira-Metadaten. Die vollstaendige Metadaten-, Link-, Sprint-, Sync- und Reporting-Implementierung folgt in spaeteren Phasen.

## 18. Akzeptanzkriterien fuer Phase 1

Phase 1 ist erst abgeschlossen, wenn:

- `config.yaml` erfolgreich geladen wird.
- DAH ueber `allowed_projects` freigegeben ist.
- Unbekannte Projekte abgelehnt werden.
- `DRY_RUN` standardmaessig aktiv bleibt.
- Alle fuenf Issue Types im zentralen Schema enthalten sind.
- Die Pflichtfelder je Issue Type korrekt abgebildet sind.
- `CREATE`, `UPDATE` und `IGNORE` validiert werden.
- Bestehende `.env`-Nutzung nicht ohne Migration gebrochen wird.
- Die bestehende Excel-Schaltflaeche weiter funktioniert.
- Vorhandene Makros erhalten bleiben.
- Eine bestehende XLSM nicht ungefragt geloescht wird.
- Jira Keys als anklickbare Links erzeugt werden koennen.
- Offline-Tests ohne produktive Jira-Aufrufe bestehen.
- Kein Token in Logs oder Testausgaben erscheint.
- Bestehende Funktionen nicht regressiv beschaedigt werden.

## 19. Arbeitsweise

- Vor Aenderungen den bestehenden Code vollstaendig analysieren.
- Git-Status pruefen.
- Fremde oder bestehende Aenderungen bewahren.
- Modular und rueckwaertskompatibel arbeiten.
- Keine destruktiven Git-Befehle verwenden.
- Keine schreibenden Jira-Aufrufe ausfuehren.
- Tests muessen Jira-Aufrufe mocken.
- Keine Anwendungscodeaenderung vor Abschluss der Copilot-Einrichtungsdateien.
- Nach der Einrichtung nicht erneut nur planen, sondern Phase 1 implementieren.
- Rueckfragen nur bei einer nicht selbst loesbaren technischen Blockade stellen.
- Jira-Field-IDs, Board-IDs und Sprint-IDs spaeter dynamisch ermitteln; sie sind keine Nutzerfragen.
- Keine unnoetigen neuen Abhaengigkeiten installieren.
- `requirements.txt` nur bei tatsaechlichem Bedarf erweitern.
- Aenderungen in kleinen, ueberpruefbaren Schritten durchfuehren.

## 20. Abschlussnachweis

Nach Phase 1 ausgeben:

1. angelegte Copilot-Anpassungen
2. geaenderte und neue Anwendungsdateien
3. umgesetzte Anforderungen
4. Testergebnisse
5. Nachweis, dass keine produktiven Jira-Schreibzugriffe erfolgt sind
6. Rueckwaertskompatibilitaetsbewertung
7. bekannte offene technische Punkte
8. empfohlener naechster Implementierungsschritt
9. vollstaendiger Git-Diff beziehungsweise verstaendliche Diff-Zusammenfassung
