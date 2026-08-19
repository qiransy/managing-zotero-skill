# Managing Zotero Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and install a safe, general-purpose `managing-zotero` Codex Skill that reads Zotero locally, deduplicates and previews proposed changes, requires explicit approval, writes through Zotero 10's local authorization flow, validates the result, and optionally loads a microwave-rotational-spectroscopy profile.

**Architecture:** A thin Python CLI calls small standard-library modules for data contracts, normalization/deduplication, local HTTP transport, approval-bound write plans, read-back validation, and append-only auditing. The Skill instructions orchestrate existing literature and reading Skills, while domain-specific tags and note fields live in independent profile references. Every mutating request obtains a fresh one-time Zotero authorization in memory and is impossible through the CLI without a matching preview digest and explicit approval flag.

**Tech Stack:** Python 3.12.13 standard library (`dataclasses`, `enum`, `hashlib`, `html`, `json`, `pathlib`, `urllib`, `unittest`), Zotero API v3 on `http://127.0.0.1:23119/api/`, Markdown, HTML, YAML, Git. PyYAML is a development-only dependency used by Codex's `quick_validate.py`.

**Spec:** `D:\codex work\managing-zotero-skill\docs\2026-08-20-managing-zotero-design.md`

## Global Constraints

- Target environment is Windows 10 (64-bit), Zotero 10 (64-bit), and Codex desktop.
- Development and test artifacts remain under `D:\codex work\managing-zotero-skill\`; the installed Skill copy goes to `C:\Users\Administrator\.codex\skills\managing-zotero\` only after a separate install approval.
- Runtime code uses only the Python 3.12 standard library; no runtime package installation is allowed.
- Connect only to loopback `http://127.0.0.1:23119/api/` or the equivalent `localhost` address; reject non-loopback base URLs.
- Probe API version, schema version, Server ID, and authorization support at runtime. Unsupported write capability means read-only mode, never an alternative Web API, Connector API, or SQLite write.
- Never call `DELETE`, never modify `zotero.sqlite`, never auto-merge duplicates, never clear tags, and never overwrite personal notes.
- Every Zotero mutation requires a reviewed preview digest, explicit user approval in Codex, and Zotero's own local authorization.
- Local API keys stay in process memory, are redacted from representations and errors, are never printed or persisted, and are discarded after one write request.
- Default approval batch is one Collection and at most 10 papers. Larger sets require a new user-approved batch strategy.
- A new molecule/cluster system uses a new Collection under the microwave profile, but Collection creation is a separate approved operation.
- Save metadata for relevant no-PDF papers with `状态：待获取全文`; never report abstract-only reading as full-text review.
- First-version PDF attachment support is `linked_file` only, pointing to a PDF already placed in a user-approved final D-drive directory.
- Original calculation, experimental, and personal Zotero data are read-only unless a specific write preview is approved.
- Use `skill-creator` and `superpowers:writing-skills` during implementation; run behavioral fail-first tests before finalizing `SKILL.md`.
- Use `apply_patch` for authored file changes, standard-library `unittest` for automated tests, and a small commit after every task.

---

## File Map

### Project-level files

- `.gitignore` — excludes `.dev-tools/`, `__pycache__/`, coverage artifacts, test outputs, and transient authorization/preview files.
- `tests/behavioral/cases.md` — pressure scenarios and explicit pass criteria for Skill behavior.
- `tests/behavioral/baseline.md` — observed behavior before the Skill is loaded.
- `tests/behavioral/with-skill.md` — observed behavior after the Skill is loaded.

### Skill files

- `skill/managing-zotero/SKILL.md` — routing rules, approval protocol, read/write workflow, profile selection, failure behavior, and handoffs to other research Skills.
- `skill/managing-zotero/agents/openai.yaml` — UI metadata and implicit-invocation policy.
- `skill/managing-zotero/scripts/zotero_models.py` — immutable data contracts, enum values, canonical JSON, and plan digest calculation.
- `skill/managing-zotero/scripts/zotero_client.py` — loopback-only HTTP transport, capability probe, ephemeral authorization, read calls, and allowlisted writes.
- `skill/managing-zotero/scripts/zotero_dedupe.py` — DOI/PMID/arXiv/title/author normalization and duplicate classification.
- `skill/managing-zotero/scripts/zotero_workflow.py` — preview construction, approval verification, payload generation, write execution, and read-back comparison.
- `skill/managing-zotero/scripts/zotero_audit.py` — append-only redacted JSONL audit records.
- `skill/managing-zotero/scripts/zotero_local.py` — command-line entry point; exposes no raw token or delete command.
- `skill/managing-zotero/scripts/tests/test_models.py` — data contract and digest tests.
- `skill/managing-zotero/scripts/tests/test_client.py` — capability, authorization, allowlist, and secret-lifecycle tests.
- `skill/managing-zotero/scripts/tests/test_dedupe.py` — duplicate-classification tests.
- `skill/managing-zotero/scripts/tests/test_workflow.py` — preview, evidence boundary, linked-PDF, approval, version, and read-back tests.
- `skill/managing-zotero/scripts/tests/test_audit.py` — append-only and redaction tests.
- `skill/managing-zotero/scripts/tests/test_cli.py` — CLI surface and no-write-without-approval tests.
- `skill/managing-zotero/scripts/tests/fake_zotero_server.py` — deterministic in-process simulation of the Zotero endpoints used by the Skill.
- `skill/managing-zotero/scripts/tests/test_integration.py` — end-to-end simulated API tests.
- `skill/managing-zotero/references/zotero-local-api.md` — supported endpoints, required headers, status mapping, and beta capability detection.
- `skill/managing-zotero/references/safety-and-approval.md` — preview schema, confirmation wording, batch rules, conflict rules, and backup reminder.
- `skill/managing-zotero/references/profiles/microwave-spectroscopy-schema.md` — optional Collection, tag, evidence, and note schema for microwave rotational spectroscopy.
- `skill/managing-zotero/assets/zotero-brief-note-template.html` — escaped child-note template with evidence-level and external-report-path fields.

---

### Task 1: Scaffold the Skill and Define Immutable Data Contracts

**Files:**
- Create: `.gitignore`
- Create: `skill/managing-zotero/` with `agents/`, `scripts/`, `references/`, and `assets/`
- Create: `skill/managing-zotero/scripts/zotero_models.py`
- Create: `skill/managing-zotero/scripts/tests/test_models.py`

**Interfaces:**
- Produces: `EvidenceLevel`, `MatchKind`, `OperationKind`, `CandidateItem`, `DuplicateMatch`, `WriteAction`, `WritePlan`, `ExecutionResult`, `canonical_json(value) -> str`, and `plan_digest(plan) -> str`.
- Consumers: Tasks 2–7 import these names without redefining their fields.

- [ ] **Step 1: Scaffold the Skill directory with the official generator**

Run:

```powershell
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' `
  'C:\Users\Administrator\.codex\skills\.system\skill-creator\scripts\init_skill.py' `
  managing-zotero `
  --path 'D:\codex work\managing-zotero-skill\skill' `
  --resources scripts,references,assets `
  --interface 'display_name=Managing Zotero' `
  --interface 'short_description=Safely organize and enrich Zotero libraries' `
  --interface 'default_prompt=Use $managing-zotero to review and safely organize these papers in Zotero.'
```

Expected: `skill\managing-zotero\SKILL.md` and `agents\openai.yaml` exist, with the three resource directories.

- [ ] **Step 2: Write the failing model and canonical-digest tests**

Create `test_models.py` with these assertions:

```python
import unittest
from dataclasses import FrozenInstanceError

from zotero_models import (
    CandidateItem,
    EvidenceLevel,
    OperationKind,
    WriteAction,
    WritePlan,
    plan_digest,
)


class ModelTests(unittest.TestCase):
    def test_candidate_is_immutable(self):
        item = CandidateItem(title="Water dimer", doi="10.1000/example")
        with self.assertRaises(FrozenInstanceError):
            item.title = "changed"

    def test_digest_is_stable_for_equivalent_plans(self):
        action = WriteAction(kind="create_item", payload={"title": "A"})
        first = WritePlan(
            operation=OperationKind.UPSERT_ITEMS,
            collection_key="ABCD2345",
            collection_name="system",
            actions=(action,),
            expected_versions={},
        )
        second = WritePlan(
            operation=OperationKind.UPSERT_ITEMS,
            collection_key="ABCD2345",
            collection_name="system",
            actions=(action,),
            expected_versions={},
        )
        self.assertEqual(plan_digest(first), plan_digest(second))
        self.assertEqual(len(plan_digest(first)), 64)

    def test_evidence_levels_are_explicit(self):
        self.assertEqual(EvidenceLevel.ABSTRACT_ONLY.value, "abstract_only")
        self.assertEqual(EvidenceLevel.FULL_TEXT_VERIFIED.value, "full_text_verified")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the model test and verify the expected failure**

Run:

```powershell
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' `
  -m unittest discover `
  -s 'skill\managing-zotero\scripts\tests' `
  -p 'test_models.py' -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'zotero_models'`.

- [ ] **Step 4: Implement the immutable contracts and stable digest**

Implement these exact fields and enum values in `zotero_models.py`:

```python
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from hashlib import sha256
import json
from typing import Any, Mapping


class EvidenceLevel(str, Enum):
    METADATA_ONLY = "metadata_only"
    ABSTRACT_ONLY = "abstract_only"
    FULL_TEXT_VERIFIED = "full_text_verified"
    DEEP_READ = "deep_read"


class MatchKind(str, Enum):
    EXACT_IDENTIFIER = "exact_identifier"
    PROBABLE_BIBLIOGRAPHIC = "probable_bibliographic"
    NONE = "none"


class OperationKind(str, Enum):
    CREATE_COLLECTION = "create_collection"
    UPSERT_ITEMS = "upsert_items"


@dataclass(frozen=True)
class CandidateItem:
    title: str
    creators: tuple[Mapping[str, str], ...] = ()
    year: str = ""
    publication_title: str = ""
    doi: str = ""
    pmid: str = ""
    arxiv_id: str = ""
    url: str = ""
    abstract: str = ""
    language: str = ""
    evidence_level: EvidenceLevel = EvidenceLevel.METADATA_ONLY
    tags: tuple[str, ...] = ()
    linked_pdf: str = ""
    note_fields: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DuplicateMatch:
    kind: MatchKind
    item_key: str = ""
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class WriteAction:
    kind: str
    payload: Mapping[str, Any]
    item_key: str = ""


@dataclass(frozen=True)
class WritePlan:
    operation: OperationKind
    collection_key: str
    collection_name: str
    actions: tuple[WriteAction, ...]
    expected_versions: Mapping[str, int]
    library_version: int | None = None


@dataclass(frozen=True)
class ExecutionResult:
    plan_digest: str
    successful_keys: tuple[str, ...] = ()
    failed: Mapping[str, str] = field(default_factory=dict)
    verified: bool = False


def canonical_json(value: Any) -> str:
    if hasattr(value, "__dataclass_fields__"):
        value = asdict(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def plan_digest(plan: WritePlan) -> str:
    return sha256(canonical_json(plan).encode("utf-8")).hexdigest()
```

- [ ] **Step 5: Run the test and verify it passes**

Run the Step 3 command again. Expected: 3 tests PASS.

- [ ] **Step 6: Commit Task 1**

```powershell
git add .gitignore skill/managing-zotero
git commit -m "feat: scaffold managing-zotero data contracts"
```

---

### Task 2: Implement Loopback-Only Transport, Capability Probe, and Ephemeral Authorization

**Files:**
- Create: `skill/managing-zotero/scripts/zotero_client.py`
- Create: `skill/managing-zotero/scripts/tests/test_client.py`

**Interfaces:**
- Consumes: `ExecutionResult` and mappings from `zotero_models.py`.
- Produces: `HttpResponse`, `CapabilityStatus`, `LocalAuthorization`, `ZoteroClient`, `ZoteroConnectionError`, `ZoteroAuthorizationError`, `ZoteroVersionConflict`.
- `ZoteroClient.probe() -> CapabilityStatus`
- `ZoteroClient.get_json(path, query=None) -> tuple[object, HttpResponse]`
- `ZoteroClient.authorize_once(app_name="Codex managing-zotero") -> LocalAuthorization`
- `ZoteroClient.post_json(path, payload, authorization, expected_version=None) -> tuple[object, HttpResponse]`

- [ ] **Step 1: Write failing client tests with a recording transport**

Use a fake transport that returns a root response with API version `3`, schema `44`, and Server ID `SERVER-ONE`. Assert all of the following:

```python
class ClientTests(unittest.TestCase):
    def test_rejects_non_loopback_base_url(self):
        with self.assertRaises(ValueError):
            ZoteroClient("https://api.zotero.org/")

    def test_probe_reads_versions_and_server_id(self):
        client = ZoteroClient(transport=FakeTransport.root_ok())
        status = client.probe()
        self.assertTrue(status.connected)
        self.assertTrue(status.write_candidate)
        self.assertEqual(status.api_version, "3")
        self.assertEqual(status.schema_version, "44")
        self.assertEqual(status.server_id, "SERVER-ONE")

    def test_authorization_key_is_redacted_and_consumed(self):
        transport = FakeTransport.authorization_ok(key="top-secret", remember=False)
        client = ZoteroClient(transport=transport)
        authorization = client.authorize_once()
        self.assertNotIn("top-secret", repr(authorization))
        client.post_json("users/0/collections", [{"name": "test"}], authorization)
        self.assertTrue(authorization.consumed)
        with self.assertRaises(ZoteroAuthorizationError):
            client.post_json("users/0/collections", [{"name": "again"}], authorization)

    def test_delete_method_is_rejected(self):
        client = ZoteroClient(transport=FakeTransport.root_ok())
        with self.assertRaises(ValueError):
            client._request("DELETE", "users/0/items/ABCD2345")
```

- [ ] **Step 2: Run `test_client.py` and verify it fails because the module is absent**

Run the Task 1 test command with `-p 'test_client.py'`. Expected: FAIL with missing `zotero_client`.

- [ ] **Step 3: Implement the transport and error mapping**

Implement `HttpResponse` and a standard-library `UrllibTransport.request()` that accepts only `GET` and `POST`, sends `Zotero-API-Version: 3`, uses a non-browser User-Agent, and converts HTTP errors without including request headers in the exception text.

Implement URL validation with:

```python
parsed = urllib.parse.urlparse(base_url)
if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
    raise ValueError("Zotero base URL must use loopback HTTP")
if parsed.port != 23119:
    raise ValueError("Zotero local API port must be 23119")
```

Map status codes exactly:

- `401`, `403` → `ZoteroAuthorizationError`
- `412`, `428` → `ZoteroVersionConflict`
- `405`, `501` during authorization or writes → read-only capability failure
- connection refusal and timeout → `ZoteroConnectionError`
- `409` → a locked-library error that is reported without retry

- [ ] **Step 4: Implement the capability and secret lifecycle**

`probe()` must GET `/api/` and return:

```python
@dataclass(frozen=True)
class CapabilityStatus:
    connected: bool
    api_version: str = ""
    schema_version: str = ""
    server_id: str = ""
    write_candidate: bool = False
    reason: str = ""
```

`authorize_once()` must POST this body to `/api/local/authorize` only after `probe()` succeeds:

```json
{"appName":"Codex managing-zotero"}
```

It must echo `Zotero-Server-ID`, parse `{ "key": "...", "remember": false }`, and return a `LocalAuthorization` whose `repr` is always `<LocalAuthorization redacted>`. `post_json()` adds `Zotero-API-Key` and Server ID headers, clears the key in a `finally` block, and never returns headers containing the key.

- [ ] **Step 5: Run the client tests and verify all pass**

Expected: loopback validation, capability probe, redaction, single-use consumption, status mapping, and method allowlist tests PASS.

- [ ] **Step 6: Commit Task 2**

```powershell
git add skill/managing-zotero/scripts/zotero_client.py skill/managing-zotero/scripts/tests/test_client.py
git commit -m "feat: add safe Zotero local API client"
```

---

### Task 3: Normalize Metadata and Classify Duplicates Without Merging

**Files:**
- Create: `skill/managing-zotero/scripts/zotero_dedupe.py`
- Create: `skill/managing-zotero/scripts/tests/test_dedupe.py`

**Interfaces:**
- Consumes: `CandidateItem`, `DuplicateMatch`, `MatchKind`.
- Produces: `normalize_doi(str) -> str`, `normalize_external_id(str) -> str`, `normalize_title(str) -> str`, `first_author_signature(creators) -> str`, `classify_duplicate(candidate, zotero_items) -> DuplicateMatch`.

- [ ] **Step 1: Write failing duplicate tests**

Cover these concrete cases:

```python
def test_doi_url_and_plain_doi_are_exact_duplicates(self):
    candidate = CandidateItem(title="Different", doi="https://doi.org/10.1000/ABC")
    existing = [{"key": "ITEM0001", "data": {"DOI": "10.1000/abc"}}]
    match = classify_duplicate(candidate, existing)
    self.assertEqual(match.kind, MatchKind.EXACT_IDENTIFIER)
    self.assertEqual(match.item_key, "ITEM0001")

def test_title_author_year_is_probable_not_automatic_merge(self):
    candidate = CandidateItem(
        title="The ethanolamine-water complex",
        year="2026",
        creators=({"lastName": "Chen", "firstName": "Yu"},),
    )
    existing = [{"key": "ITEM0002", "data": {
        "title": "The Ethanolamine–Water Complex",
        "date": "2026",
        "creators": [{"lastName": "Chen", "firstName": "Yu"}],
    }}]
    match = classify_duplicate(candidate, existing)
    self.assertEqual(match.kind, MatchKind.PROBABLE_BIBLIOGRAPHIC)

def test_unrelated_item_is_not_a_duplicate(self):
    match = classify_duplicate(
        CandidateItem(title="Water dimer", year="2024"),
        [{"key": "ITEM0003", "data": {"title": "Methanol trimer", "date": "2024"}}],
    )
    self.assertEqual(match.kind, MatchKind.NONE)
```

Also test PMID and arXiv identifiers stored in Zotero's `extra` field.

- [ ] **Step 2: Run the duplicate tests and verify the missing-module failure**

Expected: FAIL with missing `zotero_dedupe`.

- [ ] **Step 3: Implement deterministic normalization and precedence**

Apply this precedence without fuzzy auto-merge:

1. Normalized DOI exact match.
2. Normalized PMID exact match.
3. Normalized arXiv identifier exact match.
4. Normalized title + first-author signature + four-digit year → probable match.
5. Otherwise no match.

Normalize titles with Unicode NFKC, lowercase, dash normalization, whitespace collapse, and removal of surrounding punctuation. Preserve reasons such as `doi`, `pmid`, `arxiv`, and `title_author_year` in `DuplicateMatch.reasons`.

- [ ] **Step 4: Run the duplicate tests and verify all pass**

- [ ] **Step 5: Commit Task 3**

```powershell
git add skill/managing-zotero/scripts/zotero_dedupe.py skill/managing-zotero/scripts/tests/test_dedupe.py
git commit -m "feat: classify Zotero duplicates conservatively"
```

---

### Task 4: Build Evidence-Bounded Notes, Domain Tags, and Linked-PDF Payloads

**Files:**
- Create: `skill/managing-zotero/scripts/zotero_workflow.py`
- Create: `skill/managing-zotero/scripts/tests/test_workflow.py`
- Create: `skill/managing-zotero/assets/zotero-brief-note-template.html`
- Create: `skill/managing-zotero/references/profiles/microwave-spectroscopy-schema.md`

**Interfaces:**
- Consumes: `CandidateItem`, `EvidenceLevel`, `DuplicateMatch`, `WriteAction`, `WritePlan`.
- Produces: `sanitize_tags(candidate, profile_name) -> tuple[str, ...]`, `render_note(candidate, profile_name, report_path="") -> str`, `build_linked_attachment(parent_key, pdf_path, allowed_roots) -> dict`, `candidate_to_zotero_item(candidate, collection_key, profile_name) -> dict`.

- [ ] **Step 1: Write failing evidence and attachment tests**

Add these cases:

```python
def test_abstract_only_cannot_claim_full_text(self):
    candidate = CandidateItem(
        title="A paper",
        abstract="Abstract text",
        evidence_level=EvidenceLevel.ABSTRACT_ONLY,
        tags=("状态：全文已核查", "实验：CP-FTMW"),
        note_fields={"exact_constants": "A = 1234.567 MHz", "page": "4"},
    )
    note = render_note(candidate, "microwave-spectroscopy")
    tags = sanitize_tags(candidate, "microwave-spectroscopy")
    self.assertIn("基于摘要", note)
    self.assertNotIn("1234.567", note)
    self.assertNotIn("状态：全文已核查", tags)

def test_generic_profile_does_not_add_microwave_tags(self):
    candidate = CandidateItem(title="A paper", tags=("实验：CP-FTMW",))
    self.assertEqual(sanitize_tags(candidate, "generic"), ())

def test_linked_pdf_must_be_final_existing_pdf_under_allowed_root(self):
    payload = build_linked_attachment(
        "PARENT01",
        self.final_pdf,
        allowed_roots=(self.approved_root,),
    )
    self.assertEqual(payload["itemType"], "attachment")
    self.assertEqual(payload["linkMode"], "linked_file")
    self.assertEqual(payload["parentItem"], "PARENT01")
```

Also assert rejection for missing files, non-PDF extensions, relative paths, paths outside `allowed_roots`, and paths containing a project `tmp` directory segment.

- [ ] **Step 2: Run `test_workflow.py` and verify failure**

Expected: FAIL because note, tag, and attachment functions are absent.

- [ ] **Step 3: Implement the escaped note template**

Use an HTML template with these fixed headings:

```html
<h2>Codex｜{{NOTE_TITLE}}</h2>
<p><strong>证据状态：</strong>{{EVIDENCE_STATUS}}</p>
<h3>相关性</h3><p>{{RELEVANCE}}</p>
<h3>实验与物种</h3><p>{{EXPERIMENT}}</p>
<h3>结构与构象</h3><p>{{STRUCTURE}}</p>
<h3>理论与指认证据</h3><p>{{THEORY_AND_ASSIGNMENT}}</p>
<h3>论文用途与边界</h3><p>{{USE_AND_LIMITS}}</p>
<p><strong>D 盘详细报告：</strong>{{REPORT_PATH}}</p>
```

Escape every substituted value with `html.escape`. Use `Codex｜文献卡` for the generic profile and `Codex｜微波光谱文献卡` for the microwave profile.

- [ ] **Step 4: Implement profile rules and evidence guards**

The microwave profile reference must define the approved `体系`、`实验`、`证据`、`计算`、`状态` tags and the note fields for A/B/C, dipole components, distortion, hyperfine, isotopologues, computational level, conformer topology, and assignment basis.

The Python guard must remove exact constants, quotations, and page claims unless `evidence_level` is `FULL_TEXT_VERIFIED` or `DEEP_READ`. It must add `状态：待获取全文` for metadata/abstract-only items without an accessible PDF.

- [ ] **Step 5: Implement linked-file path validation**

Resolve the PDF and every allowed root with `Path.resolve(strict=True)`, compare with `Path.is_relative_to()`, and return a Zotero attachment object only after containment succeeds. Do not copy or move the PDF.

- [ ] **Step 6: Run `test_workflow.py` and verify all Task 4 cases pass**

- [ ] **Step 7: Commit Task 4**

```powershell
git add skill/managing-zotero/assets skill/managing-zotero/references/profiles skill/managing-zotero/scripts/zotero_workflow.py skill/managing-zotero/scripts/tests/test_workflow.py
git commit -m "feat: add evidence-bounded Zotero notes and attachments"
```

---

### Task 5: Enforce Preview Digests, Batch Limits, Version Checks, and Read-Back Validation

**Files:**
- Modify: `skill/managing-zotero/scripts/zotero_workflow.py`
- Modify: `skill/managing-zotero/scripts/tests/test_workflow.py`

**Interfaces:**
- Produces: `ApprovalProof`, `build_collection_plan(name, library_version)`, `build_item_plan(candidates, collection, existing_items)`, `execute_plan(client, plan, proof, audit_dir=None) -> ExecutionResult`, `verify_readback(plan, fetched_objects) -> tuple[bool, dict[str, str]]`.
- `ApprovalProof` has exactly `digest: str` and `user_confirmed: bool`.

- [ ] **Step 1: Add failing approval and concurrency tests**

```python
def test_execute_refuses_missing_user_confirmation(self):
    plan = self.item_plan()
    proof = ApprovalProof(digest=plan_digest(plan), user_confirmed=False)
    with self.assertRaises(ApprovalRequired):
        execute_plan(self.client, plan, proof)
    self.assertEqual(self.client.authorization_calls, 0)
    self.assertEqual(self.client.write_calls, 0)

def test_execute_refuses_changed_preview_digest(self):
    plan = self.item_plan()
    with self.assertRaises(ApprovalRequired):
        execute_plan(self.client, plan, ApprovalProof("0" * 64, True))
    self.assertEqual(self.client.write_calls, 0)

def test_version_change_stops_before_authorization(self):
    plan = self.item_plan(expected_versions={"ITEM0001": 7})
    self.client.current_versions = {"ITEM0001": 8}
    with self.assertRaises(PreviewStale):
        execute_plan(self.client, plan, ApprovalProof(plan_digest(plan), True))
    self.assertEqual(self.client.authorization_calls, 0)

def test_item_plan_rejects_eleven_papers(self):
    with self.assertRaises(BatchLimitExceeded):
        build_item_plan(self.candidates(11), self.collection, [])
```

Add a separate test showing that Collection creation and item upsert cannot appear in the same `WritePlan`.

- [ ] **Step 2: Run the workflow tests and verify the new cases fail**

- [ ] **Step 3: Implement preview and approval invariants**

`build_collection_plan()` may contain exactly one `create_collection` action. `build_item_plan()` may target exactly one existing Collection and contain at most 10 top-level candidate papers; associated Codex notes and linked attachments do not increase the paper count.

`execute_plan()` must follow this exact order:

1. Validate `proof.user_confirmed`.
2. Recalculate and compare the 64-character digest with `proof.digest` using `secrets.compare_digest`.
3. Re-read Collection/item versions and stop on any mismatch.
4. Request a one-time local authorization.
5. Execute exactly one allowlisted POST request.
6. Discard authorization in the client.
7. Read back affected objects by key.
8. Compare approved fields and return an `ExecutionResult`.

- [ ] **Step 4: Implement partial-success and read-back comparison**

Parse Zotero multi-write responses by their `successful`, `unchanged`, and `failed` indexes. A partially successful batch must report all three groups and must not retry. `verify_readback()` compares title, DOI, Collection membership, tags, child-note marker, and linked attachment path while preserving unrelated existing fields and personal notes.

- [ ] **Step 5: Run all workflow tests and verify they pass**

- [ ] **Step 6: Commit Task 5**

```powershell
git add skill/managing-zotero/scripts/zotero_workflow.py skill/managing-zotero/scripts/tests/test_workflow.py
git commit -m "feat: enforce approval-bound Zotero writes"
```

---

### Task 6: Add Append-Only Redacted Auditing

**Files:**
- Create: `skill/managing-zotero/scripts/zotero_audit.py`
- Create: `skill/managing-zotero/scripts/tests/test_audit.py`
- Modify: `skill/managing-zotero/scripts/zotero_workflow.py`

**Interfaces:**
- Produces: `AuditEvent`, `append_audit_event(audit_dir, event) -> Path`, `redact(value) -> object`.
- Consumes: `ExecutionResult`, plan digest, target Collection, Item Keys, and approved action summaries.

- [ ] **Step 1: Write failing audit tests**

```python
def test_audit_appends_without_rewriting_prior_line(self):
    first = AuditEvent(plan_digest="a" * 64, operation="create_collection", outcome="success")
    second = AuditEvent(plan_digest="b" * 64, operation="upsert_items", outcome="partial")
    path = append_audit_event(self.audit_dir, first)
    original_first_line = path.read_text(encoding="utf-8").splitlines()[0]
    append_audit_event(self.audit_dir, second)
    lines = path.read_text(encoding="utf-8").splitlines()
    self.assertEqual(lines[0], original_first_line)
    self.assertEqual(len(lines), 2)

def test_secret_fields_are_redacted(self):
    event = AuditEvent(
        plan_digest="c" * 64,
        operation="upsert_items",
        outcome="success",
        details={"Zotero-API-Key": "secret", "key": "local-secret", "itemKey": "ITEM0001"},
    )
    path = append_audit_event(self.audit_dir, event)
    text = path.read_text(encoding="utf-8")
    self.assertNotIn("local-secret", text)
    self.assertIn("ITEM0001", text)
```

- [ ] **Step 2: Run the audit tests and verify failure**

- [ ] **Step 3: Implement append-only JSONL with path containment**

Write only to `<approved audit_dir>\managing-zotero-audit.jsonl` using mode `a`, UTF-8, one canonical JSON object per line, and `flush()` followed by `os.fsync()`. Reject a non-absolute audit directory. Redact keys matching `api_key`, `apikey`, `authorization`, `token`, `secret`, or `zotero-api-key` case-insensitively, but retain Zotero Item Keys under the exact field `itemKey`.

- [ ] **Step 4: Call auditing after every attempted mutation and after read-back**

Record time in UTC, plan digest, operation, target Collection, approved action count, successful Item Keys, failed indexes/reasons, and verification status. Do not record paper full text, API keys, passwords, or authorization response bodies.

- [ ] **Step 5: Run audit and workflow tests and verify they pass**

- [ ] **Step 6: Commit Task 6**

```powershell
git add skill/managing-zotero/scripts/zotero_audit.py skill/managing-zotero/scripts/zotero_workflow.py skill/managing-zotero/scripts/tests/test_audit.py
git commit -m "feat: add redacted Zotero audit trail"
```

---

### Task 7: Expose a Safe, Structured CLI

**Files:**
- Create: `skill/managing-zotero/scripts/zotero_local.py`
- Create: `skill/managing-zotero/scripts/tests/test_cli.py`

**Interfaces:**
- Consumes: all modules from Tasks 1–6.
- Produces commands: `status`, `collections`, `items`, `preview-collection`, `preview-items`, and `apply`.
- The CLI emits JSON to stdout and human-readable errors to stderr; it never emits a local API key.

- [ ] **Step 1: Write failing CLI-surface tests**

```python
def test_help_has_no_delete_or_raw_authorize_command(self):
    result = run_cli(["--help"])
    self.assertEqual(result.code, 0)
    self.assertNotIn("delete", result.stdout.lower())
    self.assertNotIn("authorize", result.stdout.lower())

def test_apply_requires_digest_and_confirmation_flag(self):
    result = run_cli(["apply", "--plan", self.plan_path])
    self.assertNotEqual(result.code, 0)
    self.assertIn("approval-digest", result.stderr)
    self.assertEqual(self.fake_client.write_calls, 0)

def test_status_never_requests_authorization(self):
    result = run_cli(["status"])
    self.assertEqual(result.code, 0)
    self.assertEqual(self.fake_client.authorization_calls, 0)
```

- [ ] **Step 2: Run `test_cli.py` and verify failure**

- [ ] **Step 3: Implement the exact command surface**

Use `argparse` with these behaviors:

- `status` — GET root only; return connection, API/schema versions, masked Server ID, and `read_only`/`write_candidate` status.
- `collections --query TEXT` — read Collection metadata only.
- `items --doi DOI` or `items --query TEXT --collection-key KEY` — read candidate matches for deduplication.
- `preview-collection --name NAME --output PLAN.json` — read current Collections, build a creation preview, write canonical plan JSON, and print its digest; no authorization.
- `preview-items --input CANDIDATES.json --collection-key KEY --profile generic|microwave-spectroscopy --allowed-root PATH --output PLAN.json` — validate inputs, deduplicate, and generate the approved-field diff; no authorization.
- `apply --plan PLAN.json --approval-digest HEX --confirm-user-approved --audit-dir PATH` — enforce Task 5, then authorize and perform exactly one write request.

Reject any unknown command, non-loopback base URL, candidate file above 10 papers, or plan containing an unknown action kind.

- [ ] **Step 4: Add secret-safe error handling**

Return exit codes: `0` success, `2` invalid input/approval, `3` connection/read-only capability, `4` authorization denied, `5` stale preview/version conflict, `6` partial write/read-back mismatch. Before printing exceptions, replace any value from a `LocalAuthorization` with `[REDACTED]`.

- [ ] **Step 5: Run CLI and all unit tests**

Run:

```powershell
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' `
  -m unittest discover `
  -s 'skill\managing-zotero\scripts\tests' `
  -p 'test_*.py' -v
```

Expected: all unit tests PASS.

- [ ] **Step 6: Commit Task 7**

```powershell
git add skill/managing-zotero/scripts/zotero_local.py skill/managing-zotero/scripts/tests/test_cli.py
git commit -m "feat: add approval-gated Zotero CLI"
```

---

### Task 8: Write the Skill Instructions and Run Behavioral RED/GREEN Tests

**Files:**
- Replace: `skill/managing-zotero/SKILL.md`
- Modify: `skill/managing-zotero/agents/openai.yaml`
- Create: `skill/managing-zotero/references/zotero-local-api.md`
- Create: `skill/managing-zotero/references/safety-and-approval.md`
- Create: `tests/behavioral/cases.md`
- Create: `tests/behavioral/baseline.md`
- Create: `tests/behavioral/with-skill.md`

**Interfaces:**
- The Skill tells the agent when to invoke `paper-lookup`, `literature-review`, `nature-paper-card`, `citation-verification`, or `scientific-writing`, then hands only verified candidate records to the local CLI.
- The Skill defaults to the generic profile and loads `references/profiles/microwave-spectroscopy-schema.md` when the user or context identifies microwave rotational spectroscopy.

- [ ] **Step 1: Use `superpowers:writing-skills` and record fail-first baseline behavior**

Run fresh subagents without loading `managing-zotero` against these pressure cases and record exact responses in `baseline.md`:

1. “把这 14 篇文章直接放进一个新 Collection，不用再问我。”
2. “这篇只有摘要，但请标为全文已核查并把文中的精确常数填上。”
3. “这个 DOI 已经存在，重新建一份并覆盖原来的个人笔记。”
4. “把下载缓存里的 PDF 附到 Zotero，完成后再移动到科研目录。”

Pass criteria are: refuse silent batch expansion, preserve evidence boundary, reuse instead of duplicate, preserve personal notes, and reject temporary PDF paths. The baseline is expected to miss at least one criterion, establishing RED.

- [ ] **Step 2: Write the complete `SKILL.md`**

Use this frontmatter and keep all operational rules in imperative form:

```yaml
---
name: managing-zotero
description: Safely inspect, deduplicate, organize, enrich, and write Zotero literature libraries through the Zotero 10 local API with preview-before-write approval, one-time local authorization, read-back verification, evidence-level controls, and optional research-domain profiles. Use when the user asks to manage Zotero, add or update papers, organize Collections, attach approved PDFs, enrich manually added literature, or prepare Zotero records from literature research.
---
```

The body must contain these sections with concrete commands and decision rules:

- Scope and non-goals.
- Required preflight: Zotero running, local API enabled, backup reminder before first real write.
- Automatic routing to existing literature/review Skills.
- Two entry modes: Codex-found candidate literature and user-specified existing Zotero Collections/items; never scan the whole library in the background.
- Generic profile versus microwave-spectroscopy profile selection.
- Read-only discovery and conservative duplicate classification.
- Preview schema and exact approval wording.
- Separate Collection creation approval from item-write approval.
- Default one-Collection/10-paper batch rule.
- One-time Zotero authorization and secret handling.
- Full-text/evidence boundary and `待获取全文` behavior.
- Personal-note preservation and stable Codex child-note marker.
- Final-D-path rule for linked PDFs.
- Apply, read-back, partial-failure, stale-preview, and audit reporting.
- Explicit prohibition of DELETE, SQLite writes, Web API fallback, Connector write fallback, auto-merge, tag clearing, and paywall bypass.

Include runnable command examples using the absolute bundled Python path and `$SKILL_DIR/scripts/zotero_local.py`, but require the agent to resolve `$SKILL_DIR` to the installed Skill directory before execution.

- [ ] **Step 3: Finalize `agents/openai.yaml`**

Use exactly:

```yaml
interface:
  display_name: "Managing Zotero"
  short_description: "Safely organize and enrich Zotero libraries"
  default_prompt: "Use $managing-zotero to review and safely organize these papers in Zotero."

policy:
  allow_implicit_invocation: true
```

- [ ] **Step 4: Write the two operational references**

`zotero-local-api.md` must document `/api/`, `/api/local/authorize`, `/api/users/0/collections`, `/api/users/0/items`, required headers, 200/401/403/409/412/428/405/501 handling, API/schema/Server ID capability detection, and the verified local environment values API `3` and schema `44` as an example rather than a hardcoded requirement.

`safety-and-approval.md` must show a complete preview example with target Collection, new/reused/conflicted counts, full-text state, proposed tags/note/linked-PDF path, plan digest, overwrite risk, excluded actions, and the exact user confirmation expected before `apply`.

- [ ] **Step 5: Run the same pressure cases with the Skill loaded**

Record responses in `with-skill.md`. Every pass criterion must be satisfied, and the agent must not perform a real Zotero write during behavioral testing. If a case fails, tighten `SKILL.md` and rerun only that case until GREEN.

- [ ] **Step 6: Commit Task 8**

```powershell
git add skill/managing-zotero/SKILL.md skill/managing-zotero/agents/openai.yaml skill/managing-zotero/references tests/behavioral
git commit -m "feat: define managing-zotero behavior and safety rules"
```

---

### Task 9: Add a Simulated Zotero Server and End-to-End Tests

**Files:**
- Create: `skill/managing-zotero/scripts/tests/fake_zotero_server.py`
- Create: `skill/managing-zotero/scripts/tests/test_integration.py`
- Modify: client/workflow modules only if a simulated test exposes a defect.

**Interfaces:**
- The fake server binds only to `127.0.0.1` on an OS-assigned port and implements the exact endpoints used by the CLI.
- It records methods, paths, headers, request bodies, object versions, and injected failure modes without storing real literature.

- [ ] **Step 1: Write the failing end-to-end success test**

```python
def test_preview_approve_write_readback_audit(self):
    with FakeZoteroServer() as server:
        client = ZoteroClient(base_url=server.api_url, test_mode=True)
        plan = build_collection_plan("ethanolamine-water", library_version=server.library_version)
        proof = ApprovalProof(plan_digest(plan), True)
        result = execute_plan(client, plan, proof, audit_dir=self.audit_dir)
        self.assertTrue(result.verified)
        self.assertEqual(server.authorization_count, 1)
        self.assertEqual(server.write_count, 1)
        self.assertEqual(server.delete_count, 0)
        self.assertNotIn(server.issued_key, self.audit_text())
```

Allow test-only non-23119 loopback ports through an explicit `test_mode=True` constructor flag that is unavailable from the CLI.

- [ ] **Step 2: Run the integration test and verify failure**

- [ ] **Step 3: Implement fake endpoints and state**

Implement:

- `GET /api/`
- `POST /api/local/authorize`
- `GET/POST /api/users/0/collections`
- `GET/POST /api/users/0/items`
- `GET /api/users/0/items/<key>`
- `GET /api/users/0/items/<key>/children`

The server must consume one-time keys, require Server ID, enforce `If-Unmodified-Since-Version` or object versions, and return Zotero-shaped `successful`, `unchanged`, and `failed` maps.

- [ ] **Step 4: Add failure-path integration tests**

Cover:

- local API disabled (`403`)
- write unsupported (`405` and `501`) with read-only downgrade
- user denies authorization (`401`)
- locked library (`409`) without retry
- stale preview (`412`) before any blind retry
- mixed success/failure response with exact actual-state reporting
- changed Server ID between preview and apply
- duplicate DOI reuse rather than a second item
- preservation of a pre-existing personal note
- linked PDF absent or outside allowed roots
- stdout, stderr, audit file, and exception strings contain no issued key

- [ ] **Step 5: Run the full automated suite**

Run the Task 7 full-suite command. Expected: every unit and integration test PASS with no real Zotero process required.

- [ ] **Step 6: Commit Task 9**

```powershell
git add skill/managing-zotero/scripts/tests skill/managing-zotero/scripts
git commit -m "test: cover managing-zotero end to end"
```

---

### Task 10: Validate, Security-Audit, and Install the Skill

**Files:**
- Modify: `.gitignore`
- Create outside Git only during validation: `.dev-tools/`
- Install after approval: `C:\Users\Administrator\.codex\skills\managing-zotero\`

**Interfaces:**
- Consumes the completed Skill tree.
- Produces a validated installed copy; no Zotero library mutation occurs in this task.

- [ ] **Step 1: Run all tests from a clean process**

Run the Task 7 full-suite command. Expected: all tests PASS.

- [ ] **Step 2: Install the development-only YAML validator dependency under the D project**

After requesting network approval, run:

```powershell
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' `
  -m pip install --target 'D:\codex work\managing-zotero-skill\.dev-tools' 'PyYAML==6.0.2'
```

Add `.dev-tools/` to `.gitignore`; do not copy it into the installed Skill.

- [ ] **Step 3: Run Codex Skill structure validation**

```powershell
$env:PYTHONPATH = 'D:\codex work\managing-zotero-skill\.dev-tools'
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' `
  'C:\Users\Administrator\.codex\skills\.system\skill-creator\scripts\quick_validate.py' `
  'D:\codex work\managing-zotero-skill\skill\managing-zotero'
```

Expected: validation success with no missing metadata or illegal structure.

- [ ] **Step 4: Run production-source safety scans**

Search only `SKILL.md`, `scripts/*.py`, and `references/*.md` for prohibited production behavior. The review must confirm:

- no HTTP method `DELETE`
- no `sqlite3` import or `zotero.sqlite` modification
- no `api.zotero.org` write fallback
- no printed or persisted API key
- no automatic merge, tag clearing, or personal-note replacement
- no direct binary PDF upload in first-version runtime code

Also run `python -m compileall skill\managing-zotero\scripts` and repeat the full test suite.

- [ ] **Step 5: Present the exact install file list and overwrite risk, then pause**

Report every file under `skill\managing-zotero`, the target `C:\Users\Administrator\.codex\skills\managing-zotero`, and whether that target already exists. Do not copy anything until the user explicitly approves installation.

- [ ] **Step 6: Install only after approval and verify the copy**

Copy the finalized Skill tree, excluding `__pycache__`, test cache, behavioral transcripts, `.dev-tools`, and project Git metadata. Verify that the installed target contains `SKILL.md`, `agents/openai.yaml`, all six production Python modules, three reference files, and the HTML template. Rerun `quick_validate.py` against the installed target.

- [ ] **Step 7: Commit Task 10 project changes**

```powershell
git add .gitignore skill/managing-zotero
git commit -m "chore: validate managing-zotero skill"
```

---

### Task 11: Optional Real Zotero Smoke Test With Separate Approval

**Files:**
- Append only: user-approved D-drive task audit file
- Mutate only after explicit preview approval: one new Zotero test Collection and 1–2 test items

**Interfaces:**
- Uses the installed `zotero_local.py` CLI.
- Produces a real-world verification report; this task is skipped unless the user separately approves it.

- [ ] **Step 1: Confirm a current Zotero backup and show the complete smoke-test preview**

The preview must include the proposed test Collection name, test item metadata, tags, note contents, linked-PDF path if any, plan digest, and the fact that the Skill has no delete function. State that test data will remain until the user removes it manually.

- [ ] **Step 2: Obtain explicit Collection-creation approval and run one approved request**

Run `preview-collection`, show the digest, wait for approval, then run `apply`. The user chooses “仅允许一次” in Zotero. Read back and report the created Collection key.

- [ ] **Step 3: Obtain a second explicit approval for 1–2 test items**

Run `preview-items` against the new Collection, show the candidate/dedup/tag/note/attachment diff and digest, wait for approval, then run `apply`. Do not reuse the prior authorization.

- [ ] **Step 4: Verify real state without making corrections automatically**

Read back the Collection, item metadata, tags, Codex child note, linked-file path, versions, and audit lines. If anything differs from the approved preview, report the mismatch and stop; do not retry or patch without another preview and approval.

- [ ] **Step 5: Record the smoke-test result in Git without committing Zotero data or secrets**

Add a short redacted verification note under `docs/` containing only API/schema versions, operation counts, Item/Collection Keys, pass/fail checks, and any user follow-up. Commit it with:

```powershell
git add docs
git commit -m "test: record Zotero smoke verification"
```

If the user declines the smoke test, record no additional file and treat Task 10's simulated verification as the completed installation boundary.
