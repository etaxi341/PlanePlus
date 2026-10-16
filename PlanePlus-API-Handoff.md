# PlanePlus Act-as: Übergabe an den MCP-Client (Vertrag v4)

## Neu: Anhänge inklusive Gast-/Intake-Uploads

1. Mit Admin-Key + Act-as `POST .../projects/{project_id}/work-items/{issue_id}/attachments/`
   mit `{"name":"nachricht.eml","type":"message/rfc822","size":12345}`.
   Dateitypen sind nicht auf Bilder beschränkt; positive Bytezahl bis `FILE_SIZE_LIMIT`.
2. Antwort `upload_data: {url,fields}` + `asset_id`: separaten Multipart-POST an
   `url` senden, **alle** `fields` unverändert und die Datei als letztes Formfeld
   `file` übertragen. **Keine Plane-/Admin-/Act-as-Header an Storage schicken.**
3. Nach erfolgreichem Transfer mit Plane-Headern
   `PATCH .../attachments/{asset_id}/`, Body `{"is_uploaded":true}` → 204.
   Metadaten-POST allein lädt noch keine Dateibytes hoch.
4. Liste über `GET .../attachments/`, Download über `GET .../attachments/{asset_id}/`
   (302 mit Audit-/Target-Headern); Storage-Datei ohne Plane-Header herunterladen.
   Löschen über `DELETE .../attachments/{asset_id}/`.

Alternativ vorab persönlich hochladen: `POST .../workspaces/{slug}/assets/` mit
Dateimetadaten und optional `project_id`; gleicher Multipart-Transfer. Anschließend
`PATCH .../assets/{asset_id}/` mit `{"is_uploaded":true,"issue_id":"ISSUE_UUID"}`.
Nur der Uploader darf binden; keine fremden Uploads oder Projekt-/Ticketwechsel.
`GET .../assets/{asset_id}/` liefert `{asset_id,asset_url,asset_name,asset_type}`.

Gäste benötigen aktive Workspace-/Projektmitgliedschaft und Ticket-Sichtbarkeit;
Intake-Zugriff richtet sich bei Triage nach dem Intake-Ersteller. Der Admin verleiht
keine Zusatzrechte. Discovery liefert `attachments` und `version:4`. Für diesen
vollständigen Ablauf Version **mindestens 4** prüfen und das Backend aktualisieren.
MCP muss den dreistufigen Upload selbst ausführen. Keine neue Migration/Variable.

## Neu: Intake-Enddatum und private Inlinebilder

**Gast-Intakes:** Aktive Workspace-/Projektgäste (Rolle 5) dürfen auch mit Act-as
Intake-Einträge erstellen. Listen/Details beachten `guest_view_all_features`, sonst
sind nur eigene Einträge sichtbar. Bearbeiten als Gast: nur eigener Name/Beschreibung;
keine Triage-/Status-, Prioritäts- oder Enddatumsänderung. Löschen nur eigener Einträge.
Der echte Administrator verleiht dem Zielgast keine zusätzlichen Rechte.

- Optionales Enddatum unter `issue.target_date` bei Intake-POST/PATCH, Format
  `YYYY-MM-DD`; `null` entfernt das Datum. Ohne Feld bleibt ein vorhandenes Datum
  bei PATCH unverändert. Lesbar unter `issue_detail.target_date`.
- `GET /api/v1/workspaces/{slug}/projects/{project_id}/work-items/{issue_id}/inline-assets/{asset_id}/?redirect=false`
  mit denselben Admin-Key-/Act-as-Headern wie der Body-GET. Ticketroute unterstützt
  Beschreibungs- und Kommentarbilder. Optional explizit:
  `.../work-items/{issue_id}/comments/{comment_id}/inline-assets/{asset_id}/`.
- JSON: `{asset_id,url,expires_in}`. `url` ist signiert, HTTPS und höchstens 300 Sekunden gültig.
  Bilddaten anschließend **ohne API-Key/Act-as-Header** vom Storage herunterladen.
- Ohne `redirect=false`: 302 mit signiertem `Location`; Audit-/Target-Header stehen
  auf dieser 302-Antwort, nicht notwendigerweise auf der danach geladenen Storage-Antwort.
- Asset muss aktuell im sichtbaren Ticket-/Kommentar-Body als Bild referenziert und
  korrekt zugeordnet sein. Kein Ausweichen auf generische Asset-/Admin-Endpunkte.
  Bei 403/404/503 niemals als Admin ohne Zielheader wiederholen.
- Discovery meldet `version:4`, `intake_target_date:true`, `ticket_inline_assets`
  sowie die tatsächlichen Methoden/Routen. Es bleibt bei `PLANEPLUS_ENABLE_ACT_AS=1`;
  kein neues Environment-Flag. Das aktualisierte Backend muss deployed sein.

Die vier priorisierten API-Lücken sind implementiert. Die folgenden Requests verwenden
den **persönlichen API-Key des tatsächlichen Administrators** in `X-API-Key`.

## Ablauf pro verifiziertem Chat-Benutzer

1. **Ohne Act-as-Header:** `GET /api/v1/workspaces/{slug}/act-as-capabilities/`.
   `version >= 4` und `enabled: true` für die neuen Funktionen prüfen. `operations` nennt die tatsächlich
   freigegebenen Methoden, Pfade und Body-Felder. Pfadkonverter ersetzen.
2. **Ohne Act-as-Header:**
   `GET /api/v1/workspaces/{slug}/act-as-users/resolve/?email={urlencoded-email}`.
   `id` sowie `is_active`, `is_bot`, `workspace_member.is_active`, `eligible` prüfen.
   Keine globalen Nutzerlisten und keine Email-Teilstringsuche verwenden.
3. Alle unterstützten fachlichen GET/HEAD/POST/PATCH/DELETE mit
   `X-PlanePlus-Act-As-User: {id}` ausführen. Optional
   `X-PlanePlus-Act-As-Reason: chatbot: {conversation-id}; {request-id}`.
   **GET wird jetzt wirklich als Zielbenutzer geprüft**, nicht als zentraler Admin.
4. Gegebenenfalls explizit und nur für öffentliche Projekte:
   `POST /api/v1/workspaces/{slug}/projects/{project_id}/join/`, Body `{}`,
   dieselben Header. 201 = beigetreten, 200 = bereits Mitglied. Der zentrale Admin
   braucht keine eigene Projektmitgliedschaft. Private/archivierte Projekte und
   entfernte Mitgliedschaften liefern 403. Keine Rollenparameter senden.
5. Danach reguläre Content-Aktion. Die Ziel-Projektrolle bleibt wirksam; ein
   erfolgreicher Join eines Gastes erzeugt keine Schreibrechte für Work Items.
6. `X-PlanePlus-Act-As-Audit-Id` speichern. **Niemals bei 400/403 Header entfernen
   und als Admin wiederholen.** Header pro Request isolieren, nicht zwischen Chats teilen.

## Zusätzlich freigegeben

- Work Items inklusive Datum `archived_at`/`null` und Löschung nach normalen Rechten.
- Kommentare/Links: Erstellung, Bearbeitung, Löschung nach Zielbenutzerrechten.
- States, Labels, Cycles, Modules: CRUD; Cycle-/Module-Archivierung und Work-Item-Zuordnungen.
- Estimates und Estimate-Points; Erstellen von Points über `{"estimate_points":[...]}`.
- Aktivierte Intakes (Standard-Intake/Triage wird nötigenfalls initialisiert), Relations im selben Projekt.
- Vollständiger Work-Item-/Intake-Attachment-Lifecycle und persönliche generische Uploads.
- Cycle-Transfers (`new_cycle_id`), Work-Item-GET per Identifier, eigene Stickies inklusive PUT.
- Projektmetadaten als berechtigter Projekt- oder Workspace-Admin mit Projektmitgliedschaft;
  keine Privacy-/Rollenänderungen.
- Passende GET/HEAD-Operationen und Suche/Mitgliederlisten, gemäß Capabilities.

**Noch keine vollständige MCP-Parität:** Keine Projekterstellung/-löschung,
Einladungen/Rechtevergaben, Module-Links, Work-Item-PUT/Upsert oder Pages.
Nicht angegebene Operationen werden explizit abgewiesen. Das Feature ist weiterhin
standardmäßig aus und wird mit `PLANEPLUS_ENABLE_ACT_AS=1` aktiviert.

Discovery bleibt bei deaktiviertem Feature für berechtigte Admins verfügbar.
404 bei Email-Auflösung bedeutet keine eindeutige sichtbare Workspace-Zuordnung;
409 bedeutet mehrdeutige Zuordnung. Ein `eligible`-Benutzer benötigt weiterhin
die konkreten Projekt-/Aktionsrechte.

Vollständiger Vertrag, HTTP-Beispiele, Sicherheitsgrenzen und Audit:
[docs/planeplus-admin-impersonation.md](docs/planeplus-admin-impersonation.md).
Die Änderungen sind lokal im Code; ein erfolgreicher Test ist keine Aussage über
ein bereits aktualisiertes Deployment oder einen externen MCP-Client.
