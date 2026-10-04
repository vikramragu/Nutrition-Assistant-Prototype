# Dietary Guidance RAG Chatbot — Problem Statement (Phase 2)

> Expanded from [problemStatement.txt](problemStatement.txt). **That file is the authoritative brief;
> this document is the working interpretation of it.** Where this document adds detail the brief did
> not specify, the addition is marked **[assumption]** or listed under [Open questions](#11-open-questions).
> Where the two disagree, the brief wins.

This is an **additive** milestone. The Phase 1 prototype — [../../problemStatement.md](../../problemStatement.md),
[../../architecture.md](../../architecture.md) — stays in place: same frontend, same backend, same
code-level scope guard, same deployment. What changes is where answers come from, and what
`claims[].source` contains.

---

## 1. Summary

Build a retrieval layer under the Phase 1 assistant so that it answers **only** from official public
dietary guidance documents.

Three properties define the system:

1. **Grounded.** Answers come from retrieved document chunks, not from model priors.
2. **Cited.** Every claim carries a citation: document name, publisher, year, and a link.
3. **Honest about its limits.** When the corpus doesn't cover a question, the assistant says so and
   names what it searched.

---

## 2. Why this problem

### 2.1 The symptoms are already written down

Phase 1's failure log ([../../failure-log.md](../../failure-log.md)) recorded seven findings. All
three *unresolved* finding types are grounding failures — the brief's "numbers that move between
runs, authorities cited that never said it, claims with nothing behind them":

| Phase 1 finding | Count | What it actually was |
|---|---|---|
| `unverifiable_source` | 3 | The model named "the Institute of Medicine," "the FDA," "EFSA," "WHO" in answer prose as the authority behind a number. None of it checkable. |
| `inconsistent_number` | 2 | Protein and water figures moved between runs of the identical question, with no stated reason. |
| `unsupported_claim` | 2 | Contested topics (air frying, dairy and inflammation) stated as settled fact. |

Phase 1 patched the first by **forbidding** attribution — [`prompts/system_prompt.md`](../../../backend/prompts/system_prompt.md)
instructs the model never to name an organization as a source, precisely because a named source the
user cannot check is worse than none. The other two were recorded as accepted limitations, because no
mechanism existed to fix them. A model recalling a number from training has nothing to be consistent
with.

Retrieval addresses all three at once: numbers stop drifting because they are read off a fixed
passage, attributions become checkable because they point at a real document, and contested topics
inherit whatever hedging the source document itself uses.

> ⚠️ **This milestone inverts a live Phase 1 rule.** Phase 1 banned attribution because attribution
> was unverifiable; Phase 2 requires it because it finally is verifiable. Per
> [../../eval.md §2.3](../../eval.md), that system-prompt edit requires a full regression run before
> it is considered done.

### 2.2 The guidance exists and nobody reads it

Health authorities, national nutrition institutes, and food safety regulators publish long, careful,
thorough documents on exactly these questions. But there is **no API** — the content is written prose
with embedded tables — and the documents are long and unappealing, so almost nobody reads them.

That gap, high-quality prose nobody will sit down and read, is precisely what RAG is for. The
retrieval layer turns a 200-page PDF into an answerable surface without paraphrasing away the
authority of the source.

---

## 3. Role in the larger project

This component answers questions of two shapes:

- **"Is this a reasonable way to eat?"** — dietary pattern guidance.
- **"How long can I keep this in the fridge?"** — food safety and storage guidance.

### Explicit boundary with Milestone 3

**Nutrient numbers for individual foods do not belong here.** "How much protein is in 100 g of
lentils" is structured data, sourced from a structured nutrient database in Milestone 3 — not from
prose retrieval. This system handles *guidance*; that system handles *figures for specific foods*.

Keeping the boundary clean matters: prose documents occasionally mention a nutrient figure in
passing, and a retrieval system that happily answered from those mentions would produce worse answers
than the structured database while looking equally confident.

---

## 4. What to build

### 4.1 Corpus

Gather **5 to 7 public guidance documents** from recognised authorities.

**Eligible publishers:** national nutrition institutes, food safety regulators, international health bodies.

**Constraints:**
- **Written prose only.** If a source has a clean API behind it, it belongs in the structured-data
  milestone, not in this corpus.
- Documents must be **public**.

**Required metadata, stored with every document:**

| Field | Notes |
|---|---|
| Publisher | The issuing authority |
| Year | Publication/edition year of the version retrieved |
| Source URL | Where the document was obtained |
| Retrieval date | When it was downloaded — guidance gets revised |

Retrieval date is not bookkeeping for its own sake: guidance documents are superseded, and a citation
without a retrieval date can't be audited later.

#### Candidate corpus — 7 public documents **[assumption]**

**FROZEN 2026-10-04** at the close of [implementation-plan.md Phase 2.0](./implementation-plan.md).
Each document is **23 pages or fewer**, all PDF. This resolves
[open questions 1 and 8](#11-open-questions).

> **Verification status:** every URL was fetched end-to-end on **2026-10-04**, the bytes hashed, and
> the file parsed with PyMuPDF. Page counts come from parsing the downloaded file, not from the
> publisher's description; identity was read from each file's own metadata and first-page text.
> Re-fetch at ingestion and record the real retrieval dates then — the `sha256` values below are the
> check that the bytes have not changed since.

| # | Document | Publisher | Year | `year_source` | Verified 2026-10-04 | `sha256` (first 16) |
|---|---|---|---|---|---|---|
| 1 | [Policy Brief: Promotion of "My Plate for the Day" and physical activity](https://www.nin.res.in/brief/Policy%20Brief%20My%20Plate%20J18%2024.pdf) | ICMR–National Institute of Nutrition (India) | 2024 | `pdf_creation_date` (2024-01-18) | **4 pp**, 656 KB, 10,518 ch | `f7e6792056c9c9f7` |
| 2 | [Dietary Guidelines for Americans, 2025–2030](https://cdn.realfood.gov/DGA.pdf) | USDA & HHS (United States) | 2025 | `document_text` | **10 pp**, 3.26 MB, 17,798 ch | `c34f1bec5c941626` |
| 3 | [Your guide to healthy eating: use the Food Pyramid](https://assets.gov.ie/7649/3049964a47cb405fa20ea8d96bf50c91.pdf) | Department of Health (Ireland) | 2016 | `pdf_creation_date` (2016-12-05) | **7 pp**, 6.37 MB, 10,242 ch | `4a85efe768f7d218` |
| 4 | [Healthy diet](https://www.who.int/news-room/fact-sheets/detail/healthy-diet) | World Health Organization | 2026 | `page_last_updated` (26 January 2026) | **HTML**, 130 KB, 13 sections | *not pinned — revised in place* |
| 5 | [Prevention of Foodborne Disease: The Five Keys to Safer Food](https://www.who.int/docs/default-source/wpro---documents/posters/food-safety/the-five-keys-to-safer-food-brochure.pdf) | World Health Organization | 2013 | `pdf_creation_date` (2013-12-11) | **2 pp**, 2.01 MB, 3,639 ch | `823d59b655d4475b` |
| 6 | [Guidance Note on Handling and Disposal of Used Cooking Oil](https://fssai.gov.in/upload/uploadfiles/files/Guidance_Note_Used_Oil_12_11_2018.pdf) | FSSAI (India) | 2018 | `pdf_creation_date` (2018-11-12) | **4 pp**, 129 KB, 7,949 ch | `04e020c1a45368e7` |
| 7 | [Food Safety: Temperature control of potentially hazardous foods](https://www.foodstandards.gov.au/sites/default/files/publications/Documents/FSTemp_control_Edition_for_printing.pdf) | Food Standards Australia New Zealand | 2002 | `pdf_creation_date` (2002-10-23) | **23 pp**, 160 KB, 41,402 ch | `0a8f42bd53f8b86d` |

Total: **50 PDF pages plus one web page; 103,394 characters across 105 chunks.** Category spread
covers all three eligible publisher types — four nutrition authorities (1–4), three food safety
sources (5–7). The two worked examples have sources: **cooking oil** via documents 4 (unsaturated
fats, trans fat), 6 (reuse and disposal) and 3 (fats on the pyramid); the **fridge/storage question**
via documents 7 and 5.

**Document 4 was swapped during Phase 2.1**, from WHO's *Fact Sheet N°394* PDF (August 2018) to WHO's
current *Healthy diet* page (26 January 2026). This was not a format preference — the 2018 edition is
**superseded**. The 2026 page no longer contains `"less than 5%"` or `"30% of total energy"`, two
figures the PDF states, so the corpus was carrying guidance WHO has since revised. That is
[§10](#10-design-risks)'s stale-guidance risk, found live rather than hypothetically.

Being an HTML source, it has **no `sha256` pin and no `expected_pages`**: the page is revised in place
under a stable URL, so a content hash would fail on every legitimate update. Its version identity is
`page_last_updated` + `retrieval_date`, exactly as [architecture.md §5.1](./architecture.md)
specifies. The cost is that completeness cannot be asserted for this one document — WHO serves no
`Content-Length` either — and ingestion warns about it on every run rather than staying silent.

**Document 7 was swapped during Phase 2.0.** The original entry — FSANZ's *Storing food safely* HTML
page — yielded 2,777 characters of business-compliance text ("If you're a food business…", Standard
3.2.2) with no tables. It was replaced by FSANZ's temperature-control PDF, which raised the page
ceiling from 15 to 23 and is the only remaining FSANZ entry.

Two honest qualifications on that swap:

- **It does not deliver the tables that were its stated justification.** PyMuPDF's `find_tables()`
  reports two tables, and inspection shows **both are bulleted prose misread as 5-column grids**. The
  "dense time/temperature tables" description came from an earlier access check and does not survive
  verification. §4.2's chunking deliverable still has almost no real tables to demonstrate on.
- **What it does deliver** is the substance: the **2 hour/4 hour guide** (appearing 7 times) and the
  danger-zone definition *"Room temperature means temperatures in the range above 5°C and below
  60°C"* — directly answering the question shape §3 names. 41,402 characters of relevant guidance
  replacing 2,777 of compliance text is the real gain.

**Age caveat:** document 7 is from 2002. It remains FSANZ's published guidance at the verified URL,
but it is the oldest item in the corpus by eleven years and the sharpest instance of the
stale-guidance risk in §10. The citation will show the year, so a reader can judge it.

Document identity was confirmed from inside each file, not inferred from its URL — document 2's
embedded PDF title reads *"Dietary Guidelines for Americans, 2025–2030"*; document 4's first page
reads *"FACT SHEET N°394, UPDATED AUGUST 2018"*; document 6's reads *"HANDLING AND DISPOSAL OF USED
COOKING OIL"*. Years for documents 1, 3 and 5 come from the PDF creation date — verified as
2024-01-18, 2016-12-05 and 2013-12-11 respectively — because those three state no publication year in
their text. **Store the source of the year, not just the year.**

**Available as a swap, not an addition:** *The Eatwell Guide booklet* (Public Health England, 2018) —
verified 2026-10-04 as PDF, 7.52 MB, **12 pp**, 30,192 characters, embedded title *"The Eatwell
Guide"*, created 2018-09-21, at
<https://assets.publishing.service.gov.uk/media/5ba8a50540f0b605084c9501/Eatwell_Guide_booklet_2018v4.pdf>.
Not in the seven above — adding it would make eight, and the brief caps the corpus at seven. It is
general dietary-pattern guidance, so it overlaps documents 2 and 3 rather than adding coverage.

#### What this corpus costs

The ≤23-page ceiling is **[assumption]** — not in the brief — and it is a real trade, not a free
optimisation:

- **The chunking deliverable is still thin.** §4.2's central difficulty is tables and numbered
  recommendations being cut in half. Raising the ceiling to admit document 7 was meant to fix this,
  and **it did not**: PyMuPDF's table detection finds two candidates in that document and both are
  bulleted prose misread as grids. The corpus contains no verified machine-readable table. The README
  must still state a chunking strategy and its cost, but the interesting failure mode remains largely
  *absent from the corpus* rather than solved.
- **Section-heading metadata is thinner than assumed.** A font-size sweep on 2026-10-04 found most
  heading candidates are cover-title fragments, and document 4 yields **one heading across six
  pages**. See [architecture.md §5.3](./architecture.md) — several documents need a non-heading
  fallback as their primary path, not as an edge case.
- **The not-in-corpus refusal will fire often.** 50 pages plus one web page cannot cover much of
  food, nutrition and food safety. That makes the refusal path easy to demonstrate and the threshold
  ([open question 2](#11-open-questions)) the most load-bearing decision in the build.
- **The brief's premise is still only partly represented.** *"Health authorities publish long,
  careful, boring PDFs that nobody reads"* is the stated reason RAG is the right tool here. At 56
  pages this is closer than the original 33, but six of seven documents remain leaflets. The full
  editions are listed in the access check and remain the right sources if depth matters more than
  ingest speed.
- **The ceiling is not buying measurable savings.** Page count is a reading cost, not a pipeline cost.
  The largest excluded document (148 pp) is on the order of 250–400 chunks; embedding that locally
  takes under a minute and costs nothing. The cap is worth keeping only if the goal is a corpus a
  human reviewer can read end to end — a legitimate goal, but a different one from the brief's.

#### Known ingestion issues

*Verified 2026-10-04 during [Phase 2.0](./implementation-plan.md).*

- **Two extractors, and they solve opposite problems.** Six documents are PDFs; document 4 is HTML.
  A PDF hides its structure — headings must be *inferred* from font size and boldness. HTML states
  its structure outright, so headings are *read* from `<h2>`/`<h3>` and the work is discarding nav,
  cookie banners and footers instead. Neither parser can serve the other's format.
- **PyMuPDF handles every document**, including the two with damaged cross-reference tables that
  `pypdf` refuses (`Invalid object in /Pages`) and the three using CID-encoded fonts. `pdfminer.six`
  is defensive insurance, not load-bearing. Use `import pymupdf`; the `fitz` alias is deprecated.
- **Do not trust `find_tables()`.** It reports bulleted lists as 5-column tables on at least two pages
  of document 7. §4.2's "never split a table" rule cannot be driven by naive detection, or ordinary
  prose will be treated as an unsplittable block.
- **Set a generous download timeout and verify the byte count.** Document 3 (6.37 MB) truncated under
  a 45-second limit on 2026-10-04 and parsed as 5 pages instead of 7. A truncated PDF does not
  announce itself. **Document 5 serves no `Content-Length` header at all**, so the assertion must
  tolerate its absence and fall back to the `sha256` above.
- **Documents 1, 3 and 5 are graphic-heavy** relative to length (document 3 is 6.37 MB for 7 pages).
  Content existing only inside a figure will not be indexed.
- **Documents 3 and the Eatwell candidate have damaged cross-reference tables.** Both failed to open
  with `pypdf` (`Invalid object in /Pages`) and needed `pdfminer.six`, which recovered them fully.
  Pick a parser that tolerates a broken xref, or these two silently contribute zero chunks.
- **Set a generous download timeout.** Documents 3 (6.37 MB) and the Eatwell candidate (7.52 MB)
  both truncated under a 45-second limit on 2026-10-04, producing files that parsed as 5 and 4 pages
  instead of 7 and 12. A truncated PDF does not announce itself — it parses, returns fewer pages, and
  the corpus is quietly short. **Verify the downloaded byte count against `Content-Length`** at
  ingestion.

#### Access check (last run 2026-10-04)

Rule applied: a URL stays only if it returns HTTP 200 **and** serves the guidance content — validated
as the expected PDF (header, parsed page count, embedded metadata, first-page text) or as HTML whose
body text was extracted and read back. A landing page, redirect stub, or error body is not content.

**Over the 23-page ceiling** — the authoritative full editions, worth returning to if depth beats
brevity (see [open question 1](#11-open-questions)). Page counts as recorded on 2026-10-03; only the
one marked ✓ was re-parsed on 2026-10-04:

| Document | Pages | URL |
|---|---|---|
| Dietary Guidelines for Indians (ICMR-NIN, 2024) | 148 | <https://nin.res.in/dietaryguidelines/pdfjs/locale/DGI_2024.pdf> |
| WHO guideline on saturated and trans-fatty acid intake (2023) | 134 | <https://iris.who.int/server/api/core/bitstreams/463fa93e-6c17-4e5b-a4d7-928354ea34c3/content> |
| FSAI, Healthy eating, food safety and food legislation (Ireland) | 90 | <https://www.fsai.ie/getmedia/be0e5ec0-c1ce-40b9-8f66-51bb834bac44/10507_fsai_healthy-eating-guidelines-accessible-fa1.pdf> |
| Canada's Dietary Guidelines (Health Canada, 2019) | 62 | <https://www.canada.ca/content/dam/hc-sc/documents/services/food-guide/explore/dietary-guidelines/dietary-guidelines.pdf> |
| Five keys to safer food manual (WHO, 2006) | 30 | <https://iris.who.int/server/api/core/bitstreams/dadab0b0-98e4-41a3-b432-e984d79f15a3/content> |
| WHO SFA/TFA guideline — official summary edition ✓ | 24 | <https://iris.who.int/server/api/core/bitstreams/2aed5a4a-b7a2-4a56-b1ab-1662bb662389/content> |

The WHO SFA/TFA summary at 24 pages is now the single nearest miss — one page over the ceiling, live
and verified. FSANZ's temperature-control PDF (23 pp), previously listed here, was **promoted into the
corpus as document 7** during Phase 2.0; see the manifest above.

**Could not be verified** (2026-10-03 unless noted):

| URL | Result |
|---|---|
| `foodsafety.gov/food-safety-charts/cold-food-storage-charts` | HTTP 403 to every attempt, with and without a browser user-agent |
| `fsis.usda.gov/…/leftovers-and-food-safety` | HTTP 403 |
| `fda.gov/media/74435/download` | 10-byte "Not found" body for paths that do exist — an intercepted response, not a real 404 |
| `apps.who.int/iris/bitstream/handle/10665/43546/…_eng.pdf` | 200, but 755 bytes of HTML — a legacy IRIS redirect stub |
| `iris.who.int/handle/10665/375034`, `iris.who.int/rest/api/pid/find` | 755-byte JS shell and HTTP 403 — bitstream UUIDs scraped from WHO item pages instead |
| `nhs.uk/…/how-to-store-food-and-leftovers/` | HTTP 404 — page moved |
| `canada.ca/…/safe-food-storage.html` — carries the per-food fridge/freezer table | **Re-confirmed failing 2026-10-04**: connection fails outright (`curl` exit before any response) over both HTTP/2 and HTTP/1.1, compressed or not. The same URLs are reported to download fine from a browser-style client. |
| `food.gov.uk/safety-hygiene/chilling` | **2026-10-04**: HTTP 200 but only 1,731 characters of body text — too thin to be worth a corpus slot |
| `safefood.net/food-safety/fridge-storage` | **2026-10-04**: HTTP 403 |
| `mpi.govt.nz/food-safety-home/food-safety-tips-in-the-home/` | **2026-10-04**: HTTP 200, 212 bytes, zero extractable text — an empty shell |
| `efsa.europa.eu/en/topics/topic/food-safety-home`, `fsai.ie/…/food-safety-at-home.pdf` | **2026-10-04**: HTTP 404 |

The three US government hosts and canada.ca are almost certainly live for an ordinary browser; the
block is the checking environment's egress, not the publishers. That does not make them verified, so
they stay out. **Health Canada's *Safe food storage* page is the single most valuable blocked
source** — it carries the per-food fridge/freezer table that directly answers the §3 question shape —
so it is worth one manual download attempt from a real browser before the corpus is frozen.

### 4.2 Chunking

Every chunk must carry, as metadata:

- Document name
- Publisher
- Year
- Section heading

These travel with the chunk through retrieval and into the citation. A chunk that has lost its
provenance cannot be cited, and per §7 a claim that cannot be cited does not ship.

**The central difficulty (per the brief):** these documents are full of **tables** and **numbered
recommendations**. Fixed-size chunking will cut both in half — splitting a storage-time table across
two chunks, or severing recommendation 4 from its own text. A chunk containing half a table is worse
than no chunk, because it retrieves confidently and answers wrongly. A chunk reading `1.2–2.0 g/kg/day`
without the row header naming who that applies to is the Phase 1 `inconsistent_number` failure
reproduced with extra steps.

**[assumption]** The brief's warning describes the *full* guidance editions. The ≤23-page corpus in
§4.1 mostly inverts the problem: six of the seven documents are 2 to 10 pages, where the risk is not
bisection but **granularity** — a whole document fitting in one or two chunks, so retrieval returns
the leaflet rather than the passage, and the section-heading field is empty because the source has no
sections. Expect to chunk by paragraph or by a brochure's own numbered keys. Document 7 (23 pp) is
the one entry long enough for bisection to be a live concern; if the corpus later returns to the full
editions, it becomes the dominant one again.

**Deliverable:** the README must state what chunking strategy was chosen **and what it cost**. Every
strategy has a cost; the requirement is to name it, not to avoid it.

### 4.3 Retrieval

A vector index over the chunks, supporting two retrieval modes:

1. **Across all documents** — the default.
2. **Filtered to one named document** — "what does *[specific document]* say about X".

The filtered mode is not a convenience feature; it is what makes per-document answering (§4.5)
possible.

### 4.4 Answer layer

The assistant answers **only** from retrieved chunks. Model knowledge is not a source.

Every claim carries a citation showing **document name**, **publisher**, **year**, and **a link**.

Citations are **per-claim, not per-answer**. An answer with three claims drawn from two documents
needs its citations attached such that a reader can tell which document supports which claim.

### 4.5 Cross-document questions

Some questions have **two or more documents with something to say**. The brief's example: **cooking
oil**, where a nutrition institute speaks to it (fat composition, dietary pattern) and a food safety
regulator also speaks to it (reuse, disposal, storage).

- ✅ Answer **per document**, with **separate citations** for each.
- ❌ **Never blend two sources into one claim** about what "the guidelines say."
- When two documents **disagree, show both** with their publishers and years. **Do not pick a winner.**

Blending is the specific failure mode to design against. Two authorities may differ in emphasis,
scope, or recommendation; merging them into a single synthesised voice invents a consensus that
doesn't exist and destroys the reader's ability to check it. This is a correctness requirement, not a
presentation preference.

### 4.6 Two kinds of refusal

Both are required. They are different mechanisms with different messages, and must be
distinguishable by the user.

#### A. Not in the corpus

**Trigger:** the retrieved chunks don't hold the answer.

**Behaviour:** the assistant says the guidance doesn't cover it, **and names what it searched.**

Naming what was searched is what makes this refusal useful rather than merely safe — it tells the
user whether the gap is in the corpus or in their question.

#### B. Out of scope by design

**Trigger:** the question falls into a category this system will not answer regardless of what the
corpus contains:

- No medical advice
- No calorie targets
- No weight targets
- Nothing about what anyone should weigh

**Behaviour:** decline, and point the person to a qualified professional.

**Enforcement: in code, not in the prompt.** Consistent with Phase 1 —
[`services/scope_guard.py`](../../../backend/services/scope_guard.py) and its pre-model/post-model
checks carry over in principle. A prompt-level guardrail is a request; a code-level guardrail is a
constraint. This distinction is explicit in the brief and is a grading criterion, not a stylistic
preference.

Retrieval does not soften this boundary: a guidance document containing a calorie figure does not
make it acceptable to hand that figure to a user as a target.

### 4.7 Filling in the shell

**Do not rebuild the frontend or backend.** Phase 1 deliberately left the seams open; this milestone
uses them.

- **The sources panel** ([`SourcesPanel.tsx`](../../../frontend/app/components/SourcesPanel.tsx)) is
  currently a presentational stub that always renders its empty state. It now shows the chunks behind
  each answer.
- **`claims[].source`** is currently `Literal[None]` in [`db/schemas.py`](../../../backend/db/schemas.py),
  mirrored in the JSON Schema sent to the model in
  [`services/model_client.py`](../../../backend/services/model_client.py) and in the TypeScript type
  in [`lib/api.ts`](../../../frontend/lib/api.ts). **All three must widen together.** It now carries a
  real citation.
- **If the schema doesn't fit real citations, change it** — and note what changed and why. A citation
  needs four fields (document, publisher, year, link), not a string. Widening is expected; silently
  stuffing a formatted string into the existing field is not.

The `claims.source` DB column is already `nullable=True` Text
([`db/models.py`](../../../backend/db/models.py)), so storage needs no destructive migration.

---

## 5. Technical requirements

Same stack as Phase 1 for everything not listed here. The brief names these options explicitly:

| Concern | Options |
|---|---|
| PDF parsing | PyMuPDF, Unstructured, Docling, LlamaParse |
| Chunking | LangChain text splitters, or a custom splitter breaking on section headings |
| Embeddings | OpenAI `text-embedding-3-small`, Cohere embed, or `sentence-transformers` (local, free) |
| Vector store | Supabase pgvector, Pinecone, Qdrant |
| Orchestration | LangChain, or plain Python to keep every step visible |

---

## 6. Documentation requirements

The README must state:

- [ ] Chunk size
- [ ] Overlap
- [ ] Embedding model
- [ ] Index type
- [ ] `k` value
- [ ] The chunking strategy chosen, and what it cost (§4.2)
- [ ] Any schema change made to accommodate citations, and why (§4.7)

---

## 7. Rules

The brief's non-negotiable constraints, each independently verifiable:

- Answers come from retrieved text only. **Model knowledge is not a source.**
- Every claim carries a citation. **No citation means the claim doesn't ship.**
- **Population-level guidance stays population-level.** The assistant does not turn it into a personal
  recommendation.
- No calorie targets, no weight targets, no medical advice, **anywhere in the conversation**.
- When two documents disagree, show both with their publishers and years. Don't pick a winner.
- 5 to 7 documents.
- State chunk size, overlap, embedding model, index type and `k` value in the README.

---

## 8. Acceptance criteria

**Corpus**
- [ ] 5–7 prose guidance documents from recognised authorities are in the corpus.
- [ ] Every document is written prose from a publisher with no clean API alternative.
- [ ] Each document stores publisher, year, source URL, and retrieval date — with **no year invented**
      for a source that does not state one.
- [ ] Every source URL returns HTTP 200 and serves the guidance content itself — not a landing page,
      redirect stub, or error body.
- [ ] HTML sources have boilerplate stripped before chunking, and store their "last updated" date
      alongside the retrieval date.

**Chunking**
- [ ] Each chunk carries document name, publisher, year, and section heading.
- [ ] README states the chunking strategy and its costs.

**Retrieval**
- [ ] Vector index supports retrieval across all documents.
- [ ] Vector index supports retrieval filtered to a single named document.

**Answer layer**
- [ ] Answers are generated only from retrieved chunks.
- [ ] Every claim carries document name, publisher, year, and link.
- [ ] No claim ships without a citation.

**Cross-document**
- [ ] A cross-document question (e.g. cooking oil) yields per-document answers with separate citations.
- [ ] Two sources are never blended into a single claim.
- [ ] Disagreement between documents is shown with both publishers and years, no winner picked.

**Refusals**
- [ ] Not-in-corpus refusal fires and **names what was searched**.
- [ ] Out-of-scope refusal fires for medical advice, calorie targets, weight targets, and
      "what should I weigh" questions.
- [ ] Out-of-scope refusal is enforced **in code**, demonstrably, not only via prompt instructions.
- [ ] The two refusal kinds are distinguishable from each other by the user.

**Filling in the shell**
- [ ] The frontend and backend are extended, not rebuilt.
- [ ] The sources panel shows the chunks behind each answer.
- [ ] `claims[].source` carries a real citation instead of `null`.
- [ ] Any schema change is documented with its rationale.

**Boundaries**
- [ ] Population-level guidance is never converted into a personal recommendation.

**Documentation**
- [ ] Chunk size, overlap, embedding model, index type, and `k` value are stated in the README.

---

## 9. Non-goals

| Not in scope | Where it belongs / why |
|---|---|
| Nutrient values for individual foods | Structured database, Milestone 3 |
| Medical advice | Out of scope by design; refer to a professional |
| Calorie or weight targets | Out of scope by design |
| Any claim about what a person should weigh | Out of scope by design |
| Sources with a clean API | Not prose; belongs with structured data |
| Synthesising a single cross-authority consensus | Explicitly forbidden — answer per document |
| Rebuilding the frontend or backend | Phase 1 shell is extended, not replaced (§4.7) |

---

## 10. Design risks

Points where the system is most likely to fail **quietly**:

- **Table bisection.** Fixed-size chunking cutting a food-safety storage table in half. The retrieved
  half answers the question with half the information. The brief's own warning, and the main chunking
  concern for the full editions.
- **Severed numbered recommendations.** Same mechanism, different content: "Recommendation 7" split
  from its body, or a list item retrieved without the qualifying sentence above it.
- **Implicit blending.** A generation step that merges two retrieved documents into fluent prose by
  default. Fluency is the enemy here — the answer reads better and is less trustworthy.
- **Scope leakage via the corpus.** Guidance documents do sometimes discuss calories or weight. A
  retrieval-only guardrail would let those chunks answer out-of-scope questions — exactly why the
  brief requires code-level enforcement.
- **Stale guidance.** A cited document superseded since retrieval. Mitigated by storing retrieval
  date, but storing it is not the same as checking it.
- **Silent empty retrieval.** Low-relevance chunks returned with no relevance floor, producing a
  confident answer where the correct output was the not-in-corpus refusal.
- **Regression from the prompt inversion.** §2.1 — removing Phase 1's "never name a source" rule is a
  system-prompt change, and [../../eval.md §2.3](../../eval.md) requires a full regression run before
  it ships.

---

## 11. Open questions

To be resolved during implementation:

1. ~~**Corpus page ceiling.**~~ **RESOLVED 2026-10-04 (Phase 2.0).** Ceiling set at **23 pages**;
   corpus frozen at seven documents — 50 PDF pages plus one web page, 103,394 extracted characters
   across 105 chunks (§4.1). The next document
   up is the WHO SFA/TFA summary at 24 pages — one page over — and the full editions remain listed in
   the access check if depth later beats brevity.
2. **Relevance threshold.** What score or condition distinguishes "retrieved chunks don't hold the
   answer" from a weak-but-usable match? This threshold *is* the not-in-corpus refusal.
3. **Chunking strategy.** Structure-aware (heading/table boundaries), fixed-size, or hybrid — with the
   cost documented either way.
4. **Out-of-scope classification mechanism.** How the in-code guardrail decides a question is out of
   scope. **[assumption]** this runs *before* retrieval, so out-of-scope questions never reach the
   index — matching Phase 1's pre-model `check_request` gate.
5. **Cross-document detection.** How the system recognises that a question has more than one document
   with something to say, and routes to per-document answering.
6. **Citation schema shape.** Whether `source` becomes a nested object, and how that flows to the
   sources panel (§4.7).
7. **Missing pipeline diagram.** [problemStatement.txt:8](problemStatement.txt) contains a single
   `￼` (U+FFFC) under the heading "The pipeline" — an **image that did not survive the paste**. Recover
   it and either embed it here or transcribe it; it likely specifies the intended ingest → chunk →
   embed → retrieve → generate → cite flow and possibly the refusal branches.
8. ~~**Replacement for document 7.**~~ **RESOLVED 2026-10-04 (Phase 2.0).** The FSANZ *Storing food
   safely* HTML page (2,777 chars, business-facing) was replaced by FSANZ's **temperature-control PDF**
   (23 pp, 41,402 chars), which carries the 2 hour/4 hour guide and the 5–60 °C danger-zone definition.
   Note the caveat in §4.1: it does **not** contain the machine-readable tables that were its stated
   justification. **Health Canada's *Safe food storage*** page — consumer-facing, with a per-food
   fridge/freezer table — remains the better content and is still blocked to scripted clients; it is
   worth a manual browser download if anyone wants to swap it in later.
9. **Table detection is unreliable on this corpus.** PyMuPDF's `find_tables()` reports bulleted prose
   as 5-column tables in document 7. §4.2's "never split a table" rule therefore cannot be driven by
   naive detection — decide in Phase 2.1 whether to use a layout-aware parser, hand-annotate the few
   real tables, or drop the rule and document that choice.

---

## 12. Core rule

Phase 1's core rule was a reliable contract over clever model behaviour. Phase 2 extends it:

> Every claim is traceable to a passage in a real document that a user can open and read. Retrieved
> text is the only source of truth; model knowledge is not. A claim without a citation does not ship,
> a question the corpus cannot answer gets an honest refusal naming what was searched, and two
> authorities are never merged into one voice.

---

## Appendix: source brief

The original brief is preserved verbatim at [problemStatement.txt](problemStatement.txt). Where this
document and the brief disagree, the brief wins.

**Known gap in the source file:** line 8 is an orphaned `￼` character where a pipeline diagram was —
see [open question 7](#11-open-questions).
