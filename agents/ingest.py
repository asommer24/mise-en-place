"""
ingest.py
---------
FastAPI ingest endpoint for adding recipes to the queue.

Recipes reach the agent by being submitted to POST /ingest — pasted into the
web app's Queue box. There is no official Instagram API for reading a private
"Saved" collection, so recipes are submitted per-post rather than auto-pulled.

Takes {"url": "https://instagram.com/p/..."} → fetch, extract recipe, queue it.
A link that duplicates a library recipe (same source link, or same normalised
name once parsed) is not added again: see find_duplicate().

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

import httpx
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from supabase import create_client

from agents.claude import ask_json

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
  "vegetarian": true or false,
  "tags": ["make-ahead", "healthy"]
}

meal_type rules:
  - "lunch" if the dish is a bowl, salad, soup, wrap, grain dish, or anything
     described as meal-prep friendly or make-ahead
  - "dinner" for everything else

vegetarian rules:
  - true only if the recipe has no meat, poultry, fish or shellfish (eggs and
     dairy are fine). false if it does, or if you can't tell.

Valid ingredient categories: produce, protein, dairy, grain, bakery, pantry, condiment, frozen, other

If you cannot extract a real recipe, return: {"error": "Could not parse recipe"}
No markdown, no preamble.
"""


def extract_recipe_from_text(content: str, source_url: str = "") -> tuple[dict | None, str]:
    """Return (recipe_dict, error_detail). error_detail is empty string on success."""
    prompt = f"Source URL: {source_url}\n\nContent:\n{content[:6000]}"

    try:
        result = ask_json(RECIPE_EXTRACT_PROMPT, prompt)
        if "error" in result:
            detail = f"Claude: {result['error']} (content was {len(content)} chars)"
            logger.warning("Recipe extraction returned error for %s: %s", source_url, detail)
            return None, detail
        return result, ""
    except json.JSONDecodeError as exc:
        detail = f"JSON parse error: {exc}"
        logger.error("Recipe extraction JSON decode failed for %s: %s", source_url, exc)
        return None, detail
    except Exception as exc:
        detail = f"Extraction exception: {exc}"
        logger.error("Recipe extraction failed for %s: %s", source_url, exc)
        return None, detail


# ---------------------------------------------------------------------------
# URL content fetching
# ---------------------------------------------------------------------------

def fetch_url_content(url: str) -> tuple[str, str]:
    """Fetch a webpage and return (text_content, error_detail). error_detail is empty on success."""
    try:
        resp = httpx.get(url, timeout=15, follow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()

        # Very light HTML strip
        text = re.sub(r'<script[^>]*>.*?</script>', '', resp.text, flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:8000], ""
    except Exception as exc:
        detail = f"Fetch error ({type(exc).__name__}): {exc}"
        logger.warning("URL fetch failed for %s: %s", url, exc)
        return "", detail


def fetch_instagram_content(url: str) -> tuple[str, str]:
    """
    Attempt to get an Instagram post/reel's caption (where the recipe lives).
    Returns (content, error_detail). error_detail is empty on success.
    Uses the RapidAPI Instagram scraper if a key is set; otherwise falls back
    to the public "captioned" embed page, which (unlike plain /embed/) includes
    the caption text for posts (/p/), reels (/reel/), and IGTV (/tv/).
    """
    api_key = os.getenv("INSTAGRAM_SCRAPER_API_KEY")
    errors = []

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
                    return caption, ""
                errors.append(f"Instagram API returned no caption (keys: {list(data.keys())})")
            except Exception as exc:
                logger.warning("Instagram API failed: %s", exc)
                errors.append(f"Instagram API error ({type(exc).__name__}): {exc}")

    # Fallback: the public "captioned" embed page includes the caption text.
    # Strip any query string (?utm_source=…) before appending the embed path.
    clean_url = url.split('?')[0].rstrip('/')
    content, err = fetch_url_content(clean_url + "/embed/captioned/")
    if content:
        return content, ""
    errors.append(f"Embed fallback: {err}")
    return "", "; ".join(errors)


# ---------------------------------------------------------------------------
# Save recipe to Supabase queue
# ---------------------------------------------------------------------------

def find_duplicate(sb, url: str | None = None, name: str | None = None) -> dict | None:
    """The library recipe a submission duplicates, matched on the source-URL key
    or the normalised name (find_duplicate_recipe, scripts/schema.sql 6g), or
    None. Without that function applied there's no check: it logs and carries on."""
    try:
        rows = sb.rpc("find_duplicate_recipe", {"p_url": url, "p_name": name}).execute().data
    except Exception as exc:
        logger.warning("duplicate check skipped: %s (apply scripts/schema.sql 6g?)", exc)
        return None
    return rows[0] if rows else None


def duplicate_response(sb, dup: dict) -> dict:
    """Already queued: say so. In the library but not queued: queue it again."""
    if dup["in_queue"]:
        message = f'"{dup["name"]}" is already in your queue.'
    else:
        sb.table("recipes").update({"in_queue": True, "queue_priority": 10}) \
            .eq("id", dup["id"]).execute()
        message = f'"{dup["name"]}" is already in your library. Queued it again.'
    return {"ok": True, "duplicate": True, "recipe_id": dup["id"],
            "name": dup["name"], "message": message}


def save_recipe_to_queue(sb, recipe_data: dict, source_url: str,
                         source_type: str) -> str:
    """Returns the new recipe ID."""
    # The planner filters on the "vegetarian" tag, so don't leave it to Claude
    # remembering to put it in tags: it answers the question explicitly.
    tags = recipe_data.get("tags") or []
    if recipe_data.get("vegetarian") is True and "vegetarian" not in tags:
        tags = [*tags, "vegetarian"]
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
        "tags": tags,
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

        def finish_duplicate(dup: dict) -> dict:
            sb.table("recipe_submissions").update({
                "status": "duplicate",
                "recipe_id": dup["id"],
                "processed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", sub_id).execute()
            return duplicate_response(sb, dup)

        # Same link already known: stop before fetching or calling Claude.
        dup = find_duplicate(sb, url=url)
        if dup:
            return finish_duplicate(dup)

        # Fetch content
        content, fetch_error = (fetch_instagram_content(url) if is_instagram
                                else fetch_url_content(url))

        if not content:
            sb.table("recipe_submissions").update({
                "status": "failed",
                "error_detail": fetch_error or "No content returned",
                "processed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", sub_id).execute()
            raise HTTPException(
                status_code=422,
                detail="Couldn't read that link. Try a different link.",
            )

        recipe, extract_error = extract_recipe_from_text(content, url)
        if not recipe:
            sb.table("recipe_submissions").update({
                "status": "failed",
                "error_detail": extract_error or f"Claude returned no recipe (content was {len(content)} chars)",
                "processed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", sub_id).execute()
            raise HTTPException(
                status_code=422,
                detail="Couldn't parse a recipe from that link. Try a different link.",
            )

        # Different link, same recipe: catch it by the name Claude extracted.
        dup = find_duplicate(sb, url=url, name=recipe["name"])
        if dup:
            return finish_duplicate(dup)

        recipe_id = save_recipe_to_queue(sb, recipe, url, source_type)

        sb.table("recipe_submissions").update({
            "status": "added",
            "recipe_id": recipe_id,
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", sub_id).execute()

        return {"ok": True, "recipe_id": recipe_id, "name": recipe["name"],
                "message": f'Added "{recipe["name"]}" to the queue.'}

    # A name-only submission used to create a stub with no ingredients, which
    # nothing in the app could fill in. Links only.
    raise HTTPException(status_code=400, detail="Provide a recipe link as 'url'")


class PlanSettings(BaseModel):
    # Bounds match the CHECK constraints on meal_settings in scripts/schema.sql.
    lunches: int = Field(ge=0, le=14)
    dinners: int = Field(ge=0, le=14)
    people: int = Field(ge=1, le=8)


@app.post("/trigger/saturday-suggest")
def trigger_saturday_suggest(
    background_tasks: BackgroundTasks,
    settings: PlanSettings | None = None,
    x_ingest_token: str | None = Header(default=None),
):
    """Trigger the Saturday meal-selection agent on demand. An optional body
    overrides the saved defaults for this run only."""
    require_token(x_ingest_token)
    background_tasks.add_task(_run_saturday, settings.model_dump() if settings else None)
    return {"ok": True, "message": "Meal plan generation started"}


def _run_saturday(settings: dict | None = None) -> None:
    from agents.saturday_suggest import main as saturday_main
    try:
        saturday_main(settings)
    except Exception as exc:
        logger.error("saturday_suggest trigger failed: %s", exc)


@app.get("/health")
def health():
    return {"status": "ok"}
