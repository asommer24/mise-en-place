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

from supabase import create_client

from agents.claude import ask_json

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mise.saturday")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_supabase():
    return create_client(os.environ["SUPABASE_URL"],
                         os.environ["SUPABASE_SERVICE_KEY"])


def plan_monday(today: date | None = None) -> date:
    """The Monday a plan is filed under: this week's Monday Mon-Fri, the
    upcoming one from Saturday on. Keep in sync with planMonday() in
    frontend/src/App.jsx. `today` is injectable so the week math is testable
    without waiting for Saturday to come around."""
    today = today or date.today()
    wd = today.weekday()  # Monday = 0
    return today + timedelta(days=-wd if wd < 5 else 7 - wd)


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
  - 3 DINNER recipes: the dinner candidates below are already filtered to vegetarian recipes

Selection rules:
1. Prefer recipes marked in_queue=true (user submitted — they want these soon)
2. Avoid repeating recipes with high times_suggested values
3. Ensure variety: don't pick 3 pasta dishes or 3 soups
4. Balance nutrition across the week where possible
5. Aim for varied cuisines across the 3 dinners (e.g. Italian, Mexican, Asian)

Return ONLY valid JSON:
{
  "lunch_ids": ["<id>", "<id>", "<id>", "<id>", "<id>"],
  "dinner_ids": ["<id>", "<id>", "<id>"],
  "reasoning": "One short paragraph on why these eight."
}

Exactly 5 lunch_ids and 3 dinner_ids, no duplicates. Every id must be copied
exactly from the recipes provided. No markdown, no preamble.
"""


def validate_selection(result: dict, lunch_ids: set, dinner_ids: set) -> None:
    """
    Claude's output is a trust boundary: an id it invented would be written
    straight into weekly_plans.lunches and then blow up the downstream
    .in_() update with a UUID that matches nothing.
    """
    for key, want, valid in (("lunch_ids", 5, lunch_ids),
                             ("dinner_ids", 3, dinner_ids)):
        got = result.get(key) or []
        if len(got) != want:
            raise RuntimeError(f"Claude returned {len(got)} {key}, expected {want}")
        if len(set(got)) != want:
            raise RuntimeError(f"Claude returned duplicate {key}: {got}")
        unknown = [i for i in got if i not in valid]
        if unknown:
            raise RuntimeError(f"Claude returned unknown {key}: {unknown}")


def select_recipes_with_claude(recipes: dict) -> dict:
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

    result = ask_json(SELECTION_PROMPT, user_msg)
    validate_selection(result,
                       {r["id"] for r in recipes["lunches"]},
                       {r["id"] for r in recipes["dinners"]})
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
        # A cosmetic counter is not worth losing a good plan over — the RPC is
        # defined in scripts/schema.sql and may not be applied yet.
        try:
            sb.rpc("increment_times_suggested", {"recipe_id": recipe_id}).execute()
        except Exception as exc:
            logger.warning("increment_times_suggested failed for %s: %s "
                           "(apply scripts/schema.sql?)", recipe_id, exc)

    # Remove from queue now that they're scheduled
    sb.table("recipes").update({"in_queue": False}) \
        .in_("id", all_ids).execute()

    return plan_id


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    sb = get_supabase()
    week_start = plan_monday()

    # 1. Fetch recipes
    recipes = fetch_recipes(sb)
    if len(recipes["lunches"]) < 5 or len(recipes["dinners"]) < 3:
        raise RuntimeError("Not enough recipes in DB (need ≥5 lunches, ≥3 dinners)")

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
    try:
        main()
    except RuntimeError as exc:
        logger.error("%s", exc)
        sys.exit(1)
