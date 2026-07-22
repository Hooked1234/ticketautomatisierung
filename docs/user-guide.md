# Kurzhandbuch

## 1. Start und Modus

TicketPilot startet ohne Jira im sicheren Demomodus. Die Statusseite zeigt Modus, Projekt,
Cachezustand und Dry-Run-Schalter. Solange kein echter Adapter konfiguriert ist, können alle
Arbeitsabläufe lokal ausprobiert werden.

## 2. Ticket erfassen

Unter **Tickets** wird ein neuer Entwurf angelegt. Nach Wahl des Issue Types zeigt der Editor nur
zulässige Felder. Story und Bug benötigen Komponenten; Epic benötigt einen Epic-Namen. Sprint ist
für Epic gesperrt. Anhänge und Links bleiben lokale Referenzen, bis Jira angebunden ist.

## 3. Vorschau und Verarbeitung

**Vorschau** validiert das Ticket und zeigt CREATE-/UPDATE-Differenzen. Bei UPDATE bleibt ein
leeres Feld unverändert. Nur die exakte Eingabe `<CLEAR>` entfernt später einen optionalen Wert.
Gesperrte Felder werden nicht als Änderung angeboten. Im Dry Run entsteht ausschließlich ein
Audit-/Ergebnisdatensatz.

## 4. Dashboard und Kommentare

Das Dashboard wertet im Demomodus lokale synthetische Tickets read-only aus. Die
Kommentarübersicht zeigt Autor sowie Erstellungs-/Änderungszeit ohne KI-Zusammenfassung.

## 5. Einstellungen

Konfiguration und Cache können lokal zurückgesetzt werden; Tokens werden getrennt im sicheren
Credential-Store verwaltet. Das Auditprotokoll enthält keine Tokens oder Authorization-Header.
