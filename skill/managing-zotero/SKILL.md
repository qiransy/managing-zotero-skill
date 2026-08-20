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
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' "$SKILL_DIR\scripts\zotero_local.py" status
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
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' "$SKILL_DIR\scripts\zotero_local.py" items --doi '10.0000/example'
```

## Evidence and PDFs

Keep metadata-only and abstract-only evidence visibly bounded. Do not label either as full-text verified, quote exact values, or fill precise constants from an unavailable article. Set the state to `待获取全文` and state what evidence is missing. Change a record to `状态：全文已核查` only after verifying the full text; use `状态：深度精读` only after a deep read.

Attach a linked PDF only after it already resides at its approved, final absolute D-drive path. Reject cache, temporary, download, C-drive, and future-to-be-moved paths. Do not attach first and move later. Pass the final approved parent directory through `--allowed-root`.

## Preview and approvals

Create a Collection preview first. Show the preview and obtain an explicit approval for that Collection creation alone. Then create a separate item-write preview for its actual collection key, show it, and obtain a second explicit approval for the item write. Do not infer either approval from “do it,” a prior approval, or approval of the other step.

Limit the default proposal to one Collection and at most 10 papers. If the request has more than 10 papers or more than one Collection, split it into previews and request approval for each. Do not silently expand, even when the user says not to ask again.

Show the complete preview schema in [safety-and-approval.md](references/safety-and-approval.md): target Collection; new/reused/conflicted counts; evidence states; tags, child note, and linked-PDF path; plan digest; overwrite risk; and excluded actions. Ask exactly:

> Confirm this exact Collection preview by replying: `Approve Collection plan <digest>`.

For an item plan, ask exactly:

> Confirm this exact item-write preview by replying: `Approve item plan <digest>`.

Use only the digest returned by the preview. Do not apply on paraphrased approval, stale data, or a changed candidate file.

```powershell
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' "$SKILL_DIR\scripts\zotero_local.py" preview-collection --name 'Example collection' --output 'D:\research\zotero-plans\collection.json'
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' "$SKILL_DIR\scripts\zotero_local.py" preview-items --input 'D:\research\zotero-plans\candidates.json' --collection-key 'COLLECT01' --profile generic --allowed-root 'D:\research\approved-pdfs' --output 'D:\research\zotero-plans\items.json'
```

## Apply, verify, and report

Request local authorization only after the corresponding preview and explicit approval. Treat it as one-time: never print, log, persist, reuse, or ask the user to paste the key. Apply only the approved plan with its exact digest, then read back the target and report created/reused/unchanged/failed results and audit-file locations. On partial failure, stale preview, changed Server ID, lock, or authorization error, stop; do not retry automatically, widen the operation, or fall back to another write channel. Generate a fresh narrow preview after the user resolves the condition.

```powershell
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' "$SKILL_DIR\scripts\zotero_local.py" apply --plan 'D:\research\zotero-plans\items.json' --approval-digest '<digest-from-the-explicit-approval>' --confirm-user-approved --audit-dir 'D:\research\zotero-audit'
```

Read [the local API reference](references/zotero-local-api.md) before capability checks, authorization, or interpreting HTTP errors. Read [the approval reference](references/safety-and-approval.md) before presenting or applying a preview.
