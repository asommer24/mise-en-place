"""
sms_inbound.py
--------------
FastAPI webhook called by Twilio when either user sends an SMS to your number.

Handles two SMS patterns:
  1. Instagram link  → fetch metadata, extract recipe, add to queue
  2. Recipe URL      → fetch page, extract recipe, add to queue  
  3. Plain text name → search existing library or create stub recipe in queue
  4. "status"        → reply with this week's plan summary
  5. "list"          → reply with top 5 queued recipes

Deploy this as a Vercel serverless function or Railway service.

Env vars required:
    ANTHROPIC_API_KEY
    SUPABASE_URL
    SUPABASE_SERVICE_KEY
    TWILIO_ACCOUNT_SID
    TWILIO_AUTH_TOKEN
    TWILIO_FROM_NUMBER
    PHONE_NUMBER_1
    PHONE_NUMBER_2
    INSTAGRAM_SCRAPER_API_KEY   (optional — RapidAPI Instagram scraper)
"""

import json
import logging
import os
import re
from datetime import datetime, timezone

import anthropic
import httpx
from fastapi import FastAPI, Form, Response
from supabase import create_client

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mise.sms")

app = FastAPI()

ALLOWED_PHONES = {
    os.getenv("PHONE_NUMBER_1", ""),
    os.getenv("PHONE_NUMBER_2", ""),
}

URL_PATTERN = re.compile(r'https?://\S+')
INSTAGRAM_PATTERN = re.compile(r'https?://(www\.)?instagram\.com/\S+')


# ---------------------------------------------------------------------------
# Supabase helper
# ---------------------------------------------------------------------------

def get_sb():
    return create_client(os.environ["SUPABASE_URL"],
                         os.environ["SUPABASE_SERVICE_KEY"])


# ---------------------------------------------------------------------------
# Recipe extraction via Claude
# ---------------------------------------------------------------------------

RECIPE_EXTRACT_PROMPT = """
You are a recipe parser. Given text content from a web page, blog post,
or Instagram caption, extract a structured recipe.

Return ONLY valid JSON:
{
  "name": "Recipe Name",
  "description": "One sentence about the dish",
  "meal_type": "lunch" or "dinner",
  "ingredients": [
    {"name": "ingredient", "amount": "2", "unit": "cups", "category": "produce"}
  ],
  "instructions": "Full instructions as a single string",
  "prep_time_mins": 15,
  "cook_time_mins": 30,
  "servings": 4,
  "tags": ["make-ahead", "healthy"]
}

meal_type rules:
  - "lunch" if the dish is a bowl, salad, soup, wrap, grain dish, or anything 
     described as meal-prep friendly or make-ahead
  - "dinner" for everything else

Valid ingredient categories: produce, protein, dairy, grain, bakery, pantry, condiment, frozen, other

If you cannot extract a real recipe, return: {"error": "Could not parse recipe"}
No markdown, no preamble.
"""


def extract_recipe_from_text(content: str, source_url: str = "") -> dict | None:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    prompt = f"Source URL: {source_url}\n\nContent:\n{content[:6000]}"

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            system=RECIPE_EXTRACT_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        result = json.loads(response.content[0].text.strip())
        if "error" in result:
            return None
        return result
    except Exception as exc:
        logger.error("Recipe extraction failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# URL content fetching
# ---------------------------------------------------------------------------

def fetch_url_content(url: str) -> str:
    """Fetch a webpage and return its text content."""
    try:
        resp = httpx.get(url, timeout=15, follow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()

        # Very light HTML strip
        import re
        text = re.sub(r'<script[^>]*>.*?</script>', '', resp.text, flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:8000]
    except Exception as exc:
        logger.warning("URL fetch failed for %s: %s", url, exc)
        return ""


def fetch_instagram_content(url: str) -> str:
    """
    Attempt to get Instagram post content.
    Uses RapidAPI Instagram scraper if key is set; otherwise falls back
    to fetching the public embed URL.
    """
    api_key = os.getenv("INSTAGRAM_SCRAPER_API_KEY")

    if api_key:
        # RapidAPI Instagram scraper
        shortcode = re.search(r'/p/([A-Za-z0-9_-]+)', url)
        if shortcode:
            try:
                resp = httpx.get(
                    "https://instagram-scraper-api2.p.rapidapi.com/v1/post_info",
                    headers={
                        "X-RapidAPI-Key": api_key,
                        "X-RapidAPI-Host": "instagram-scraper-api2.p.rapidapi.com"
                    },
                    params={"code_or_id_or_url": shortcode.group(1)},
                    timeout=15,
                )
                data = resp.json()
                caption = data.get("data", {}).get("caption", {}).get("text", "")
                return caption
            except Exception as exc:
                logger.warning("Instagram API failed: %s", exc)

    # Fallback: embed URL
    embed_url = url.rstrip('/') + "/embed/"
    return fetch_url_content(embed_url)


# ---------------------------------------------------------------------------
# Save recipe to Supabase queue
# ---------------------------------------------------------------------------

def save_recipe_to_queue(sb, recipe_data: dict, source_url: str,
                         source_type: str, submitted_by: str) -> str:
    """Returns the new recipe ID."""
    row = {
        "name": recipe_data["name"],
        "description": recipe_data.get("description", ""),
        "source_url": source_url,
        "source_type": source_type,
        "meal_type": recipe_data["meal_type"],
        "ingredients": recipe_data.get("ingredients", []),
        "instructions": recipe_data.get("instructions", ""),
        "prep_time_mins": recipe_data.get("prep_time_mins"),
        "cook_time_mins": recipe_data.get("cook_time_mins"),
        "servings": recipe_data.get("servings", 4),
        "tags": recipe_data.get("tags", []),
        "in_queue": True,
        "queue_priority": 10,  # user-submitted = high priority
    }
    res = sb.table("recipes").insert(row).execute()
    recipe_id = res.data[0]["id"]
    logger.info("Added recipe '%s' to queue (id=%s)", recipe_data["name"], recipe_id)
    return recipe_id


# ---------------------------------------------------------------------------
# Twilio reply helper
# ---------------------------------------------------------------------------

def twiml_reply(message: str) -> Response:
    """Return a TwiML response that sends an SMS back."""
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Response>'
        f'<Message>{message}</Message>'
        '</Response>'
    )
    return Response(content=xml, media_type="application/xml")


# ---------------------------------------------------------------------------
# Status / list query handlers
# ---------------------------------------------------------------------------

def handle_status_query(sb) -> str:
    from datetime import date, timedelta
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    plan = (
        sb.table("weekly_plans")
        .select("lunches,dinners,status,week_start")
        .eq("week_start", week_start.isoformat())
        .maybe_single()
        .execute()
    ).data

    if not plan:
        return "No plan set for this week yet. Check back Saturday!"

    all_ids = (plan["lunches"] or []) + (plan["dinners"] or [])
    recipes = (
        sb.table("recipes").select("id,name,meal_type")
        .in_("id", all_ids).execute()
    ).data or []
    by_id = {r["id"]: r for r in recipes}

    lunches = [by_id[i]["name"] for i in plan["lunches"] if i in by_id]
    dinners = [by_id[i]["name"] for i in plan["dinners"] if i in by_id]

    return (
        f"Week of {plan['week_start']} [{plan['status']}]\n"
        f"LUNCHES: {', '.join(lunches)}\n"
        f"DINNERS: {', '.join(dinners)}"
    )


def handle_list_query(sb) -> str:
    queued = (
        sb.table("recipes")
        .select("name,meal_type")
        .eq("in_queue", True)
        .order("queue_priority", desc=True)
        .limit(5)
        .execute()
    ).data or []

    if not queued:
        return "No recipes in queue. Send a link or recipe name to add one!"
    items = "\n".join(f"  · {r['name']} ({r['meal_type']})" for r in queued)
    return f"Queue ({len(queued)} recipes):\n{items}"


# ---------------------------------------------------------------------------
# Main webhook
# ---------------------------------------------------------------------------

@app.post("/webhook/sms")
async def sms_webhook(
    From: str = Form(...),
    Body: str = Form(...),
):
    # Validate sender
    if ALLOWED_PHONES and From not in ALLOWED_PHONES:
        logger.warning("SMS from unknown number %s — ignored", From)
        return Response(status_code=204)

    sb = get_sb()
    body = Body.strip()
    lower = body.lower()

    # ── Commands ──
    if lower in ("status", "this week", "week"):
        return twiml_reply(handle_status_query(sb))

    if lower in ("list", "queue"):
        return twiml_reply(handle_list_query(sb))

    if lower in ("help", "?"):
        return twiml_reply(
            "Mise en Place commands:\n"
            "  status — see this week's plan\n"
            "  list — see recipe queue\n"
            "  Send an Instagram/URL link — add recipe to queue\n"
            "  Send a recipe name — add stub to queue"
        )

    # ── URL / Instagram link ──
    url_match = URL_PATTERN.search(body)
    if url_match:
        url = url_match.group(0)
        is_instagram = bool(INSTAGRAM_PATTERN.match(url))
        source_type = "instagram" if is_instagram else "url"

        # Log submission
        sub_res = sb.table("recipe_submissions").insert({
            "submitted_by": From,
            "raw_message": body,
            "parsed_url": url,
            "status": "processing",
        }).execute()
        sub_id = sub_res.data[0]["id"]

        # Fetch content
        content = (fetch_instagram_content(url) if is_instagram
                   else fetch_url_content(url))

        if not content:
            sb.table("recipe_submissions").update({
                "status": "failed",
                "processed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", sub_id).execute()
            return twiml_reply(
                "⚠️ Couldn't read that link. Try sending the recipe name instead, "
                "and we'll add it manually."
            )

        recipe = extract_recipe_from_text(content, url)
        if not recipe:
            sb.table("recipe_submissions").update({
                "status": "failed",
                "processed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", sub_id).execute()
            return twiml_reply(
                "⚠️ Couldn't parse a recipe from that link. "
                "Try sending just the recipe name!"
            )

        recipe_id = save_recipe_to_queue(sb, recipe, url, source_type, From)

        sb.table("recipe_submissions").update({
            "status": "added",
            "recipe_id": recipe_id,
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", sub_id).execute()

        return twiml_reply(
            f"✅ Added \"{recipe['name']}\" to the queue! "
            f"It'll be prioritised for next week's plan."
        )

    # ── Plain text recipe name ──
    # Search if it exists already
    existing = (
        sb.table("recipes")
        .select("id, name")
        .ilike("name", f"%{body[:50]}%")
        .limit(1)
        .execute()
    ).data

    if existing:
        match = existing[0]
        # Move to queue if not already there
        sb.table("recipes").update({
            "in_queue": True,
            "queue_priority": 10,
        }).eq("id", match["id"]).execute()
        return twiml_reply(
            f"✅ Found \"{match['name']}\" in the library and added it to the queue!"
        )
    else:
        # Create a stub recipe
        stub = {
            "name": body[:100],
            "meal_type": "dinner",   # default; user can update on web app
            "description": "Added via SMS — fill in details on the web app.",
            "ingredients": [],
            "source_type": "sms",
            "in_queue": True,
            "queue_priority": 8,
        }
        sb.table("recipes").insert(stub).execute()
        return twiml_reply(
            f"✅ Added \"{body[:50]}\" to the queue as a stub. "
            f"Fill in ingredients at the web app!"
        )


@app.get("/health")
def health():
    return {"status": "ok"}
