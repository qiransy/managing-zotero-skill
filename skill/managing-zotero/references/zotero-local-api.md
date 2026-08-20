# Zotero local API operating reference

Use only Zotero's loopback local API at `http://127.0.0.1:23119/api/`. Keep the client on loopback HTTP and use only the CLI's GET and POST paths. Never substitute the Web API, a Connector, SQLite, or another write channel.

## Detect capability before any write path

Call `GET /api/` through `zotero_local.py status`. Inspect these response headers:

| Header | Use |
|---|---|
| `Zotero-API-Version` | Confirm API compatibility. API `3` is the verified example, not a value to assume without probing. |
| `Zotero-Schema-Version` | Confirm it is a positive decimal schema version. Schema `44` is the verified example, not a hardcoded requirement. |
| `Zotero-Server-ID` | Bind authorization to this running Zotero instance; never display or persist it. |
| `Last-Modified-Version` | Record the library/object version for stale-preview protection. |

Treat a missing or incompatible API version, an invalid schema version, or a missing Server ID as read-only. Do not request authorization in that state.

## Allowed endpoints and headers

Use narrow reads only:

| Purpose | Endpoint |
|---|---|
| Capability probe | `GET /api/` |
| One-time local authorization | `POST /api/local/authorize` |
| Narrow Collection reads and approved create | `GET` or approved `POST /api/users/0/collections` |
| Narrow item reads and approved upsert | `GET` or approved `POST /api/users/0/items` |

Send `Zotero-API-Version: 3` and a User-Agent on each request. For `POST /api/local/authorize`, send JSON and `Zotero-Server-ID`. For an approved write, send JSON, `Zotero-API-Key`, `Zotero-Server-ID`, and `If-Unmodified-Since-Version` when the preview supplies a version. Let the CLI obtain and consume the one-time API key; never print, save, or manually reuse it. Choose one-time **Allow**, not **Always Allow**. A remembered authorization is intentionally rejected by this approval-bound workflow.

For newly created items, notes, and attachments, omit both `key` and `version` and let Zotero return the actual key. Zotero 10 local API builds can fail before save when a new object carries a client-generated key with `version: 0`. Create bibliographic parents first, save and verify their returned keys, and create child notes/attachments only in a second approved request that references those actual parent keys. Existing reused parents keep their key and positive version.

## Status handling

| HTTP status | Meaning in this workflow | Required action |
|---|---|---|
| 200 | Read, authorization, or write request succeeded | For a write, read back the affected target before reporting success. |
| 401 / 403 | Authorization rejected or expired | Stop. Do not retry automatically or fall back; make a fresh approved attempt only after the user resolves it. |
| 409 | Zotero library is locked | Stop and do not retry automatically. Ask the user to resolve the lock, then create a fresh narrow preview. |
| 412 / 428 | Preview/version precondition is stale | Stop. Re-read narrowly and generate a new preview; obtain new explicit approval. |
| 405 / 501 | Local write authorization or write method is unavailable | Latch into read-only mode for this run; report the condition and do not use another write channel. |

Treat other non-2xx responses as a local API error. Preserve the error context without exposing secrets, and stop rather than guessing or broadening the request.

## CLI mapping

Resolve `$SKILL_DIR` to the installed Skill directory before execution. Use the bundled interpreter and the local script, never a shell alias or a copied script:

```powershell
$SKILL_DIR = (Resolve-Path 'C:\path\to\installed\managing-zotero').Path
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' "$SKILL_DIR\scripts\zotero_local.py" status
```

The CLI redacts the Server ID in `status`, accepts no raw authorization command, and permits only `create_collection`, `upsert_items`, and `create_children` plans. An `apply` requires the preview file, its exact SHA-256 digest, `--confirm-user-approved`, and an absolute audit directory. Parent `apply` should also use `--result-output`; `preview-children` requires that verified result and its matching parent plan.
