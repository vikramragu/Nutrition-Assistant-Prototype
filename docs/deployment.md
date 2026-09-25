# Deployment Guide — Phase 6

Deploys the backend to Railway and the frontend to Vercel, per [implementation-plan.md](./implementation-plan.md) Phase 6 and architecture.md §12. Both are set up from their respective dashboards against the GitHub repo (`vikramragu/Nutrition-Assistant-Prototype`), since this is a monorepo and each platform needs to be told its service's root directory.

Order matters: deploy the backend first (you need its public URL before you can configure the frontend), then the frontend, then circle back to update the backend's CORS setting with the frontend's URL.

---

## 1. Backend — Railway

1. **New Project → Deploy from GitHub repo** → select `vikramragu/Nutrition-Assistant-Prototype`.
2. **Root Directory**: set to `backend` (Settings → Source). Railway's Nixpacks builder will detect `requirements.txt` and use the `Procfile` (`alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port $PORT`) as the start command — this runs migrations automatically on every deploy, so there's no separate manual migration step.
3. **Add a Postgres plugin** to the same project (`+ New` → `Database` → `PostgreSQL`). Railway auto-injects a `DATABASE_URL` reference into the backend service's variables once linked.
4. **Environment variables** (Service → Variables):
   - `GROQ_API_KEY` — your Groq key (secret).
   - `DATABASE_URL` — should already be auto-populated from the linked Postgres plugin. Note: Railway's value is a plain `postgresql://...` URL; the backend normalizes this to the `psycopg` driver itself (`db/database_url.py`), so no manual edit is needed here.
   - `ALLOWED_ORIGINS` — leave as `http://localhost:3000` for now; **update this after the Vercel deploy** (step 2) to the real Vercel URL.
5. Deploy. Once live, copy the public Railway URL (Settings → Networking → Public Networking, or the generated `*.up.railway.app` domain) — the frontend needs it in the next step.
6. Sanity check: `curl https://<railway-url>/health` → `{"status":"ok"}`.

---

## 2. Frontend — Vercel

1. **Add New Project → Import** the same GitHub repo.
2. **Root Directory**: set to `frontend` (Vercel auto-detects Next.js once this is set; no `vercel.json` needed).
3. **Environment variable**: `NEXT_PUBLIC_API_BASE_URL` = the Railway backend URL from step 1.6 (e.g. `https://your-app.up.railway.app`).
4. Deploy. Copy the resulting `*.vercel.app` URL.

---

## 3. Wire CORS back to the real frontend URL

Back in Railway (Service → Variables), set:

```
ALLOWED_ORIGINS=https://your-app.vercel.app
```

(Comma-separate multiple origins if you also want to keep `http://localhost:3000` for local dev against the deployed backend — not required, just convenient.) Railway redeploys automatically on variable changes.

---

## 4. Smoke test against the deployed URLs

Mirrors the Phase 4/5 exit criteria, run against production instead of localhost:

- [ ] `GET https://<railway-url>/health` → 200.
- [ ] Open the Vercel URL in a browser: page loads, a conversation is created.
- [ ] Ask an in-scope question → real Groq answer appears, claims disclosure shows, every `source` is `null`.
- [ ] Ask an out-of-scope question (e.g. "how many calories should I eat to lose weight?") → `RefusalNotice` renders, styled distinctly from a normal answer.
- [ ] Refresh the page → prior messages reload via `GET /conversations/{id}`.
- [ ] Browser devtools → Network tab: confirm no `GROQ_API_KEY` or any secret appears in any request the browser makes, and the JS bundle contains no Groq API key (Sources tab / view-source on the built JS).

---

## 5. Known risk to check on first deploy

Nixpacks' Python version auto-detection hasn't been verified against Railway specifically for this project (local dev runs Python 3.14, which is very new). If the build fails or resolves a different Python version than expected, check Railway's build logs first — this is the one part of the Railway config that wasn't validated locally before this guide was written.
