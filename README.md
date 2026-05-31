# 🍽️ Mise en Place — Automated Weekly Meal Planner

Picks 5 make-ahead lunches + 3 dinners every Saturday and shows them on a web
app where either of you can swap recipes, then builds a categorized shopping
list in Apple Reminders (or Todoist) Sunday morning.

Add recipes by sending an Instagram link to the ingest endpoint — paste it in
the web app, or share a post straight from Instagram with a one-tap iOS
Shortcut. Each link is fetched and parsed into a structured recipe by Claude,
then queued and prioritized for the next week's plan.

---

## Weekly Flow

```
Anytime         →  Paste/share an Instagram link → recipe parsed → added to queue
                →  Or type a recipe name        → stub added to queue

Saturday 9 AM   →  Claude picks 5 lunches + 3 dinners from library + queue
                →  Plan appears on the web app
                →  Either user can swap any recipe on the web app

Sunday 1 AM     →  Claude consolidates all ingredients
                →  Categorized shopping list → Apple Reminders / Todoist
                →  Check items off in the web app or in Reminders
```

> **Why not pull Instagram "Saved" posts automatically?** Instagram has no
> official API for reading a private *Saved* collection — the Graph API only
> exposes posts an account *publishes*, and Basic Display was shut down in
> Dec 2024. The only thing that can read Saved is an unofficial "log in as you"
> client, which violates Instagram's TOS and risks the account. So recipes are
> submitted per-post (one tap from the share sheet) instead.

---

## Setup (~30 minutes)

### 1. Supabase
1. Create a free project at supabase.com
2. SQL Editor → run `scripts/schema.sql`
3. Settings → API → copy Project URL + service_role key

### 2. Apple Reminders (CalDAV)
1. Go to **appleid.apple.com** → Security → App-Specific Passwords → Generate
2. Name it "Mise en Place", copy the password
3. In Reminders.app on iPhone/Mac: create a list called "Grocery List"
4. Add `CALDAV_PASSWORD=xxxx-xxxx-xxxx-xxxx` to your secrets

(Or use Todoist instead — see `.env.example`.)

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

Note the service URL (e.g. `https://mise-ingest.railway.app`).

### 4. GitHub Secrets (for the Saturday/Sunday crons)
Add to **GitHub repo → Settings → Secrets and variables → Actions**:
`ANTHROPIC_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, plus your shopping
backend secrets (CalDAV or Todoist). See `.env.example`.

### 5. Frontend (Vercel)
```bash
cd frontend
npm install
# Set env vars: VITE_INGEST_URL (your Railway URL) and VITE_INGEST_TOKEN
# (same value as INGEST_TOKEN)
vercel deploy
```

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

## Cost Estimate

| Service | Monthly | Annual |
|---|---|---|
| Claude Sonnet API (2 crons/week + ingests) | ~$0.50 | ~$6 |
| Supabase (free tier) | $0 | $0 |
| Railway (free tier for ingest endpoint) | $0 | $0 |
| Vercel (free tier) | $0 | $0 |
| **Total** | **~$0.50** | **~$6** |

Optional: RapidAPI Instagram scraper ~$10/month if you want richer IG parsing.

---

## File Structure

```
mise-en-place/
├── agents/
│   ├── saturday_suggest.py   # Saturday cron: picks the week's recipes
│   ├── sunday_shopping.py    # Sunday cron: shopping list to Reminders/Todoist
│   └── ingest.py             # FastAPI: POST /ingest adds recipes to the queue
├── scripts/
│   └── schema.sql            # Supabase schema + seed recipes
├── frontend/
│   └── src/App.jsx           # React web app
├── .github/workflows/
│   └── meal-planner.yml      # GitHub Actions: Saturday + Sunday crons
├── Procfile                  # Railway: runs agents.ingest:app
├── requirements.txt
└── .env.example
```
