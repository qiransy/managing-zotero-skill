# Consolidated Tasks 5–9 safety fix report

## Scope

- Base: `13e3e60f8ffef7510c323cdddb020ef7c8d75202`
- No real Zotero process, external network, C-drive installation, or real literature data was used.
- Ruling R8 was followed: seven blocking findings were covered by seven aggregated regressions; ordinary Minor findings remain deferred.

## Finding verification and fixes

1. **Redirect credential forwarding (Critical).** Verified that the default `urllib` opener followed a 302 from one loopback port to another and forwarded the request headers. `UrllibTransport` now installs a no-redirect handler, returns the 3xx response as an error-class response, and never reaches the second origin.
2. **Unsafe loaded plans (Critical).** Verified that loaded `upsert_items` plans could carry deletion flags, unknown mutation fields, unmarked note updates, tag clearing, missing versions, and unchecked attachments into the write path. Apply now enforces item-type-specific field allowlists, rejects destructive fields, requires object keys/versions and Collection version binding, permits only stable-marker Codex notes, revalidates linked PDFs against bound final D roots, and requires one duplicate binding per top-level paper.
3. **Unbound Server ID (Critical).** Verified that a preview from one Zotero identity could be applied to a distinct identity. Production previews now probe the Server ID, store only its SHA-256 fingerprint in the canonical plan/digest, and compare the current fingerprint before authorization.
4. **Trusted metadata overwrite on exact reuse (Important).** Verified that candidate title/journal metadata replaced existing Zotero values. Exact reuse now derives a minimal payload from the existing object and changes only approved Collection membership; tags and metadata are not sent.
5. **Narrow dedupe and concurrent duplicates (Important).** Verified that CLI preview searched only the target Collection, wrote probable matches, and did not recheck a new candidate before authorization. Preview now performs per-candidate library-wide identifier/title lookup, probable matches stop as conflicts, and canonical duplicate bindings are re-read before authorization.
6. **Missing canonical note/attachment/evidence state (Important).** Verified that production preview emitted only parent items. It now includes a stable `data-codex-note="evidence-bounded-v1"` child note and any validated linked PDF in the canonical plan. New objects receive preview-bound Zotero keys with version `0`. Ruling R7 is preserved: low evidence with an accessible PDF does not receive `状态：待获取全文`.
7. **Partial-write index drift and hidden state (Important).** Verified that an early failed payload caused later read-back to compare against the wrong planned payload, while CLI exit 6 hid actual state. Read-back now maps response index to the exact payload and returned key; failed payloads are not reported as missing read-back objects. CLI returns redacted structured partial JSON with exit code 6.

## RED evidence

The seven aggregated regressions were added before production changes. Initial runs demonstrated:

- redirect test returned HTTP 200 from the second loopback server instead of retaining 302;
- exact reuse retained the untrusted candidate title;
- all five malicious-plan subcases reached or attempted the write path;
- a plan applied successfully to a different Server ID;
- library-wide exact reuse was missed, probable conflict was not blocked, and concurrent duplication was not rechecked;
- production preview contained only `journalArticle`, without note or attachment;
- first-item failure produced no structured partial JSON and misassociated later read-back.

## GREEN evidence

Focused consolidated regression command:

```text
python -m unittest -v <seven consolidated regression cases>
Ran 7 tests in 7.091s
OK
```

The first full run exposed eight legacy fixture/assertion mismatches caused by the intentional new key, probe, version, and response-index contracts. No new production defect was found. The eight focused compatibility checks then passed (`Ran 8 tests in 1.222s; OK`).

Fresh final full suite:

```text
python -m unittest discover -s skill\managing-zotero\scripts\tests -p test_*.py -q
Ran 87 tests in 14.386s
OK
```

`git diff --check` produced no whitespace errors; Git emitted only the repository's existing LF-to-CRLF advisory.

## Changed files

- `skill/managing-zotero/scripts/zotero_client.py`
- `skill/managing-zotero/scripts/zotero_models.py`
- `skill/managing-zotero/scripts/zotero_workflow.py`
- `skill/managing-zotero/scripts/zotero_local.py`
- `skill/managing-zotero/scripts/tests/fake_zotero_server.py`
- `skill/managing-zotero/scripts/tests/test_client.py`
- `skill/managing-zotero/scripts/tests/test_workflow.py`
- `skill/managing-zotero/scripts/tests/test_integration.py`

## Deferred Minor findings

- Collection reuse discovery before Collection creation.
- Optional tuple/list defaults at the model/input boundary.
- Additional explicit key discard/zeroization beyond the existing one-time consume-and-clear behavior.

No Critical or Important review finding remains open in this pass.

## Scoped personal-note re-review fix

A scoped re-review found that a loaded plan could target an existing personal-note key/version if the replacement HTML itself contained the Codex marker. A new synthetic regression seeded an unmarked personal note, then attempted a marked replacement at the same key. Before the fix, apply returned success and overwrote the note (`expected 2, got 0`).

The first version now permits only new stable-marker Codex notes with `version == 0`; all existing-note updates are rejected before authorization. This is the conservative branch because the current plan validator does not independently prove that an existing note was originally Codex-owned.

- Focused GREEN: `Ran 1 test in 0.538s; OK`.
- Fresh full suite: `Ran 88 tests in 14.786s; OK`.
- The seeded personal note remained unchanged; authorization and write counts remained zero.
