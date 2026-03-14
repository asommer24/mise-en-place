<<<<<<< HEAD
# 🍽️ Mise en Place — Automated Weekly Meal Planner

Picks 5 make-ahead lunches + 3 dinners every Saturday, texts both of you a link, 
lets you swap recipes on the web, then builds a categorized shopping list in 
Apple Reminders (or Todoist) Sunday morning.

---

## Weekly Flow

```
Saturday 9 AM  →  Claude picks recipes from library + queue
                →  SMS sent to both phones with web link
                →  Either user can swap any recipe on web app

Saturday–Sunday →  Users override picks, confirm plan

Sunday 1 AM    →  Claude consolidates all ingredients
                →  Categorized shopping list → Apple Reminders / Todoist
                →  SMS confirmation sent

Anytime        →  Text an Instagram link → recipe added to queue
                →  Text a recipe name    → stub added to queue
                →  Web app: manage queue, view library, check off shopping
```

---

## Setup (~45 minutes)

### 1. Supabase
1. Create a free project at supabase.com
2. SQL Editor → run `scripts/schema.sql`
3. Settings → API → copy Project URL + service_role key

### 2. Twilio
1. Sign up at twilio.com (free trial gives ~$15 credit = hundreds of SMS)
2. Get a phone number
3. Note your Account SID + Auth Token
4. **For inbound SMS:** Messaging → Phone Numbers → your number → 
   Webhook URL = `https://your-api.railway.app/webhook/sms`

### 3. Apple Reminders (CalDAV)
1. Go to **appleid.apple.com** → Security → App-Specific Passwords → Generate
2. Name it "Mise en Place", copy the password
3. In Reminders.app on iPhone/Mac: create a list called "Grocery List"
4. Add `CALDAV_PASSWORD=xxxx-xxxx-xxxx-xxxx` to your secrets

### 4. Deploy the SMS webhook (Railway — free tier)
The inbound SMS handler must run as a persistent web service (GitHub Actions 
can't receive webhooks).

```bash
# Install Railway CLI
npm install -g @railway/cli
railway login
railway new mise-sms
railway up
# Set env vars in Railway dashboard
```

Then set your Twilio webhook URL to `https://your-service.railway.app/webhook/sms`

### 5. GitHub Secrets
Add all variables from `.env.example` to:  
**GitHub repo → Settings → Secrets and variables → Actions**

### 6. Frontend (Vercel)
```bash
# Create a Next.js or Vite project, drop in MiseEnPlace.jsx
# Connect to Supabase with @supabase/supabase-js
vercel deploy
```

---

## Adding Recipes

**Via text (recommended):**
- Send an Instagram link → auto-parsed and queued
- Send any recipe URL → auto-parsed and queued  
- Send a recipe name → stub created (fill details on web)
- Text `list` → see current queue
- Text `status` → see this week's plan

**Via web app:**
- Queue tab → paste link or name directly

---

## Cost Estimate

| Service | Monthly | Annual |
|---|---|---|
| Claude Sonnet API (2 runs/week) | ~$0.50 | ~$6 |
| Twilio SMS (~10 messages/week) | ~$0.40 | ~$5 |
| Supabase (free tier) | $0 | $0 |
| Railway (free tier for SMS webhook) | $0 | $0 |
| Vercel (free tier) | $0 | $0 |
| **Total** | **~$0.90** | **~$11** |

Optional: RapidAPI Instagram scraper ~$10/month if you submit many IG links.

---

## File Structure

```
mise-en-place/
├── agents/
│   ├── saturday_suggest.py   # Saturday cron: picks recipes, sends SMS
│   ├── sunday_shopping.py    # Sunday cron: shopping list to Reminders/Todoist
│   └── sms_inbound.py        # FastAPI webhook: handles inbound texts
├── scripts/
│   └── schema.sql            # Supabase schema + seed recipes
├── frontend/
│   └── MiseEnPlace.jsx       # React web app
├── .github/workflows/
│   └── meal-planner.yml      # GitHub Actions: Saturday + Sunday crons
├── requirements.txt
└── .env.example
```
=======
# mise-en-place
Agentic application for recipe planning. Perfect for couples and people who love to cook! 
>>>>>>> d3fad61b3e383447417ff4a2cb59aa3a1c3e6348
