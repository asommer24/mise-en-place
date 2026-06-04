# 🍽️ Mise en Place — Automated Weekly Meal Planner

Picks 5 make-ahead lunches + 3 vegetarian dinners every Saturday and shows them
on a web app where either of you can swap recipes, then builds a categorized
shopping list in Todoist Sunday morning.

Add recipes by sending an Instagram link to the ingest endpoint — paste it in
the web app, or share a post straight from Instagram with a one-tap iOS
Shortcut. Each link is fetched and parsed into a structured recipe by Claude,
then queued and prioritized for the next week's plan.

---

## Weekly Flow

```
Anytime         →  Paste/share an Instagram link → recipe parsed → added to queue
                →  Or type a recipe name        → stub added to queue

Saturday 9 AM   →  Claude picks 5 lunches + 3 vegetarian dinners from library + queue
                →  Plan appears on the web app
                →  Either user can swap any recipe on the web app

Sunday 1 AM     →  Claude consolidates all ingredients
                →  Categorized shopping list → Todoist
                →  Check items off in the web app or in Todoist
```

> **Why not pull Instagram "Saved" posts automatically?** Instagram has no
> official API for reading a private *Saved* collection — the Graph API only
> exposes posts an account *publishes*, and Basic Display was shut down in
> Dec 2024. The only thing that can read Saved is an unofficial "log in as you"
> client, which violates Instagram's TOS and risks the account. So recipes are
> submitted per-post (one tap from the share sheet) instead.

> **Dinners are vegetarian-only.** The Saturday agent filters the dinner pool to
> recipes tagged `vegetarian` (`tags` array in Supabase). Tag at least 3 dinner
> recipes `vegetarian` or the Saturday job will exit with "Not enough recipes."

---

## Setup (~30 minutes)

### 1. Supabase
1. Create a free project at supabase.com
2. SQL Editor → run `scripts/schema.sql` (creates tables, RLS policies, helper
   views, and seeds a handful of starter recipes)
3. Settings → API → copy Project URL + service_role key

### 2. Todoist
1. Todoist → **Settings → Integrations → Developer** → copy your API token
2. (Optional) create a project named "Groceries" — the agent creates it
   automatically if it doesn't exist
3. Add to your secrets:
   ```
   TODOIST_API_KEY=...
   TODOIST_PROJECT_NAME=Groceries
   ```

The Sunday agent clears the project's open tasks, then writes one task per
ingredient grouped under `── CATEGORY ──` header tasks (produce, protein,
dairy, …). The same list is also saved to Supabase for the web app.

### 3. Deploy the ingest endpoint (Railway — free tier)
The ingest handler runs as a persistent web service (GitHub Actions can't
receive HTTP requests).

```bash
npm install -g @railway/cli
railway login
railway new mise-ingest
railway up
# Set env vars in the Railway dashboard:
#   ANTHROPIC_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY, INGEST_TOKEN
```

Note the service URL (e.g. `https://mise-ingest.railway.app`). `GET /health`
returns `{"status": "ok"}` for uptime checks.

### 4. GitHub Secrets (for the Saturday/Sunday crons)
Add to **GitHub repo → Settings → Secrets and variables → Actions**:
`ANTHROPIC_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, plus
`TODOIST_API_KEY` and `TODOIST_PROJECT_NAME`. See `.env.example`.

The workflow can also be run on demand from the **Actions** tab via
*Run workflow* — pick `saturday_suggest` or `sunday_shopping`, and optionally
tick **dry run** to preview the Saturday selection without writing anything.

### 5. Frontend (Vercel)
```bash
cd frontend
npm install
# Set env vars:
#   VITE_INGEST_URL        your Railway URL
#   VITE_INGEST_TOKEN      same value as INGEST_TOKEN
#   VITE_SUPABASE_URL      Supabase Project URL
#   VITE_SUPABASE_ANON_KEY Supabase ANON / publishable key (NOT service_role)
vercel deploy
```

The web app reads the plan, recipe library, queue, and shopping list straight
from Supabase with the anon key, and writes swaps / confirmations / checked-off
groceries back to the current week's plan. RLS (`scripts/schema.sql`) keeps the
anon key scoped to those operations — re-run `schema.sql` if you set this up
before the policy was added.

---

## Adding Recipes

**From the web app:** open the **Queue** tab → paste an Instagram link or a
recipe name → **Add**.

**From Instagram (one tap) — iOS Shortcut:**
1. Open the **Shortcuts** app → create a new shortcut.
2. Add **Get Contents of URL**:
   - URL: `https://<your-ingest-service>/ingest`
   - Method: `POST`
   - Headers: `Content-Type: application/json`, `X-Ingest-Token: <your INGEST_TOKEN>`
   - Request Body: `JSON` → key `url` = the Shortcut Input
3. In the shortcut settings, enable **Show in Share Sheet** and accept URLs.
4. Now in Instagram: tap **Share → Add to Mise** and the recipe is queued.

A submitted **name** that already matches a library recipe is re-queued; an
unknown name is added as a stub (default `meal_type: dinner`) for you to fill in
on the web app.

### How Instagram link parsing works
The ingest endpoint reads the post's **caption** (where the recipe text lives),
then Claude turns it into a structured recipe. For Instagram it fetches the
public **`/embed/captioned/`** page — this works for posts (`/p/`), reels
(`/reel/`), and IGTV (`/tv/`) with **no API key**, and any `?utm_source=…` query
string on the link is stripped automatically. Plain `/embed/` is *not* used —
it returns only page chrome (likes/handle) without the caption.

If a caption is sparse or the embed is unavailable, set the optional
`INSTAGRAM_SCRAPER_API_KEY` (RapidAPI) for richer parsing, or just submit the
recipe **name** instead (`{"name": "..."}`), which skips fetching entirely.

---

## Local Development

```bash
# Ingest API
pip install -r requirements.txt
cp .env.example .env        # fill in the REQUIRED vars
uvicorn agents.ingest:app --reload          # → http://localhost:8000

# Frontend
cd frontend && npm install && npm run dev   # → http://localhost:5173
```

`agents/ingest.py` loads the root `.env` automatically. CORS defaults to `*`;
set `FRONTEND_ORIGIN` (comma-separated origins) to lock it down. The two cron
agents can be run directly for testing:

```bash
python -m agents.saturday_suggest
python -m agents.sunday_shopping
```

---

## Data Model (Supabase)

| Table | Purpose |
|---|---|
| `recipes` | Master recipe library. `meal_type` (`lunch`/`dinner`), `ingredients` (JSONB), `tags[]`, `times_suggested`, and the `in_queue` / `queue_priority` queue flags. |
| `weekly_plans` | One row per week (`week_start` = Monday). Holds the 5 lunch + 3 dinner recipe IDs, `status` (`suggested`→`confirmed`→`shopping_done`), and the consolidated `shopping_list`. |
| `recipe_submissions` | Audit trail of inbound ingest requests and their parse status. |
| `meal_pipeline_runs` | Run log for the Saturday/Sunday agents. |

`schema.sql` also defines the `current_week_plan` and `recipe_queue` views and a
trigger that maintains `updated_at`.

---

## Cost Estimate

| Service | Monthly | Annual |
|---|---|---|
| Claude Sonnet API (2 crons/week + ingests) | ~$0.50 | ~$6 |
| Supabase (free tier) | $0 | $0 |
| Railway (free tier for ingest endpoint) | $0 | $0 |
| Vercel (free tier) | $0 | $0 |
| Todoist (free tier) | $0 | $0 |
| **Total** | **~$0.50** | **~$6** |

Optional: RapidAPI Instagram scraper ~$10/month if you want richer IG parsing.

---

## File Structure

```
mise-en-place/
├── agents/
│   ├── saturday_suggest.py   # Saturday cron: picks the week's recipes
│   ├── sunday_shopping.py    # Sunday cron: shopping list to Todoist
│   └── ingest.py             # FastAPI: POST /ingest adds recipes to the queue
├── scripts/
│   └── schema.sql            # Supabase schema, RLS, views + seed recipes
├── frontend/
│   ├── src/App.jsx           # React web app
│   ├── src/supabase.js       # Supabase client
│   └── vite.config.js
├── .github/workflows/
│   └── meal-planner.yml      # GitHub Actions: Saturday + Sunday crons
├── Procfile                  # Railway: runs agents.ingest:app
├── requirements.txt
└── .env.example
```
