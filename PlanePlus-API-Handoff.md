# PlanePlus Act-as: Übergabe an den MCP-Client (Vertrag v2)

Die vier priorisierten API-Lücken sind implementiert. Die folgenden Requests verwenden
den **persönlichen API-Key des tatsächlichen Administrators** in `X-API-Key`.

## Ablauf pro verifiziertem Chat-Benutzer

1. **Ohne Act-as-Header:** `GET /api/v1/workspaces/{slug}/act-as-capabilities/`.
   `version >= 2` und `enabled: true` prüfen. `operations` nennt die tatsächlich
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
- Initialisierte Intakes, Relations im selben Projekt, Work-Item-Attachment-Lifecycle.
- Projektmetadaten als berechtigter Projektadmin; keine Privacy-/Rollenänderungen.
- Passende GET/HEAD-Operationen und Suche/Mitgliederlisten, gemäß Capabilities.

**Noch keine vollständige MCP-Parität:** Keine Projekterstellung/-löschung,
Einladungen/Rechtevergaben, Cycle-Transfers, Module-Links, PUT/Upsert oder Pages.
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
