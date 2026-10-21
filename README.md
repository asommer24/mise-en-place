# 🍽️ Mise en Place — Automated Weekly Meal Planner

Picks make-ahead lunches + vegetarian dinners every Saturday, enough servings
for the week, and shows them on a web app where either of you can swap recipes, then builds a categorized
shopping list in Todoist Sunday morning.

Add recipes from the web app's Queue tab by pasting a link or a
recipe name. Each link is fetched and parsed into a structured recipe by Claude,
then queued and prioritized for the next week's plan. Anything already in the
library or queue isn't added twice.

**More documentation lives in [`docs/`](docs/):**
[Queue](docs/QUEUE.md) (adding, duplicates, removing),
[Settings tab](docs/SETTINGS.md) (what the defaults control) and
[Tagging](docs/TAGGING.md) (how meal type, tags and servings get decided).

---

## Weekly Flow

```
Anytime         →  Paste an Instagram link in the Queue tab → recipe parsed → added to queue
                →  Or type a recipe name        → stub added to queue

Saturday 9 AM   →  Claude picks lunches + vegetarian dinners from library + queue
                   to cover the week's servings (see below)
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
> submitted per-post (pasted into the Queue tab) instead.

> **Vegetarian toggles.** Settings has one switch for lunches and one for
> dinners (dinners on, lunches off by default). A switch limits that pool to
> recipes tagged `vegetarian` (`tags` array in Supabase). Tag enough recipes
> `vegetarian` to cover the servings or the Saturday job will exit with
> "Not enough dinners tagged vegetarian."

> **Servings, not recipe counts.** The **Settings** tab holds three defaults:
> lunches per week, dinners per week and people. The week needs
> `(lunches + dinners) × people` servings (default 5 / 3 / 2 = 16), with lunches
> and dinners covered separately. Each recipe's `servings` counts toward its
> target (4 if unset), whole recipes only, so the extra servings are leftovers.
> **Generate** and **regenerate** open a dialog where those numbers can be
> changed for that week only; they are stored on the plan
> (`weekly_plans.settings`), never on the defaults. The Saturday cron always
> uses the saved defaults. Details in [docs/SETTINGS.md](docs/SETTINGS.md).

---

## Setup (~30 minutes)

### 1. Supabase
1. Create a free project at supabase.com
2. SQL Editor → run `scripts/schema.sql` (creates tables, RLS policies, helper
   views, the `increment_times_suggested` function the Saturday agent calls,
   and seeds enough recipes to produce a plan)
3. Settings → API → copy Project URL + service_role key

**Already set up?** Re-run sections **6b** and **7b** of `schema.sql` — 6b adds
the `increment_times_suggested` function (previously called but never defined,
so `times_suggested` never incremented) and 7b tops the library up to 6 lunches
and 4 vegetarian dinners. Also run **6d** (the `meal_settings` defaults table and
`weekly_plans.settings`), **6e** (the vegetarian switches), **6f** (lets the web
app edit a recipe's meal type, tags and servings and remove it from the queue)
and **6g** (duplicate detection). Without 6d/6e the app falls back to the
defaults and the Settings tab can't save; without 6f the recipe **edit** and
queue **remove** buttons report an error; without 6g nothing stops a duplicate
being added. All seven are safely re-runnable. Do *not* re-run section 7
— it has no unique constraint to conflict on, so it duplicates its recipes.

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

Recipes are added from the web app: open the **Queue** tab → paste an Instagram
link or a recipe name → **Add**. That is the only way in. A submission that
matches a recipe already in the library or queue is not added again, and a
queued recipe can be taken off the queue with **remove**. How duplicates are
detected and what each case does is in [docs/QUEUE.md](docs/QUEUE.md).

An unknown **name** is added as a stub (default `meal_type: dinner`) for you to
fill in on the web app. Use **edit** on a recipe card (Library or Queue) to change
its meal type, servings and tags; vegetarian is a switch over the `vegetarian`
tag. How each of those gets decided, and when, is in
[docs/TAGGING.md](docs/TAGGING.md).

### How Instagram link parsing works
The ingest endpoint reads the post's **caption** (where the recipe text lives),
then Claude turns it into a structured recipe, including whether it is
vegetarian (no meat, poultry, fish or shellfish), which becomes the `vegetarian`
tag the planner filters on. For Instagram it fetches the
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

### Tests

```bash
python test_agents.py     # no network, no DB, no framework — just asserts
```

Covers the parts that broke in production: the Saturday/Sunday week alignment,
JSON parsing of model replies (thinking blocks, code fences, truncation), and
validation of the recipe ids Claude returns.

### The Claude call

All three agents share `agents/claude.py`. The model is `claude-sonnet-5` by
default — override with `ANTHROPIC_MODEL` to pin or upgrade without editing
code. Two things that module handles, which any new call site should reuse
rather than reimplement:

- **Read the text blocks, not `content[0]`.** Current models emit a `thinking`
  block first, so `response.content[0].text` raises `AttributeError`.
- **Thinking tokens count against `max_tokens`.** A budget that used to be
  ample can now truncate mid-JSON; the helper says so plainly instead of
  surfacing a confusing `JSONDecodeError`.

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
│   ├── claude.py             # Shared: model id + JSON-reply parsing
│   ├── saturday_suggest.py   # Saturday cron: picks the week's recipes
│   ├── sunday_shopping.py    # Sunday cron: shopping list to Todoist
│   └── ingest.py             # FastAPI: POST /ingest adds recipes to the queue
├── docs/
│   ├── QUEUE.md              # Adding, duplicate detection, removing
│   ├── SETTINGS.md           # The Settings tab: what it controls and governs
│   └── TAGGING.md            # How meal type, tags and servings get decided
├── test_agents.py            # Offline asserts: week math, JSON parsing, validation
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
