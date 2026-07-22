# ADR 0001: Lokale PySide6-Desktoparchitektur

- Status: akzeptiert
- Datum: 2026-07-21

## Kontext

Die Anwendung ist eine persönliche, Windows-first Ticketanwendung. Ein vorhandenes Repository
oder Excel-Frontend steht nicht zur Verfügung. Die echte Jira-Verbindung wird separat ergänzt.
Persönliche Zugangsdaten dürfen nicht in Konfigurationsdateien oder SQLite gespeichert werden.

## Entscheidung

TicketPilot besteht aus vier strikt gerichteten Schichten:

1. `domain` enthält Modelle und unveränderliche Fachregeln.
2. `application` orchestriert Vorschau, Validierung, Synchronisation und Reporting über Ports.
3. `infrastructure` implementiert lokale SQLite-Persistenz, Cache und Credential-Adapter.
4. `ui` enthält ausschließlich PySide6-Präsentationslogik.

Der Offline-/Demomodus ist ein vollwertiger Adapter. Ein echter Jira-Data-Center-Adapter kann im
nächsten Schritt hinter denselben Ports ergänzt werden. Schreibvorgänge bleiben standardmäßig im
Dry Run und benötigen zusätzlich eine explizite Bestätigung. Tokens werden ausschließlich über
den Windows-Anmeldeinformationsmanager beziehungsweise dessen `keyring`-Adapter gespeichert.

## Konsequenzen

- Der Kern lässt sich ohne PySide6 und ohne Netzwerk testen.
- Die Oberfläche kann heute vollständig bedient werden, ohne Jira zu imitieren oder produktive
  Schreibzugriffe auszuführen.
- Jira-spezifische Metadaten und HTTP-Details bleiben an der Infrastrukturgrenze.
- Excel ist bewusst kein Frontend und keine Laufzeitabhängigkeit dieses Produkts.
