"""
saturday_suggest.py
-------------------
Runs every Saturday morning. Uses Claude to:
  1. Pull recipes from Supabase (prioritising the queue)
  2. Select lunches + dinners whose servings cover the week:
     (lunches + dinners) * people, with lunches and dinners covered separately
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


# Keep in sync with the meal_settings defaults in scripts/schema.sql and
# DEFAULT_SETTINGS in frontend/src/App.jsx.
DEFAULT_SETTINGS = {"lunches": 5, "dinners": 3, "people": 2}
DEFAULT_SERVINGS = 4   # for recipes saved without a serving size


def servings(recipe: dict) -> int:
    return recipe.get("servings") or DEFAULT_SERVINGS


def targets(settings: dict) -> tuple[int, int]:
    """(lunch servings, dinner servings) the week must cover."""
    return (settings["lunches"] * settings["people"],
            settings["dinners"] * settings["people"])


def load_settings(sb) -> dict:
    """The saved defaults. Falls back to DEFAULT_SETTINGS so a missing table
    (schema is applied by hand) doesn't stop the Saturday cron."""
    try:
        row = (sb.table("meal_settings").select("lunches,dinners,people")
               .eq("id", 1).maybe_single().execute().data)
    except Exception as exc:
        logger.warning("meal_settings unreadable, using defaults: %s "
                       "(apply scripts/schema.sql 6d?)", exc)
        row = None
    return {**DEFAULT_SETTINGS, **(row or {})}


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
# Step 2: Claude picks recipes that cover the servings
# ---------------------------------------------------------------------------

def build_prompt(lunch_target: int, dinner_target: int) -> str:
    return f"""
You are a meal planning assistant for a vegetarian household. Your job is to choose:
  - LUNCH recipes (make-ahead friendly: prepped Sunday, refrigerated, reheated or eaten cold all week) whose servings add up to at least {lunch_target}
  - DINNER recipes (the candidates below are already filtered to vegetarian) whose servings add up to at least {dinner_target}

Each recipe has a "servings" count. Whole recipes only, and leftovers are the
point, but don't overshoot: removing the recipe with the fewest servings from
a list must drop its total below the target. A target of 0 means an empty list.

Selection rules:
1. Prefer recipes marked in_queue=true (user submitted — they want these soon)
2. Avoid repeating recipes with high times_suggested values
3. Ensure variety: don't pick 3 pasta dishes or 3 soups
4. Balance nutrition across the week where possible
5. Aim for varied cuisines across the dinners (e.g. Italian, Mexican, Asian)

Return ONLY valid JSON:
{{
  "lunch_ids": ["<id>", ...],
  "dinner_ids": ["<id>", ...],
  "reasoning": "One short paragraph on why these."
}}

No duplicates. Every id must be copied exactly from the recipes provided.
No markdown, no preamble.
"""


def validate_selection(result: dict, lunches: dict, dinners: dict,
                       lunch_target: int, dinner_target: int) -> None:
    """
    Claude's output is a trust boundary: an id it invented would be written
    straight into weekly_plans.lunches and then blow up the downstream
    .in_() update with a UUID that matches nothing. `lunches`/`dinners` map
    recipe id -> recipe. Each list must cover its target with no spare recipe.
    """
    for key, pool, target in (("lunch_ids", lunches, lunch_target),
                              ("dinner_ids", dinners, dinner_target)):
        got = result.get(key) or []
        if len(set(got)) != len(got):
            raise RuntimeError(f"Claude returned duplicate {key}: {got}")
        unknown = [i for i in got if i not in pool]
        if unknown:
            raise RuntimeError(f"Claude returned unknown {key}: {unknown}")
        have = [servings(pool[i]) for i in got]
        if sum(have) < target:
            raise RuntimeError(f"{key} give {sum(have)} servings, need {target}")
        if have and sum(have) - min(have) >= target:
            raise RuntimeError(f"{key} give {sum(have)} servings for {target}: "
                               "drop the smallest recipe")


def pick_fallback(pool: list, target: int) -> list:
    """Queue first, then least-used, until the target is covered. Only used
    when Claude's picks are rejected twice, so it doesn't try to be clever."""
    ids, total = [], 0
    for r in sorted(pool, key=lambda r: (not r.get("in_queue"),
                                         r.get("times_suggested") or 0)):
        if total >= target:
            break
        ids.append(r["id"])
        total += servings(r)
    return ids


def select_recipes_with_claude(recipes: dict, settings: dict) -> dict:
    lunch_target, dinner_target = targets(settings)
    lunches = {r["id"]: r for r in recipes["lunches"]}
    dinners = {r["id"]: r for r in recipes["dinners"]}

    # Build a compact recipe list for the prompt
    all_recipes = []
    for r in recipes["lunches"] + recipes["dinners"]:
        all_recipes.append({
            "id": r["id"],
            "name": r["name"],
            "meal_type": r["meal_type"],
            "servings": servings(r),
            "description": r.get("description", ""),
            "tags": r.get("tags", []),
            "times_suggested": r.get("times_suggested", 0),
            "in_queue": r.get("in_queue", False),
            "queue_priority": r.get("queue_priority", 0),
        })

    prompt = build_prompt(lunch_target, dinner_target)
    user_msg = f"Available recipes:\n{json.dumps(all_recipes, indent=2)}"

    for _ in range(2):
        result = ask_json(prompt, user_msg)
        try:
            validate_selection(result, lunches, dinners,
                               lunch_target, dinner_target)
            break
        except RuntimeError as exc:
            logger.warning("Claude's selection rejected: %s", exc)
            user_msg += f"\n\nYour previous answer was rejected: {exc}. Fix it."
    else:
        result = {
            "lunch_ids": pick_fallback(recipes["lunches"], lunch_target),
            "dinner_ids": pick_fallback(recipes["dinners"], dinner_target),
            "reasoning": "Picked by queue, then least-used, to cover the servings.",
        }

    logger.info("Selected lunches=%s dinners=%s | %s",
                result["lunch_ids"], result["dinner_ids"],
                result.get("reasoning", ""))
    return result


# ---------------------------------------------------------------------------
# Step 3: Save plan + increment times_suggested
# ---------------------------------------------------------------------------

def save_weekly_plan(sb, selection: dict, week_start: date,
                     settings: dict) -> str:
    """Creates or updates the weekly_plan row. Returns plan ID."""
    plan_data = {
        "week_start": week_start.isoformat(),
        "lunches": selection["lunch_ids"],
        "dinners": selection["dinner_ids"],
        "status": "suggested",
        "notes": selection.get("reasoning", ""),
        "settings": settings,   # what this plan was generated for
    }

    # Upsert on week_start
    def upsert():
        return (sb.table("weekly_plans")
                .upsert(plan_data, on_conflict="week_start").execute())
    try:
        res = upsert()
    except Exception as exc:
        # The settings column comes from scripts/schema.sql 6d, applied by hand.
        # A plan without it beats no plan; regenerate then reopens on defaults.
        if "settings" not in str(exc):
            raise
        logger.warning("weekly_plans.settings missing, saving without it "
                       "(apply scripts/schema.sql 6d?)")
        del plan_data["settings"]
        res = upsert()
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
    if all_ids:
        sb.table("recipes").update({"in_queue": False}) \
            .in_("id", all_ids).execute()

    return plan_id


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(settings: dict | None = None) -> None:
    """`settings` overrides the saved defaults for this run (the web app's
    per-week override); the Saturday cron passes nothing."""
    sb = get_supabase()
    week_start = plan_monday()
    settings = settings or load_settings(sb)

    # 1. Fetch recipes
    recipes = fetch_recipes(sb)
    for kind, target in zip(("lunches", "dinners"), targets(settings)):
        if sum(servings(r) for r in recipes[kind]) < target:
            raise RuntimeError(f"Not enough {kind} in DB to cover {target} servings")

    # 2. Claude selection
    selection = select_recipes_with_claude(recipes, settings)

    # 3. Save plan
    plan_id = save_weekly_plan(sb, selection, week_start, settings)

    # 4. Log run
    sb.table("meal_pipeline_runs").insert({
        "job": "saturday_suggest",
        "status": "success",
        "details": {
            "plan_id": plan_id,
            "week_start": week_start.isoformat(),
            "settings": settings,
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
