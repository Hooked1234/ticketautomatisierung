# Sicherheitskonzept

## Sicherheitsinvarianten

- `dry_run` ist beim ersten Start und nach ungültiger Konfiguration immer aktiv.
- Ticketlöschungen und Jira-Transitions sind nicht Teil der Anwendung.
- Ein bestehendes Ticket darf weder Projekt, Issue Type, Reporter, Jira Key noch Jira-Status
  ändern.
- Ein UPDATE benötigt Validierung, sichtbaren Feld-Diff, explizite Bestätigung und unmittelbar
  vor dem späteren PUT eine Prüfung des Jira-`updated`-Zeitstempels.
- Schreibende POST-Anfragen dürfen nach unklarer Antwort niemals automatisch wiederholt werden.
- Ein Fehler in einem Ticket oder Anhang darf die Verarbeitung anderer Tickets nicht stoppen.
- Logs, Fehlermeldungen und Auditdaten werden vor Speicherung bereinigt.

## Zugangsdaten

Produktive Tokens werden über `keyring` im Windows-Anmeldeinformationsmanager abgelegt. Ist kein
sicherer Credential-Backend verfügbar, bleibt ein eingegebener Token nur für die laufende Sitzung
im Speicher. TicketPilot schreibt Tokens weder in SQLite noch in JSON, `.env`, Logs oder Exporte.

TicketPilot lädt keine `.env`-Datei und übernimmt `JIRA_TOKEN` nicht automatisch aus der
Prozessumgebung. Die mitgelieferte `.env.example` ist ausschließlich eine gekennzeichnete
Vorlage für einen späteren, ausdrücklich auszulösenden Migrationsschritt; URL und Tokenwert sind
absichtlich ungültig. Die aktuelle App liest daraus keine Zugangsdaten.

## Offlinezustand

Der aktuelle `LocalDemoGateway` arbeitet ausschließlich mit synthetischen Daten. Der
`DisabledJiraGateway` bricht jeden Integrationsversuch mit einer klaren Konfigurationsmeldung ab.
Damit kann weder versehentlich gelesen noch geschrieben werden.
