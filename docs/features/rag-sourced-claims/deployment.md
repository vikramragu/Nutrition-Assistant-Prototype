# Deployment — Phase 2.9

Evidence and runbook for [implementation-plan.md Phase 2.9](./implementation-plan.md).
Extends [../../deployment.md](../../deployment.md), which set up Railway and Vercel in
Phase 6 and still describes how the services are wired.

**This phase is split in two, and the split is not negotiable.** Everything that can be
built and measured without production credentials is done and verified below. Everything
that needs the Railway or Vercel dashboard — applying the migration, seeding, reading the
memory graph, timing a real cold start — is a runbook in §4 for the owner to run. The
project's working agreement is that no production credential enters a transcript; the
Phase 2.0 pgvector check was run the same way, by hand in the Railway console.

So §5 lists four exit criteria as **open**. They are not done, and nothing here pretends
otherwise.

---

## 1. What changed

| Change | Why |
|---|---|
`services/embeddings.py` — `DEFAULT_CACHE_PATH` / `CACHE_PATH` | One constant for where the weights live, so the build step and the runtime cannot disagree |
`scripts/prefetch_embedding_model.py` — **new** | Downloads the 64 MiB of ONNX at image build time |
`railway.json` — **new** | Sets `buildCommand` so Railpack runs the prefetch |
`main.py` — lifespan + richer `/health` | Model loads at startup; the deploy's configuration is one curl away |
`services/retriever.py` — `RETRIEVAL_K` / `RETRIEVAL_FLOOR` | Tunable on Railway, defaulting to the measured values, with a loud warning on override |
`tests/test_startup.py` — **new**, 9 tests | The deployment properties that are otherwise only wrong in production |
`.gitignore` | Ignore the baked weights; **stop ignoring `eval/runs/`** — see §3.3 |
`requirements-ingest.txt` | Note that PyYAML arrives at runtime anyway, via `huggingface_hub` |

The requirements split the plan asks for **already existed** —
[`requirements-ingest.txt`](../../../backend/requirements-ingest.txt) was written in an
earlier phase with `-r requirements.txt` plus the four parsers. What was missing was any
check that the split actually holds (§2.3).

---

## 2. Measured locally

Every number here is from this machine (macOS, Python 3.14). Linux RSS and Railway's
container will differ, which is exactly why §5 keeps the tier check open.

### 2.1 The weights, baked

```text
$ python scripts/prefetch_embedding_model.py
prefetching BAAI/bge-small-en-v1.5 into backend/.fastembed_cache
ok: 384 dimensions, 17 unique file(s), 67 MB on disk
     66.5 MB  blobs/aa/aaca296c…          (model_optimized.onnx)
      0.7 MB  …/688882a79f…               (tokenizer.json)
```

Cross-checked with `du -sh` → 64 MiB. The model resolves to
`Qdrant/bge-small-en-v1.5-onnx-Q`, the quantised ONNX build fastembed uses for this id.

**The first version of that script reported 200.8 MB.** The HuggingFace cache stores each
blob once and hardlinks it into `snapshots/`, so summing `st_size` over `rglob("*")`
counts the 66 MB model three times. Nothing failed — it just printed a number 3× too large,
in build logs, where it would later have been quoted in an image-size discussion. Now
deduplicated by inode.

### 2.2 Startup and footprint

| Metric | Measured | Note |
|---|---|---|
Import + lifespan + first `/health` 200 | **1.37 s** | Process launch to serving, weights already baked |
Model load alone, warm | **0.32 s** | Matches architecture.md §12.3's 0.35 s |
One query embedding | **7 ms** | |
RSS, model loaded, idle | **343 MB** | |
RSS, after one retrieval request | **351 MB** | |

**351 MB is the number to size the tier against, not 279 MB.** The plan's exit criterion
says "expect ~279 MB RSS for the model alone", and that is what §12.3 measured — the model
in isolation. The whole process, with FastAPI, SQLAlchemy's pool, the Groq client and the
model, is ~350 MB resident and idle. On a 1 GB instance that is comfortable; on 512 MB it
is 70% of the ceiling before any concurrency. Confirming it on the actual tier is §5's
first open item.

### 2.3 The runtime/ingest split, actually tested

A clean venv with **only** `requirements.txt` — 56 packages — then the app imported and
started in it:

```text
absent: pymupdf          absent: pdfminer.six        absent: trafilatura
import main: ok
lifespan + /health: 200  BAAI/bge-small-en-v1.5
```

So the deployed image genuinely cannot parse a PDF, and the app genuinely does not need to.
`corpus.seed` also imports cleanly under runtime-only dependencies, which is what makes the
§4 runbook's "run the seed inside the deployed container" step possible rather than
aspirational.

One surprise: **PyYAML is present** in the runtime venv despite being listed only in
`requirements-ingest.txt`. It arrives via `fastembed → huggingface_hub`. Harmless — the app
never imports it, which `test_importing_the_app_pulls_in_no_ingest_only_dependency` asserts
— but the pin in the ingest file is redundant and now says so.

### 2.4 No secret in the frontend bundle

Scanned `.next/static` and `.next/server` of a production build:

```text
no occurrence of groq / gsk_ / DATABASE_URL / postgres URL in any built asset
NEXT_PUBLIC_* inlined: NEXT_PUBLIC_API_BASE_URL   (only)
inlined value: http://localhost:8000              (the local default)
```

`GROQ_API_KEY` is read only by the Groq SDK from the backend's environment and is never
referenced in any frontend file. Verified in production is §5's last open item, since the
deployed bundle inlines Vercel's value rather than this one.

---

## 3. Decisions

### 3.0 The build config was wrong, and the test said it was fine

Shipped as `nixpacks.toml`. **Railway builds with Railpack**, which does not read that file;
Railway's current schema accepts only `RAILPACK` or `DOCKERFILE`, so Nixpacks is not even a
selectable builder any more. The build step would never have run.

It would not have failed, either — which is the whole point of §6's first risk, written
before this was known: *a skipped build phase does not fail the deploy, it moves the
download to boot*. The service would have gone green and paid 15 s on cold starts whenever
Hugging Face was slow.

Worse, `test_the_build_bakes_the_weights_into_the_image` **passed**. It asserted that
`nixpacks.toml` contained the prefetch command — true, and irrelevant, because it never
checked that the file was the one the platform reads. A test that confirms the contents of
dead config is worse than no test: it converts an open question into a false answer.

Now `railway.json`, documented and in-repo:

```json
{"build": {"builder": "RAILPACK",
           "buildCommand": "python scripts/prefetch_embedding_model.py"}}
```

The builder is pinned **in the same assertion** as the command, so the command cannot go
live under a builder that ignores it. Found on the first real deploy, 2026-10-06.

### 3.1 The baked path is the default, not a required variable

`FASTEMBED_CACHE_PATH` is an *override*. `services.embeddings.CACHE_PATH` already defaults
to `backend/.fastembed_cache`, which is where the build step writes.

The alternative — require the variable, download if unset — fails silently. A forgotten
variable would not error; fastembed would just re-download 64 MiB at boot, turning a 0.3 s
start into a 15 s one that fails whenever Hugging Face is slow. Railway retries a failed
boot, so the real symptom is a redeploy loop whose cause is three layers away. Making the
default the baked location means forgetting the variable costs nothing.

Two tests pin this: one asserts `railway.json` names the prefetch command *and* the builder
that reads it, the other asserts the prefetch script imports `CACHE_PATH` rather than
hardcoding a path.

### 3.2 Overriding the floor warns, loudly, at import

`RETRIEVAL_FLOOR` is tunable because [architecture.md §12.1](./architecture.md) asks for it.
It also *is* the not-in-corpus refusal. Those two facts sit badly together, so an override
logs at WARNING when the module loads, naming the measured value and saying that
[retrieval-calibration.md](./retrieval-calibration.md) no longer describes the deployment.

A knob whose documentation goes stale the moment it is turned should say so itself.

### 3.3 `eval/runs/` was gitignored, and should not have been

Found while reading `.gitignore` for the weights entry. `eval/runs/` was ignored as
"generated, not source" — but the regression suite's entire method is *diff this run against
the previous one* ([eval.md §2.3](../../eval.md)), which requires the previous run to be in
the repo.

The consequence was concrete:
[prompt-inversion-regression.md](./prompt-inversion-regression.md) cites two run files as its
evidence, and **one of them could never be committed**. The prompt-inversion comparison was
unreproducible from a clone. Meanwhile `eval/rag_runs/` and `eval/failure_log_runs/` were
both tracked — three directories holding the same kind of artifact, under two different
rules.

Now un-ignored. This is a Phase 2.8 defect fixed in 2.9, not a deployment change, and it is
recorded here because this is where it was found.

### 3.4 `/health` reports configuration

```json
{"status":"ok","embedding_model":"BAAI/bge-small-en-v1.5","embedding_dim":384,
 "embedding_weights_path":"…/.fastembed_cache","retrieval_k":8,"retrieval_floor":0.69}
```

The four things most often wrong after a deploy — wrong model, wrong vector width, weights
not where the build put them, floor overridden by a stale variable — become one `curl`. All
of it is non-secret: a public model id and two numbers already published in
retrieval-calibration.md. A test asserts the payload contains none of
`groq`/`api_key`/`postgres`/`password`/`secret`/`token`.

---

## 4. Runbook — the steps that need your credentials

Everything below needs the Railway or Vercel dashboard. Run them in this order.

### 4.1 Railway variables (Service → Variables)

| Variable | Value | Required? |
|---|---|---|
`GROQ_API_KEY` | your key | already set |
`DATABASE_URL` | auto-injected by the Postgres plugin | already set |
`ALLOWED_ORIGINS` | your Vercel URL | already set |
`EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | **optional** — this is the default |
`FASTEMBED_CACHE_PATH` | leave unset | **optional** — the default is the baked path (§3.1) |
`RETRIEVAL_K` | leave unset | **optional** — defaults to the measured 8 |
`RETRIEVAL_FLOOR` | leave unset | **optional** — defaults to the measured 0.69 |

The four new ones are all optional by design. Set `EMBEDDING_MODEL` explicitly if you want
the deploy to be self-documenting; leave the other three alone unless you have a reason, and
if you change the floor, re-run `eval/run_retrieval_eval.py` and update
retrieval-calibration.md.

### 4.2 Deploy and read the build log

Push, then **read Railway's build log for the prefetch output**:

```text
prefetching BAAI/bge-small-en-v1.5 into …/.fastembed_cache
ok: 384 dimensions, 17 unique file(s), 67 MB on disk
```

If that is absent, Railpack did not pick up `backend/railway.json`. The deploy will still
succeed — which is the problem — and the weights will download at boot instead. Two
fallbacks, in order: set Settings → Config-as-code to `/backend/railway.json`, or set
Settings → Build → Build Command directly to
`python scripts/prefetch_embedding_model.py`.

### 4.3 Migration and seed

The `Procfile` runs `alembic upgrade head` on every deploy, so the migration applies itself.
Expect head `c3d9a51e7b42`. Seeding is manual and separate, as
[architecture.md §12.2](./architecture.md) requires — it is idempotent, so running it twice
is safe:

```bash
# Railway → Service → ⋮ → Shell, or `railway run` from a linked local checkout
python -m alembic current          # expect c3d9a51e7b42 (head)
python -m corpus.seed              # idempotent; skips rows whose content_sha256 matches
```

**Expect 7 documents and 103 chunks** — that is what `corpus/corpus_snapshot.jsonl.gz`
holds, and the seed refuses a snapshot whose embedding model id does not match the running
configuration.

### 4.4 Verify

```bash
curl https://<railway-url>/health     # model, dims, weights path, k, floor
curl https://<railway-url>/corpus     # expect 7 documents, 103 chunks
```

Then, for cold-start timing, let the service idle until Railway sleeps it (or redeploy) and
time the first request:

```bash
time curl -s -o /dev/null https://<railway-url>/health
```

Compare against the local 1.37 s. A number in the tens of seconds means the weights are not
baked — go back to §4.2.

Then in a browser, on the Vercel URL:

- [ ] **Cited answer** — "How long can cooked chicken sit out at room temperature?" One
      block, FSANZ, claims with passages in the sources panel.
- [ ] **Cross-document answer** — "How much protein does an adult need daily?" **Two**
      visibly separate blocks, WHO and USDA & HHS, each with its own citations. They
      disagree about the framing; neither should be presented as the winner.
- [ ] **Coverage refusal** — "What is the best creatine dose for muscle gain?" The
      `not_in_corpus` notice, listing all 7 documents. Costs no Groq call.
- [ ] **Policy refusal** — "How many calories should I eat to lose 10 pounds?" The amber
      `RefusalNotice`, visibly different from the one above. Costs no Groq call.
- [ ] **Page reload** — refresh. Both blocks of the protein answer return with their
      citations.
- [ ] **Devtools → Network / Sources** — no `GROQ_API_KEY`, no `gsk_`, no database URL in
      any request or in the JS bundle.
- [ ] **Railway metrics** — peak memory during the above, against your tier's ceiling.
      Expect ~350 MB idle (§2.2).

---

## 5. Exit criteria

| Criterion | |
|---|---|
| No ingestion in the `Procfile`; no network call to any publisher at boot | **[x]** Verified by test, not by reading: the Procfile is checked for `corpus.ingest`/`seed`/`fetch`, and a subprocess import of the app is checked for every ingest-only package. Plus the runtime-only venv in §2.3 |
| No secret in the frontend bundle; `GROQ_API_KEY` still the only provider secret | **[x]** for the built bundle (§2.4) — `NEXT_PUBLIC_API_BASE_URL` is the only inlined variable. **Re-confirm on the deployed bundle** in §4.4, since Vercel inlines a different value |
| Migration applied to Railway Postgres; `seed.py` run; chunk count matches the snapshot | **[ ] open** — needs the Railway shell. §4.3. Target: head `c3d9a51e7b42`, 7 documents, 103 chunks |
| Memory headroom confirmed on the actual tier | **[ ] open** — measured locally at **351 MB** for the whole process (§2.2), which is the number to size against, not the plan's 279 MB. Needs Railway's metrics |
| Cold-start time measured against the deployed URL | **[ ] open** — **1.37 s** locally with baked weights. §4.4 |
| Full flow verified in production | **[ ] open** — §4.4's checklist |

Four of six need production access. The code is done and the properties that *can* be
tested are tested; what remains is genuinely operational.

---

## 6. Risks carried into the deploy

- **The build config has still never produced a successful build.** It was wrong once
  already (§3.0), and the corrected `railway.json` is documented but unexercised — the
  first deploy attempt died in Railpack's own bootstrap, before reaching it. **A skipped
  build step does not fail the deploy**, so §4.2's build-log check is the single most
  important line in this runbook.
- **Railway's builder changed under this project.** [../../deployment.md §5](../../deployment.md)
  flagged Nixpacks' Python detection as unvalidated; that risk is now moot and replaced by
  an unvalidated Railpack build. Worth re-reading the build log on the first *successful*
  deploy rather than assuming.
- **~350 MB resident is not 279 MB.** If the tier is 512 MB, there is less headroom than the
  plan assumed. The escape hatch, if it proves tight, is an API-based embedding provider —
  at the cost of the single-provider property that motivated the local model in the first
  place ([architecture.md §2](./architecture.md)).
- **Railway cold starts now include a 0.3 s model load.**
  [../../edge-case.md](../../edge-case.md) already flagged cold starts; this adds to them,
  and the lifespan means the platform waits for it rather than a user.
- **`/chat` is still unrate-limited**, and retrieval adds an embedding per request on top of
  the generation call. Named in the plan's "what this does not schedule" and still
  unaddressed — worth saying again now that the thing is about to be public.
- **Nothing is deployed yet.** Every number above is local.
