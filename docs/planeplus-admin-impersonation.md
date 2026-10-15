# Sichere API-Impersonation in PlanePlus

## Aktivierung

Das Feature ist standardmäßig **ausgeschaltet**. Zuerst die Datenbankmigration
`db.0123_impersonation_audit` mit dem normalen Migrator ausführen, dann im
Backend-Environment setzen und den API-Prozess neu starten:

```dotenv
PLANEPLUS_ENABLE_ACT_AS=1
```

- CLI/Swarm-Installation: Variable in `plane.env` setzen; das Compose-Template
  reicht sie an das Backend weiter.
- Dockge: Variable in der bestehenden `.env` setzen.
- Lokales Compose: Variable in der Root-`.env` setzen.
- Direkter Backend-Start bzw. Root-`docker-compose.yml`: `apps/api/.env` bzw.
  Prozess-Environment verwenden.
- AIO: `-e PLANEPLUS_ENABLE_ACT_AS=1` übergeben.

`0` oder eine fehlende Variable deaktiviert neue delegierte Schreibvorgänge.
Bestehende Audit-/Provenienzeinträge bleiben lesbar. Branch- und Docker-Hub-
Publishing-Einstellungen werden durch dieses Feature nicht geändert: **master bleibt master**.

## HTTP-Vertrag

Die Authentifizierung bleibt der bestehende persönliche Plane-API-Key des
Administrators. Keine fremden Tokens, Sessions oder Passwörter verwenden.

```http
X-API-Key: plane_api_<administrator-token>
X-PlanePlus-Act-As-User: <target-user-uuid>
X-PlanePlus-Act-As-Reason: chatbot: conversation-123; request-456
Content-Type: application/json
```

- `X-PlanePlus-Act-As-User` aktiviert die Delegation für unterstützte Schreiboperationen.
- Der Grund ist optional, maximal 500 Zeichen, ohne CR/LF/NUL. Eine Kontext-ID
  kann dort angegeben werden. **Keine Zugangsdaten oder sensiblen Chat-Inhalte hineinlegen.**
- Eine Begründung ohne Ziel-Header ist bei Schreiboperationen ein Fehler.
- Ohne beide Header bleibt die API unverändert.
- GET, HEAD und OPTIONS ignorieren beide Header: Sie lesen **immer als tatsächlicher API-Aufrufer**.
- Die JSON-Antwort eines Content-Endpoints behält ihre bisherige Struktur.
- Abgeschlossene delegierte Anfragen enthalten `X-PlanePlus-Act-As-Audit-Id`.
  Nach erfolgreicher Zielauflösung wird außerdem `X-PlanePlus-Act-As-User` zurückgegeben.
- Die unterstützten Schreiboperationen dokumentieren die Header auch im OpenAPI-Schema.

### Normaler Kommentar

```sh
curl --fail-with-body -X POST \
  'https://planeplus.example/api/v1/workspaces/acme/projects/PROJECT_UUID/work-items/ITEM_UUID/comments/' \
  -H "X-API-Key: $ADMIN_API_KEY" \
  -H 'Content-Type: application/json' \
  --data '{"comment_html":"<p>Kommentar des API-Aufrufers.</p>"}'
```

### Kommentar im Namen eines Benutzers

```sh
curl --fail-with-body -i -X POST \
  'https://planeplus.example/api/v1/workspaces/acme/projects/PROJECT_UUID/work-items/ITEM_UUID/comments/' \
  -H "X-API-Key: $ADMIN_API_KEY" \
  -H "X-PlanePlus-Act-As-User: $TARGET_USER_UUID" \
  -H 'X-PlanePlus-Act-As-Reason: chatbot: conversation-123; request-456' \
  -H 'Content-Type: application/json' \
  --data '{"comment_html":"<p>Fachlich vom Zielbenutzer verfasst.</p>"}'
```

Bei Erfolg: HTTP 201; `actor` und `created_by` zeigen auf den Zielbenutzer.
Beim Bearbeiten bleibt der ursprüngliche Ersteller erhalten; `updated_by` zeigt
auf den effektiven Bearbeiter. Der Administrator steht separat im Audit-Log.
Ein neuer Kommentar und seine zugehörige Description erhalten dieselbe Autorenzuordnung.

## Unterstützte Endpoints

Alle folgenden Pfade beginnen mit `/api/v1/workspaces/{slug}/projects/{project_id}/`:

| Methode | Suffix                                        | Aktion               |
| ------- | --------------------------------------------- | -------------------- |
| POST    | `work-items/`                                 | Work Item erstellen  |
| PATCH   | `work-items/{item_id}/`                       | Work Item bearbeiten |
| POST    | `work-items/{item_id}/comments/`              | Kommentar erstellen  |
| PATCH   | `work-items/{item_id}/comments/{comment_id}/` | Kommentar bearbeiten |
| POST    | `work-items/{item_id}/links/`                 | Link erstellen       |
| PATCH   | `work-items/{item_id}/links/{link_id}/`       | Link bearbeiten      |

Die bestehenden `issues/`-Aliasrouten derselben Handler unterstützen diese
Operationen ebenfalls. PUT/Upsert und DELETE sind absichtlich **nicht freigegeben**.

Pages haben in diesem Fork keine öffentliche REST-Schreib-API. Sessionbasierte
Page-Endpoints werden nicht zweckentfremdet. Ebenfalls ausgeschlossen sind Token-,
Benutzer-, Rollen-, Passwort-, Workspace- und Projektverwaltung, Dateien,
Zyklen, Module und andere nicht explizit freigegebene Methoden/Endpoints.
Mit Delegationsheader werden solche bestehenden Schreiboperationen mit HTTP 403
abgewiesen, nicht stillschweigend als Administrator ausgeführt.

### Zulässige Nutzdaten

- Work Items: `name`, `description_html`, `description_binary`, `priority`,
  `state`, `parent`, `assignees`, `labels`, `start_date`, `target_date`,
  `estimate_point`, `type_id`, `sort_order`, `external_source`, `external_id`.
- Kommentare: `comment_html`, `comment_json`, `access`, `external_source`, `external_id`.
- Neue Links: `title`, `url`; Link-Updates zusätzlich `metadata`.

Insbesondere keine `created_by`, `updated_by`, `actor`, `created_at`,
`deleted_at`, Workspace-/Projekt-/Work-Item-Zuordnungen im Body senden.
Nicht freigegebene Felder liefern HTTP 400. Bestehende Serializer validieren
die normalen Inhalte und Beziehungen; der Header ersetzt keinen Validator.

## Berechtigungsmodell

1. Der API-Key muss gültig, aktiv und nicht abgelaufen sein; sein Besitzer muss
   aktiv sein. Bot-/Service-Tokens und Bot-Aufrufer sind ausgeschlossen.
2. Der tatsächliche Aufrufer muss nach den bestehenden Plane-Rechten
   Instance-Admin oder aktiver Workspace-Admin des angegebenen Workspaces sein.
   `is_superuser` allein ersetzt diese Zuordnung nicht.
3. Ein workspacegebundener Token bleibt auch für einen Instance-Admin auf diesen
   Workspace beschränkt. Ein Workspace-Admin darf nicht in andere Workspaces delegieren.
4. Das Ziel muss ein aktiver regulärer Benutzer und aktives Workspace-Mitglied
   sein. Es benötigt selbst eine aktive Projektmitgliedschaft und die normalen
   Rechte der konkreten Operation. Unterschiedliche Projektrollen sind zulässig;
   sie werden nicht erhöht. Work Items/Links benötigen nach der öffentlichen
   API Projektrolle Member oder Admin. Kommentare unterstützen auch berechtigte Gäste.
5. Für Gäste gelten zusätzlich die normalen Sichtbarkeitsgrenzen des Work Items.
   Kommentar-Updates benötigen Erstellerrechte oder entsprechende Projekt-/
   Workspace-Adminrechte des **Zielbenutzers**, nicht des Aufrufers.
6. Workspace, Projekt, Work Item, Kommentar/Link und Typ-Zuordnungen werden
   serverseitig gegeneinander geprüft. Archivierte Projekte/Work Items und
   fremde Ressourcen sind für diese Delegation gesperrt.

## Audit und abrufbare Transparenz

`planeplus_impersonation_audit` enthält einen separaten Eintrag pro abgeschlossener
delegierter Anfrage: tatsächlicher Aufrufer, Ziel-UUID, Token-UUID (nicht dessen
Geheimnis), Workspace/Projekt, Aktion, Ressource/Work Item, Zeitpunkt, Grund,
HTTP-Status und Erfolg bzw. Fehlercode. Wenn Authentifizierung oder Zielauflösung
scheitert, bleiben nicht verlässlich ermittelbare Identitäten leer; die angefragte
Zielkennung wird trotzdem festgehalten. Anfragen außerhalb der öffentlichen API
werden an der Grenze abgewiesen, ohne deren API-Key als verifiziert auszugeben.

Erfolgreiche Content-Änderung und Audit werden **in derselben Datenbanktransaktion**
gespeichert. Bei Fehlern werden Content-Änderungen zurückgerollt und das negative
Ergebnis separat gespeichert. Ohne verfügbares Audit keine erfolgreiche delegierte
Änderung: HTTP 503. Bei vollständigem Datenbankausfall kann auch der abgewiesene
Versuch nicht persistent protokolliert werden; dafür gibt es zusätzlich Server-Logs.

ORM-Sperren und ein PostgreSQL-Trigger verbieten UPDATE, DELETE und TRUNCATE.
Es gibt keine mutierende Audit-API, keine Soft-Deletion und keine kaskadierenden
Fremdschlüssel: Benutzer-/Content-Löschungen entfernen nicht die Identitäts-Snapshots.
Ein Datenbank-Superuser/Tabelleneigentümer kann technisch Schutzmechanismen ändern;
dies ist kein extern versiegeltes WORM-Archiv. Für stärkere Betriebsgrenzen getrennte
Migrations-/Runtime-Rollen einsetzen und dem Runtime-Account am Audit nur SELECT/INSERT geben.

Abruf per normalem API-Key, auch bei ausgeschaltetem Feature:

```http
GET /api/v1/workspaces/{slug}/act-as-audit/{audit-id}/
GET /api/v1/workspaces/{slug}/projects/{project_id}/work-items/{item_id}/act-as-provenance/
```

Workspace-/Instance-Admins können im Detail auch Grund, Token-ID und negative
Ergebnisse sehen. Normale aktive Content-Mitglieder können erfolgreiche Provenienz
für sichtbare Inhalte abrufen, aber keine privaten Gründe/Token-IDs. Die Liste ist
nach den bestehenden Plane-Konventionen paginiert. Interne Kommentare werden Gästen
nicht über Provenienz offengelegt. Die normale Autorendarstellung bleibt beim
Zielbenutzer; diese abrufbare Provenienz ist der zusätzliche Hinweis auf Stellvertretung.
Eine neue Frontend-Badge-Komponente wird nicht eingeführt.

Bestehende Activity- und Webhook-Pfade erhalten den Zielbenutzer als fachlichen
Actor. Delegierte Events werden erst nach dem Commit an die bestehenden Celery-
Tasks übergeben; Kommentare lösen dabei die üblichen Benachrichtigungen aus.
Delegierte Description-Änderungen erzeugen neue Aktivitäten statt alte Aktivitäten
desselben Benutzers zusammenzufassen. Historische Aktivitäten werden nicht umgeschrieben.
Broker-Ausfälle nach dem Commit werden geloggt; es gibt hier keinen neuen transaktionalen
Outbox-/Retry-Dienst und keine Zustellgarantie über die bestehenden Plane-Tasks hinaus.

## Fehlervertrag

Fehler enthalten `error_code` und gewöhnlich `error`:

- **403**: `act_as_disabled`, `act_as_api_key_required`, `act_as_admin_required`,
  `act_as_workspace_forbidden`, `act_as_target_not_workspace_member`,
  `act_as_target_permission_denied`, `act_as_resource_forbidden`, `act_as_operation_not_supported`.
- **400**: `act_as_invalid_user`, `act_as_invalid_reason`, `act_as_invalid_payload`,
  `act_as_fields_not_supported`; außerdem die bestehenden Serializerfehler.
- **503**: `act_as_audit_unavailable`; keine erfolgreiche delegierte Änderung.

Ungültige eigentliche API-Keys behalten die bestehende Authentifizierungsfehlerantwort.

## MCP-/Chatbot-Integration und Grenzen

Der offizielle MCP-Server muss für **jede einzelne unterstützte Schreibanfrage**
die obigen Header an den Plane-HTTP-Client weitergeben. Falls seine Version keine
benutzerspezifischen Header unterstützt, ist dafür ein Client-Hook/Adapter nötig;
dieses PlanePlus-Feature verändert nicht automatisch einen externen MCP-Server.

Ziel-UUID aus der verifizierten Chat-Benutzeridentität ableiten, nicht einem beliebigen
LLM-Argument oder Chat-Text vertrauen. Den Admin-Key ausschließlich serverseitig
halten. Keine global veränderliche Header-Map zwischen mehreren Chats teilen:
Header pro Request bzw. isoliertem Client setzen. Audit-ID für Nachvollziehbarkeit
speichern. Bei 403/400 niemals den Header entfernen und dieselbe Aktion als Admin
wiederholen. Keine Zielbenutzer-Tokens erzeugen.

Die bisherigen Import-/Autorenfelder normaler Upstream-API-Aufrufe bleiben zur
Kompatibilität unverändert. Dieses Feature ist keine nachträgliche Auditierung
historischer oder headerloser Import-Aufrufe; für nachvollziehbare Stellvertretung
immer den neuen Headervertrag verwenden.

## Tests und Erweiterung

```sh
docker compose -f docker-compose-test.yml run --rm --build api-tests pytest \
  plane/tests/contract/api/test_impersonation.py \
  plane/tests/unit/utils/test_act_as_context.py \
  plane/tests/unit/middleware/test_api_authentication.py \
  plane/tests/unit/views/test_swg_authentication.py \
  plane/tests/unit/views/test_issue_link.py
```

Die Tests installieren den echten PostgreSQL-Auditschutz auch unter dem bestehenden
`--nomigrations`-Testmodus. Nur für pytest-Teardown wird der Testdatenbank-Trigger entfernt.
Paralleltests verwenden getrennte Datenbankverbindungen und echte HTTP-Testclients.

Neue Schreiboperationen explizit mit `act_as_content` registrieren, zulässige Felder,
Ressourcenbindung und normale Objektberechtigungen prüfen, gegebenenfalls die enge
Modellfreigabe in `plane/utils/act_as.py` erweitern und Sicherheitsregressionen ergänzen.
Ein neuer Endpoint erbt die Delegation nicht automatisch. Die ursprünglichen
DRF-/Django-Requests, Sessions, API-Keys und Throttles bleiben beim echten Aufrufer;
nur die geprüften Handler/Permissions erhalten eine lokale Content-Request-Fassade.
Autorenfelder werden über einen mit `finally` zurückgesetzten `ContextVar` gesetzt.
