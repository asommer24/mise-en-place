"""
ingest.py
---------
FastAPI ingest endpoint for adding recipes to the queue.

Recipes reach the agent by being submitted to POST /ingest — either pasted
into the web app's Queue box, or shared from Instagram via a one-tap iOS
Share-sheet Shortcut. There is no official Instagram API for reading a private
"Saved" collection, so recipes are submitted per-post rather than auto-pulled.

Handles two payload shapes:
  1. {"url": "https://instagram.com/p/..."}  → fetch, extract recipe, queue it
  2. {"name": "Lemon Orzo Salad"}            → match existing library or queue a stub

Auth: every request must send header  X-Ingest-Token: <INGEST_TOKEN>.

Deploy as a persistent web service (Railway). See Procfile.

Env vars required:
    ANTHROPIC_API_KEY
    SUPABASE_URL
    SUPABASE_SERVICE_KEY
    INGEST_TOKEN                 (shared secret guarding the endpoint)
    INSTAGRAM_SCRAPER_API_KEY    (optional — RapidAPI Instagram scraper)
"""

import json
import logging
import os
import re
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()  # load root .env for local dev (no-op when env vars are already set)

import anthropic
import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mise.ingest")

app = FastAPI()

# The web app calls /ingest from a different origin (Vercel frontend → Railway
# API, or localhost:5173 → localhost:8000 in dev). Allow it. Set FRONTEND_ORIGIN
# (comma-separated) to lock this down; defaults to "*". Auth is via the
# X-Ingest-Token header, not cookies, so credentials stay off.
_origins = os.getenv("FRONTEND_ORIGIN", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

URL_PATTERN = re.compile(r'https?://\S+')
INSTAGRAM_PATTERN = re.compile(r'https?://(www\.)?instagram\.com/\S+')


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def require_token(x_ingest_token: str | None) -> None:
    """Raise 401 unless the request carries the shared secret."""
    expected = os.environ.get("INGEST_TOKEN", "")
    if not expected or x_ingest_token != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing ingest token")


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
    Attempt to get an Instagram post/reel's caption (where the recipe lives).
    Uses the RapidAPI Instagram scraper if a key is set; otherwise falls back
    to the public "captioned" embed page, which (unlike plain /embed/) includes
    the caption text for posts (/p/), reels (/reel/), and IGTV (/tv/).
    """
    api_key = os.getenv("INSTAGRAM_SCRAPER_API_KEY")

    if api_key:
        # RapidAPI Instagram scraper — handle /p/, /reel/, /reels/, /tv/ links
        shortcode = re.search(r'/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)', url)
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
                if caption:
                    return caption
            except Exception as exc:
                logger.warning("Instagram API failed: %s", exc)

    # Fallback: the public "captioned" embed page includes the caption text.
    # Strip any query string (?utm_source=…) before appending the embed path.
    clean_url = url.split('?')[0].rstrip('/')
    return fetch_url_content(clean_url + "/embed/captioned/")


# ---------------------------------------------------------------------------
# Save recipe to Supabase queue
# ---------------------------------------------------------------------------

def save_recipe_to_queue(sb, recipe_data: dict, source_url: str,
                         source_type: str) -> str:
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
# Ingest endpoint
# ---------------------------------------------------------------------------

class IngestRequest(BaseModel):
    url: str | None = None
    name: str | None = None
    submitted_by: str = "web"


@app.post("/ingest")
def ingest(req: IngestRequest, x_ingest_token: str | None = Header(default=None)):
    require_token(x_ingest_token)

    sb = get_sb()

    # ── URL / Instagram link ──
    if req.url:
        url_match = URL_PATTERN.search(req.url)
        if not url_match:
            raise HTTPException(status_code=400, detail="Not a valid URL")
        url = url_match.group(0)
        is_instagram = bool(INSTAGRAM_PATTERN.match(url))
        source_type = "instagram" if is_instagram else "url"

        # Log submission
        sub_res = sb.table("recipe_submissions").insert({
            "submitted_by": req.submitted_by,
            "raw_message": req.url,
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
            raise HTTPException(
                status_code=422,
                detail="Couldn't read that link. Try the recipe name instead.",
            )

        recipe = extract_recipe_from_text(content, url)
        if not recipe:
            sb.table("recipe_submissions").update({
                "status": "failed",
                "processed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", sub_id).execute()
            raise HTTPException(
                status_code=422,
                detail="Couldn't parse a recipe from that link. Try the recipe name instead.",
            )

        recipe_id = save_recipe_to_queue(sb, recipe, url, source_type)

        sb.table("recipe_submissions").update({
            "status": "added",
            "recipe_id": recipe_id,
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", sub_id).execute()

        return {"ok": True, "recipe_id": recipe_id, "name": recipe["name"],
                "message": f'Added "{recipe["name"]}" to the queue.'}

    # ── Plain text recipe name ──
    if req.name:
        name = req.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Empty recipe name")

        # Search if it exists already
        existing = (
            sb.table("recipes")
            .select("id, name")
            .ilike("name", f"%{name[:50]}%")
            .limit(1)
            .execute()
        ).data

        if existing:
            match = existing[0]
            sb.table("recipes").update({
                "in_queue": True,
                "queue_priority": 10,
            }).eq("id", match["id"]).execute()
            return {"ok": True, "recipe_id": match["id"], "name": match["name"],
                    "message": f'Found "{match["name"]}" in the library and queued it.'}

        # Create a stub recipe
        stub = {
            "name": name[:100],
            "meal_type": "dinner",   # default; user can update on web app
            "description": "Added via web — fill in details on the web app.",
            "ingredients": [],
            "source_type": req.submitted_by,
            "in_queue": True,
            "queue_priority": 8,
        }
        res = sb.table("recipes").insert(stub).execute()
        return {"ok": True, "recipe_id": res.data[0]["id"], "name": stub["name"],
                "message": f'Added "{name[:50]}" to the queue as a stub. '
                           f'Fill in ingredients on the web app.'}

    raise HTTPException(status_code=400, detail="Provide either 'url' or 'name'")


@app.get("/health")
def health():
    return {"status": "ok"}
