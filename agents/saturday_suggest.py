"""
saturday_suggest.py
-------------------
Runs every Saturday morning. Uses Claude to:
  1. Pull recipes from Supabase (prioritising the queue)
  2. Select 5 make-ahead lunches + 3 dinners for the week
  3. Create a weekly_plan row

The plan is then viewed in the web app — no notifications are sent.

Env vars required:
    ANTHROPIC_API_KEY
    SUPABASE_URL
    SUPABASE_SERVICE_KEY
"""

import json
import logging
import os
import sys
from datetime import date, timedelta

import anthropic
from supabase import create_client

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mise.saturday")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_supabase():
    return create_client(os.environ["SUPABASE_URL"],
                         os.environ["SUPABASE_SERVICE_KEY"])


def next_monday() -> date:
    today = date.today()
    days_ahead = 7 - today.weekday()  # Monday = 0
    if days_ahead == 7:
        days_ahead = 0
    return today + timedelta(days=days_ahead)


# ---------------------------------------------------------------------------
# Step 1: Fetch recipes from Supabase
# ---------------------------------------------------------------------------

def fetch_recipes(sb) -> dict:
    """Returns {'lunches': [...], 'dinners': [...], 'queued': [...]}"""
    # Queued recipes (user-submitted, prioritised)
    queued = (
        sb.table("recipes")
        .select("*")
        .eq("in_queue", True)
        .order("queue_priority", desc=True)
        .execute()
    ).data or []

    # All lunches
    lunches = (
        sb.table("recipes")
        .select("*")
        .eq("meal_type", "lunch")
        .order("times_suggested", desc=False)   # prefer less-used recipes
        .execute()
    ).data or []

    # All dinners — vegetarian only. "vegetarian" is a value in the tags[]
    # array (there is no vegetarian column), so filter with contains().
    dinners = (
        sb.table("recipes")
        .select("*")
        .eq("meal_type", "dinner")
        .contains("tags", ["vegetarian"])
        .order("times_suggested", desc=False)
        .execute()
    ).data or []

    logger.info("fetch_recipes returning: lunches=%d dinners=%d queued=%d",
                len(lunches), len(dinners), len(queued))
    return {"lunches": lunches, "dinners": dinners, "queued": queued}


# ---------------------------------------------------------------------------
# Step 2: Claude selects the best 5+3
# ---------------------------------------------------------------------------

SELECTION_PROMPT = """
You are a meal planning assistant for a vegetarian household. Your job is to choose:
  - 5 LUNCH recipes: must be make-ahead friendly (can be prepped Sunday, refrigerated, reheated or eaten cold all week)
  - 3 DINNER recipes: MUST be vegetarian only — no meat, no fish, no poultry of any kind

Selection rules:
1. Prefer recipes marked in_queue=true (user submitted — they want these soon)
2. For dinners, ONLY select recipes where vegetarian=true — this is a strict requirement
3. Avoid repeating recipes with high times_suggested values
4. Ensure variety: don't pick 3 pasta dishes or 3 soups
5. Balance nutrition across the week where possible
6. Aim for varied cuisines across the 3 dinners (e.g. Italian, Mexican, Asian)
"""


def select_recipes_with_claude(recipes: dict) -> dict:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # Build a compact recipe list for the prompt
    all_recipes = []
    for r in recipes["lunches"] + recipes["dinners"]:
        all_recipes.append({
            "id": r["id"],
            "name": r["name"],
            "meal_type": r["meal_type"],
            "description": r.get("description", ""),
            "tags": r.get("tags", []),
            "times_suggested": r.get("times_suggested", 0),
            "in_queue": r.get("in_queue", False),
            "queue_priority": r.get("queue_priority", 0),
        })

    user_msg = f"Available recipes:\n{json.dumps(all_recipes, indent=2)}"

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        system=SELECTION_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )

    result = json.loads(response.content[0].text.strip())
    logger.info("Claude selected lunches=%s dinners=%s | %s",
                result["lunch_ids"], result["dinner_ids"], result["reasoning"])
    return result


# ---------------------------------------------------------------------------
# Step 3: Save plan + increment times_suggested
# ---------------------------------------------------------------------------

def save_weekly_plan(sb, selection: dict, week_start: date) -> str:
    """Creates or updates the weekly_plan row. Returns plan ID."""
    plan_data = {
        "week_start": week_start.isoformat(),
        "lunches": selection["lunch_ids"],
        "dinners": selection["dinner_ids"],
        "status": "suggested",
        "notes": selection.get("reasoning", ""),
    }

    # Upsert on week_start
    res = (
        sb.table("weekly_plans")
        .upsert(plan_data, on_conflict="week_start")
        .execute()
    )
    plan_id = res.data[0]["id"]
    logger.info("Saved weekly plan %s for week of %s", plan_id, week_start)

    # Increment times_suggested for chosen recipes
    all_ids = selection["lunch_ids"] + selection["dinner_ids"]
    for recipe_id in all_ids:
        sb.rpc("increment_times_suggested", {"recipe_id": recipe_id}).execute()

    # Remove from queue now that they're scheduled
    queued_ids = [r for r in all_ids]
    sb.table("recipes").update({"in_queue": False}) \
        .in_("id", queued_ids).execute()

    return plan_id


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    sb = get_supabase()
    week_start = next_monday()

    # 1. Fetch recipes
    recipes = fetch_recipes(sb)
    if len(recipes["lunches"]) < 5 or len(recipes["dinners"]) < 3:
        logger.error("Not enough recipes in DB (need ≥5 lunches, ≥3 dinners)")
        sys.exit(1)

    # 2. Claude selection
    selection = select_recipes_with_claude(recipes)

    # 3. Save plan
    plan_id = save_weekly_plan(sb, selection, week_start)

    # 4. Log run
    sb.table("meal_pipeline_runs").insert({
        "job": "saturday_suggest",
        "status": "success",
        "details": {
            "plan_id": plan_id,
            "week_start": week_start.isoformat(),
            "lunch_ids": selection["lunch_ids"],
            "dinner_ids": selection["dinner_ids"],
        }
    }).execute()

    logger.info("Saturday suggestion complete ✓")


if __name__ == "__main__":
    main()
