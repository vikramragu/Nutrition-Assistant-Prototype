# Frontend — Phase 2.7

Evidence for [implementation-plan.md Phase 2.7](./implementation-plan.md).

**Goal:** fill in the Phase 1 shell rather than rebuild it. Zero new dependencies —
`package.json` and `package-lock.json` are byte-identical to before.

---

## 1. What changed

| File | Change |
|---|---|
[`lib/api.ts`](../../../frontend/lib/api.ts) | `Claim.source` widens `null` → `Citation`; `not_in_corpus` joins the response union; `getCorpus()`; `ScopeCategory` gains `personalised_guidance` |
[`app/components/types.ts`](../../../frontend/app/components/types.ts) | `DisplayMessage` gains a `not_in_corpus` variant; `assistant` now carries `documentAnswers[]` |
[`app/components/history.ts`](../../../frontend/app/components/history.ts) | **New.** Pure logic: regroup a reloaded conversation into turns |
[`app/components/NotInCorpusNotice.tsx`](../../../frontend/app/components/NotInCorpusNotice.tsx) | **New.** The coverage refusal, listing what was searched |
[`app/components/SourcesPanel.tsx`](../../../frontend/app/components/SourcesPanel.tsx) | Stops being a stub: the passages behind the selected answer, or the corpus when idle |
[`app/components/MessageList.tsx`](../../../frontend/app/components/MessageList.tsx) | One block per document, headed by publisher and year; numbered citation markers |
[`app/components/ChatWindow.tsx`](../../../frontend/app/components/ChatWindow.tsx) | Owns the citation selection; renders both columns |
[`app/components/RefusalNotice.tsx`](../../../frontend/app/components/RefusalNotice.tsx) | A label for the new `personalised_guidance` category |
[`app/page.tsx`](../../../frontend/app/page.tsx) | Stops rendering `SourcesPanel` directly |

Seven changed, three added — more than the plan's "four changed, one added", and the
difference is worth naming rather than hiding. The plan's list omitted `ChatWindow.tsx`
and `page.tsx`, which have to change for the panel to show anything at all (§2), and
`history.ts` is an extraction made for testability (§4). The binding exit criterion —
**no new npm dependency** — holds.

---

## 2. Where the shared state had to go

The panel shows "the passages behind the selected answer". That is state shared between
`MessageList` (which raises the selection) and `SourcesPanel` (which renders it), and it
has to live next to `messages`, which `ChatWindow` already owns. But `page.tsx` rendered
`SourcesPanel` as a **sibling** of `ChatWindow`, as the two flex children of
`.layout`.

Three options:

| Option | Why not / why |
|---|---|
| Make `page.tsx` a client component and hold the state there | Loses the server-rendered shell for a piece of UI state |
| A new wrapper component owning selection + messages | Clearest separation, but means lifting `messages` out of `ChatWindow` too — a bigger change than the panel needs |
| **`ChatWindow` returns a fragment: chat column + panel** | **Chosen.** The flex layout still receives two children, so `page.module.css` is untouched and `page.tsx` stays a server component |

The cost is that a component called `ChatWindow` renders an `<aside>` it is not named for.
That is recorded in a comment at both ends — in `page.tsx` where the panel visibly
disappeared, and at the `return` in `ChatWindow` — because the next person to open
`page.tsx` looking for the sources panel will not find it there.

---

## 3. Design decisions that are not cosmetic

### 3.1 Two documents must look like two documents

Each document answer is its own `<article>` with a solid border, its own background, and a
header carrying **publisher and year** before anything else. The blocks are separated by a
real 12px gap, not a hairline: adjacent bordered cards with no space between them read as
one panel, and visual merging would undo the one-call-per-document guarantee the backend
goes to some trouble to make structural.

When a turn has more than one block, a line underneath says so explicitly — *"2 documents
answered separately. Where they differ, both are shown — neither is presented as the
winner."* That sentence exists because [the Phase 2.8 run](./prompt-inversion-regression.md)
produced a real disagreement (WHO: protein 10–15% of energy; USDA: 1.2–1.6 g/kg) and a
reader who does not notice the two headers would take the second number as a correction of
the first.

### 3.2 The markers are on the claims, not mid-sentence

The plan says "inline citation markers select a source in the panel". They are numbered
buttons on each claim rather than superscripts inside the answer prose, because **the
backend does not say where in the prose a claim came from** — `claims[]` are standalone
statements, not offsets. Placing a marker mid-sentence would mean guessing which clause it
belongs to, and a citation marker pointing at the wrong clause is worse than one sitting
beside the claim it actually cites.

Numbering runs across the whole turn, so `[3]` means the same passage in the message and in
the panel even when it is the first claim of the second document.

### 3.3 Four states, distinguishable without colour

| State | Shape |
|---|---|
| Answer | Left-aligned bordered card(s), publisher/year header, solid border |
| Policy refusal | Centred, amber `--warning-*`, **solid** border, "outside scope" badge |
| Coverage refusal | Centred, green `--accent-tint`, **dashed** border, "not in the guidance I searched" badge |
| Error | Centred, red `--danger-*`, solid border |

The two centred notices differ by **border style and badge text** as well as hue. Hue alone
would make the brief's "two refusals must be distinguishable" fail for a red-green colour
deficiency and in any monochrome screenshot — and these two mean opposite things: one says
*the assistant will not*, the other says *the documents do not*.

### 3.4 The panel is never empty when an answer is selected

Guaranteed upstream rather than handled here: the backend's citation validator rejects any
answer with no claims, and every claim carries a citation. So a selected turn always has at
least one passage. The panel's idle state — no answer selected — lists the corpus from
`GET /corpus`, so "what can I even ask this?" is answerable before asking.

A coverage refusal clears the selection deliberately, sending the panel back to the corpus
list: the user has just asked for something outside it, and the list of seven documents is
the most useful thing on screen at that moment.

### 3.5 Three small truthfulness details

- **`year: null` renders as "year not stated"**, never as a blank or a guess. Three corpus
  documents state no publication year and the brief forbids inventing one; an empty gap is
  something a reader fills in themselves.
- **`page_from: 0` renders as no page at all.** Zero means *not paginated* — the WHO HTML
  source. "page 0" is wrong and "page 1" is a fabrication.
- **The quoted passage is shown, not just linked.** A citation you have to go and open is
  one most people will not check. Long passages scroll within the card rather than being
  truncated, because the chunker deliberately keeps some tables whole.

---

## 4. What was verified, and how

No browser automation was available in this session, so the split between what is proven
and what is not is worth being exact about.

### 4.1 Proven mechanically

**The live JSON matches `lib/api.ts` field by field.** A mismatched key does not crash
React — it renders `undefined`, or an empty block, and looks like a styling problem. So the
field names were extracted from the TypeScript source and compared against real payloads
from a running backend:

```text
GET  /corpus                        CorpusResponse, CorpusDocument            ok
POST /chat -> refused               ChatRefusedResponse                       ok
POST /chat -> not_in_corpus         ChatNotInCorpusResponse, DocumentRef      ok
POST /chat -> answer                ChatAnswerResponse, DocumentAnswer,
                                    Claim, Citation, DocumentRef              ok
GET  /conversations/{id}            ConversationRead, MessageRead,
                                    ClaimRead, Citation                       ok
```

The answer used was *"How much protein does an adult need daily?"* — the two-document case
from the Phase 2.8 run. It came back as WHO (2 claims) + USDA & HHS (1 claim), every
citation naming its own document, every one carrying a quote and an `http` URL.

**The reload grouping was run against that real payload.** This is the only non-trivial
logic in the frontend and the only part whose failure is invisible — two documents merged
into one block, or citations missing after a refresh, raise nothing. It was extracted into
[`history.ts`](../../../frontend/app/components/history.ts) (no JSX, so it is directly
runnable), compiled with the project's own `tsc`, and checked:

```text
real payload: 3 rows -> 2 entries: user, assistant
  ok  3 rows become 2 entries (question + one turn)
  ok  the turn holds BOTH documents — World Health Organization | USDA & HHS
  ok  the two blocks are different documents
  ok  WHO / USDA: every claim cited, every citation names its own document,
      every citation has a quote and a working link, year present or explicitly null
  ok  default selection points at this turn
  ok  a Phase 1 uncited row is dropped, not rendered uncited
  ok  and creates no phantom timeline entry
```

The last two matter beyond this corpus: a Phase 1 row has no document and no citation, and
rendering it as an uncited block would put exactly the thing this milestone forbids back on
screen. It is dropped instead.

**Build, typecheck and lint are clean**, the page serves HTTP 200, and the SSR shell
contains the title and the panel's idle heading with no dev-server errors. The backend suite
is still 180 passing.

### 4.2 Not proven

**Nothing here was looked at.** Layout, spacing, the dark-mode palette, whether two blocks
genuinely *read* as two blocks, whether the marker buttons are comfortable to hit, and
whether the panel's `max-height` scrolls as intended rather than stretching the page — all
of that is reasoned from the CSS and unverified by eye.

The four exit criteria about appearance are therefore **claimed on the markup and CSS, not
on a screenshot**:

```bash
# backend
cd backend && .venv/bin/python -m uvicorn main:app --port 8000
# frontend
cd frontend && npm run dev     # http://localhost:3000
```

Questions that exercise each state: *"How much protein does an adult need daily?"* (two
blocks), *"What is the best creatine dose for muscle gain?"* (coverage refusal, free),
*"How many calories should I eat to lose 10 pounds?"* (policy refusal, free), then refresh
the page (history with citations).

---

## 5. Exit criteria

| Criterion | |
|---|---|
| Two-document answers render as two visibly separate blocks | **[x]** markup + CSS: separate `<article>`s, own borders, 12px gap, publisher/year headers; grouping verified against the real two-document payload. **Not eyeballed** — §4.2 |
| Every claim's citation shows document name, publisher, year, and a working link | **[x]** Verified on live data: every citation carried a name, publisher, `year` (or explicit null), an `http` URL and a quote |
| Answer, policy refusal, coverage refusal and generic error are four distinguishable states | **[x]** Four distinct components/classes, differing in alignment, border style and badge text as well as hue — §3.3. **Not eyeballed** |
| Sources panel never shows an empty state when an answer is selected | **[x]** Structural: the backend rejects an answer with no claims and every claim carries a citation, so a selected turn always has a passage — §3.4 |
| Page refresh reloads history with citations intact | **[x]** `GET /conversations/{id}` → grouping → two cited blocks, verified on the real payload |
| No new npm dependency added | **[x]** `package.json` and `package-lock.json` unchanged |

---

## 6. Deliberately not built

- **The document-filter UI.** `sendChatMessage` carries the optional `document_filter`
  parameter so the client matches the wire contract, but nothing selects it. Retrieval
  filtered to one document is a backend criterion (2.4) and is tested there; no 2.7
  criterion asks for the control, and adding a picker would be the first piece of UI in this
  phase that nothing in the brief requires.
- **A frontend test runner.** Jest or Vitest would be a new dependency, which the exit
  criteria forbid. The pure logic was verified by compiling it with the project's existing
  `tsc` and running it under `node` (§4.1) — real verification, but it is a one-off script
  rather than a suite that runs in CI. If frontend tests become worth having, that is a
  deliberate dependency decision, not something to smuggle in here.
- **Streaming, optimistic rendering, retry.** Phase 1 had none and the brief asks for none.
