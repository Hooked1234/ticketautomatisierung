# Vertrag für den nächsten Jira-Integrationsschritt

Die Oberfläche und Anwendungslogik sprechen ausschließlich mit den Ports aus
`ticketpilot.application`. Der spätere Jira-Data-Center-Adapter implementiert diese Ports und
übersetzt REST-Antworten an der Infrastrukturgrenze in interne Modelle.

## Read-Operationen

- Serverinformationen und authentifizierten Nutzer laden
- Projekt-, Create-Meta-, Feld-, Prioritäts-, Komponenten- und Versionsmetadaten laden
- Linktypen, Boards sowie aktive/zukünftige und abgeschlossene Sprints paginiert laden
- Personen, Epics, Tickets, Products and Services und Accounts bedarfsgesteuert suchen
- einzelne Tickets inklusive `updated` laden
- Reporting per JQL und Kommentare ausschließlich lesend laden

## Write-Operationen

- CREATE ohne automatische Wiederholung
- UPDATE erst nach erneuter `updated`-Prüfung
- Anhänge nach eindeutig erfolgreichem CREATE oder geladenem UPDATE-Ziel einzeln ergänzen
- Issue Links ausschließlich ergänzen

Keine Implementierung darf Delete-Endpunkte, Transitions oder Änderungen an gesperrten Feldern
anbieten. Alle Tests des Adapters müssen HTTP vollständig mocken und synthetische Fixtures nutzen.
