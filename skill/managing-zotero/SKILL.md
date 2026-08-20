---
name: managing-zotero
description: Safely inspect, deduplicate, organize, enrich, and write Zotero literature libraries through the Zotero 10 local API with preview-before-write approval, one-time local authorization, read-back verification, evidence-level controls, and optional research-domain profiles. Use when the user asks to manage Zotero, add or update papers, organize Collections, attach approved PDFs, enrich manually added literature, or prepare Zotero records from literature research.
---

# Managing Zotero

Safely prepare a small, evidence-bounded Zotero change through Zotero 10's loopback local API. Keep discovery read-only until the user explicitly approves one exact preview; treat approval of research assistance as distinct from approval of every Zotero write.

## Scope and non-goals

Use the local CLI only for read-only discovery, conservative duplicate classification, preview generation, and approved Collection or item writes. Do not scan the whole library in the background. Do not DELETE, write SQLite, use the Zotero Web API or a Connector as a write fallback, auto-merge records, clear tags, bypass a paywall, install on the C drive, or mutate Zotero without this flow.

Use the CLI only after a candidate has been verified. Do not present an ordinary literature-search result as a verified Zotero candidate.

## Preflight and profile

1. Confirm that Zotero is running and that its local API is enabled. Resolve `$SKILL_DIR` to the installed `managing-zotero` Skill directory before running any example; do not run a command with the literal placeholder unchanged.
2. Run the read-only capability check. Before the first real write, remind the user to make or confirm a Zotero backup.

```powershell
$SKILL_DIR = (Resolve-Path 'C:\path\to\installed\managing-zotero').Path
python "$SKILL_DIR\scripts\zotero_local.py" status
```

Default to `generic`. Select `microwave-spectroscopy` only when the user or the active research context identifies microwave rotational spectroscopy; then read [the microwave profile](references/profiles/microwave-spectroscopy-schema.md). Do not add microwave-specific tags under the generic profile.

## Route research before Zotero

Route literature work to installed specialist Skills before creating CLI candidates:

| Need | Route first | Hand to the local CLI only after |
|---|---|---|
| Find and verify papers or identifiers | `paper-lookup` | DOI/identifier, bibliographic metadata, provenance, and evidence level are checked |
| Broad, systematic, or gap-oriented search | `literature-review` | selected candidate records are verified |
| Deep read of one paper | `nature-paper-card` | full-text claims and extraction provenance are verified |
| Validate references and identifiers | `citation-verification` | citation details and duplicate identity are checked |
| Draft evidence-bounded scholarly notes | `scientific-writing` | supplied claims have a stated evidence boundary |

Use one of two entry modes. For Codex-found literature, collect only verified candidate records from the routed work. For user-specified existing Collections or items, search only the named Collection, item key, DOI, or query. Never use a background whole-library scan to discover either.

## Discover and classify without writing

Use `collections --query`, `items --doi`, or `items --query` for the narrow target. Treat an exact DOI match as a reuse candidate, not permission to duplicate, replace, merge, or overwrite. For incomplete records, classify the result conservatively as `new`, `reused`, or `conflicted`; report the reason and ask for a decision for each conflict. Preserve all personal notes. Create only a separate Codex child note bearing the exact stable marker `data-codex-note="evidence-bounded-v1"`; never alter an unmarked personal note.

```powershell
python "$SKILL_DIR\scripts\zotero_local.py" items --doi '10.0000/example'
```

## Evidence and PDFs

Keep metadata-only and abstract-only evidence visibly bounded. Do not label either as full-text verified, quote exact values, or fill precise constants without verifying them in the article. Track reading depth separately from PDF availability: use `待获取全文` when no accessible linked PDF exists, and `全文已获取，尚未深读` when a final linked PDF exists but only metadata or the abstract has been checked. Change a record to `状态：全文已核查` only after verifying the full text; use `状态：深度精读` only after a deep read.

Attach a linked PDF only after it already resides under an approved final absolute directory. Reject cache, temporary, unapproved-root, browser-managed incomplete-download, and future-to-be-moved paths. Do not attach first and move later. A user-approved `Downloads` directory may be treated as final when the file will remain there. Pass each final approved parent directory through `--allowed-root`; approved roots may be on any local drive.

## Preview and approvals

Create a Collection preview first when the target does not already exist. Show it and obtain approval for that Collection creation alone. Item creation then uses two separately approved plans because Zotero 10 can fail when a new local item is sent with a client-generated key: the parent plan creates or reuses only bibliographic parents, and the child plan creates notes and linked attachments only after the verified parent result supplies Zotero's actual keys. New objects must omit both `key` and `version`; existing reused parents must retain their positive `version`.

Do not generate the child preview until the parent write has succeeded, read back cleanly, and written its `--result-output`. The child preview must bind the unchanged candidate input, exact parent plan digest, verified parent result, current parent versions, target Collection, child-note contents, and final PDF paths. Parent and child plans each require their own preview, exact-digest approval, and local authorization. Do not infer one approval from the other.

Limit the default proposal to one Collection and at most 10 papers. If the request has more than 10 papers or more than one Collection, split it into previews and request approval for each. Do not silently expand, even when the user says not to ask again.

Show the complete preview schema in [safety-and-approval.md](references/safety-and-approval.md): target Collection; new/reused/conflicted counts; evidence states; tags, child note, and linked-PDF path; plan digest; overwrite risk; and excluded actions. Ask exactly:

> Confirm this exact Collection preview by replying: `Approve Collection plan <digest>`.

For a parent item plan, ask exactly:

> Confirm this exact parent-item preview by replying: `Approve parent plan <digest>`.

For a child-object plan, ask exactly:

> Confirm this exact child-object preview by replying: `Approve child plan <digest>`.

Use only the digest returned by the preview. Do not apply on paraphrased approval, stale data, or a changed candidate file.

```powershell
python "$SKILL_DIR\scripts\zotero_local.py" preview-collection --name 'Example collection' --output 'F:\research\zotero-plans\collection.json'
python "$SKILL_DIR\scripts\zotero_local.py" preview-items --input 'F:\research\zotero-plans\candidates.json' --collection-key 'COLLECT01' --profile generic --allowed-root 'F:\research\approved-pdfs' --output 'F:\research\zotero-plans\parents.json'
```

## Apply, verify, and report

Request local authorization only after the corresponding preview and explicit approval. Ask the user to choose one-time **Allow**, not **Always Allow**. Never print, log, persist, reuse, or ask the user to paste the key. If Zotero returns a remembered authorization, stop and ask the user to clear write authorizations before continuing with one-time approval. Apply only the approved plan with its exact digest, then read back the target and report created/reused/unchanged/failed results and audit-file locations. On partial failure, stale preview, changed Server ID, lock, or authorization error, stop; do not retry automatically, widen the operation, or fall back to another write channel. Generate a fresh narrow preview after the user resolves the condition.

```powershell
python "$SKILL_DIR\scripts\zotero_local.py" apply --plan 'F:\research\zotero-plans\parents.json' --approval-digest '<approved-parent-digest>' --confirm-user-approved --audit-dir 'F:\research\zotero-audit' --result-output 'F:\research\zotero-plans\parent-result.json'
python "$SKILL_DIR\scripts\zotero_local.py" preview-children --input 'F:\research\zotero-plans\candidates.json' --parent-plan 'F:\research\zotero-plans\parents.json' --parent-result 'F:\research\zotero-plans\parent-result.json' --collection-key 'COLLECT01' --profile generic --allowed-root 'F:\research\approved-pdfs' --output 'F:\research\zotero-plans\children.json'
python "$SKILL_DIR\scripts\zotero_local.py" apply --plan 'F:\research\zotero-plans\children.json' --approval-digest '<approved-child-digest>' --confirm-user-approved --audit-dir 'F:\research\zotero-audit'
```

Read [the local API reference](references/zotero-local-api.md) before capability checks, authorization, or interpreting HTTP errors. Read [the approval reference](references/safety-and-approval.md) before presenting or applying a preview.
