# Preview and approval reference

Present a complete preview before every write. A Collection preview authorizes neither item writes nor another Collection. An item preview authorizes only its exact digest and no changed, expanded, or stale variant.

## Complete preview example

```text
Operation: create Collection (preview only; no Zotero write performed)
Target Collection: “Microwave literature — August 2026” (new)
Candidates: 10 total | new: 7 | reused: 2 | conflicted: 1
Conflicts: DOI 10.0000/example is an existing candidate; no duplicate, merge, or overwrite is proposed.
Evidence/access state: 6 状态：全文已核查; 2 全文已获取，尚未深读; 2 状态：待获取全文
Proposed tags: generic profile: no domain-specific tags
Proposed Codex child note: data-codex-note="evidence-bounded-v1"; existing personal notes: preserved and excluded
Proposed linked PDFs: F:\research\approved-pdfs\paper-01.pdf and F:\research\approved-pdfs\paper-02.pdf only
Plan digest: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
Overwrite risk: none; conflicts remain unchanged pending a later user decision
Excluded actions: no DELETE, auto-merge, note overwrite, tag clearing, SQLite write, Web/Connector fallback, paywall bypass, or cache/temporary PDF attachment
Plan digest covers: target, candidate identities, classifications, evidence states, tags, child-note marker, final paths, and expected versions.
```

For a Collection creation preview, ask exactly:

> Confirm this exact Collection preview by replying: `Approve Collection plan <digest>`.

For the parent-item preview, ask exactly:

> Confirm this exact parent-item preview by replying: `Approve parent plan <digest>`.

After the parent result is verified and saved, present the separately generated child-object preview and ask exactly:

> Confirm this exact child-object preview by replying: `Approve child plan <digest>`.

Do not call `apply` unless the reply contains the matching approval type and exact digest. A parent approval never authorizes the child plan. Do not treat “yes,” “continue,” “do it,” or an earlier approval as consent. Split a request exceeding one Collection or 10 papers into separate previews and approvals.

## Apply and report

After matching approval, request one-time local authorization, apply exactly one plan, and read it back. Save the verified parent result before generating children. Request a fresh explicit approval and authorization for the child plan. Report the target, digest, created/reused/unchanged/failed counts, verification result, audit path, and any failure condition. On a partial result or a mismatch, report it as incomplete and stop; do not generate or apply children. On stale data, make a new narrow preview and obtain a new approval.
