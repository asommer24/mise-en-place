"""
sunday_shopping.py
------------------
Runs Sunday at 1:00 AM. 

  1. Pulls the confirmed (or suggested) weekly plan
  2. Aggregates all ingredients across the 8 recipes
  3. Uses Claude to deduplicate and combine quantities
  4. Writes a categorized shopping list to:
       a. Supabase (for the web app to display)
       b. Todoist (so you can check items off while shopping)

Env vars required:
    ANTHROPIC_API_KEY
    SUPABASE_URL
    SUPABASE_SERVICE_KEY
    TODOIST_API_KEY
    TODOIST_PROJECT_NAME  (optional, defaults to "Groceries")
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
logger = logging.getLogger("mise.sunday")


GROCERY_CATEGORIES = [
    "produce", "protein", "dairy", "grain", "bakery",
    "pantry", "condiment", "frozen", "other"
]

CONSOLIDATION_PROMPT = """
You are a precise grocery list consolidator. Given a raw list of ingredients
from multiple recipes, you will:
  1. Merge duplicate items (e.g. "2 lemons" + "3 lemons" → "5 lemons")
  2. Convert units sensibly (e.g. "16 tbsp butter" → "1 cup butter")
  3. Group items into grocery store sections
  4. Note which recipe(s) each item is for

Return ONLY a JSON array. Each element:
{
  "name": "chicken thighs",
  "amount": "3",
  "unit": "lbs",
  "category": "protein",
  "for_recipes": ["Greek Chicken Bowls", "Sheet Pan Chicken"],
  "checked": false
}

Valid categories: produce, protein, dairy, grain, bakery, pantry, condiment, frozen, other
No markdown, no preamble. Only the JSON array.
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_supabase():
    return create_client(os.environ["SUPABASE_URL"],
                         os.environ["SUPABASE_SERVICE_KEY"])


def get_week_start() -> date:
    """Returns Monday of the current week."""
    today = date.today()
    return today - timedelta(days=today.weekday())


# ---------------------------------------------------------------------------
# Step 1: Pull the weekly plan + recipe ingredients
# ---------------------------------------------------------------------------

def fetch_plan_with_recipes(sb) -> dict:
    week_start = get_week_start()

    plan_res = (
        sb.table("weekly_plans")
        .select("*")
        .eq("week_start", week_start.isoformat())
        .maybe_single()
        .execute()
    )
    if not plan_res.data:
        logger.error("No plan found for week of %s", week_start)
        sys.exit(1)

    plan = plan_res.data
    all_recipe_ids = plan["lunches"] + plan["dinners"]

    recipes_res = (
        sb.table("recipes")
        .select("id, name, ingredients")
        .in_("id", all_recipe_ids)
        .execute()
    )
    return {"plan": plan, "recipes": recipes_res.data or []}


# ---------------------------------------------------------------------------
# Step 2: Aggregate raw ingredients
# ---------------------------------------------------------------------------

def aggregate_ingredients(recipes: list[dict]) -> list[dict]:
    """Flatten all ingredient lists, tagging each with its recipe name."""
    raw = []
    for recipe in recipes:
        ings = recipe.get("ingredients", [])
        if isinstance(ings, str):
            ings = json.loads(ings)
        for ing in ings:
            ing["_recipe"] = recipe["name"]
            raw.append(ing)
    logger.info("Aggregated %d raw ingredient lines", len(raw))
    return raw


# ---------------------------------------------------------------------------
# Step 3: Claude consolidates + categorizes
# ---------------------------------------------------------------------------

def consolidate_with_claude(raw_ingredients: list[dict]) -> list[dict]:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    user_msg = f"Raw ingredients:\n{json.dumps(raw_ingredients, indent=2)}"

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        system=CONSOLIDATION_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    consolidated = json.loads(response.content[0].text.strip())
    logger.info("Consolidated to %d shopping list items", len(consolidated))
    return consolidated


# ---------------------------------------------------------------------------
# Step 4: Write to Todoist
# ---------------------------------------------------------------------------

def write_to_todoist(items: list[dict], week_start: date) -> None:
    """
    Creates a section per grocery category in your Todoist project.
    Requires: pip install todoist-api-python
    """
    try:
        from todoist_api_python.api import TodoistAPI
    except ImportError:
        logger.error("todoist-api-python not installed: pip install todoist-api-python")
        raise

    api = TodoistAPI(os.environ["TODOIST_API_KEY"])
    project_name = os.getenv("TODOIST_PROJECT_NAME", "Groceries")

    # Find or create project
    projects = api.get_projects()
    project = next((p for p in projects
                    if p.name.lower() == project_name.lower()), None)
    if not project:
        project = api.add_project(name=project_name)

    # Close/archive old tasks
    existing_tasks = api.get_tasks(project_id=project.id)
    for task in existing_tasks:
        api.close_task(task_id=task.id)

    week_label = week_start.strftime("%b %-d")
    by_category: dict[str, list] = {}
    for item in items:
        cat = item.get("category", "other")
        by_category.setdefault(cat, []).append(item)

    for category in GROCERY_CATEGORIES:
        cat_items = by_category.get(category, [])
        if not cat_items:
            continue

        # Create a section header task
        api.add_task(
            content=f"── {category.upper()} ──",
            project_id=project.id,
            labels=["header"],
        )

        for item in cat_items:
            amount_str = f"{item.get('amount','')} {item.get('unit','')}".strip()
            label = f"{item['name']}"
            if amount_str:
                label += f" ({amount_str})"
            desc = f"For: {', '.join(item.get('for_recipes', []))}"

            api.add_task(
                content=label,
                description=desc,
                project_id=project.id,
            )

    logger.info("Created Todoist shopping list for week of %s", week_label)


# ---------------------------------------------------------------------------
# Step 5: Save consolidated list to Supabase
# ---------------------------------------------------------------------------

def save_shopping_list(sb, plan_id: str, items: list[dict]) -> None:
    sb.table("weekly_plans").update({
        "shopping_list": items,
        "status": "shopping_done",
    }).eq("id", plan_id).execute()
    logger.info("Shopping list saved to Supabase (%d items)", len(items))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    sb = get_supabase()

    # 1. Fetch plan
    data = fetch_plan_with_recipes(sb)
    plan = data["plan"]
    recipes = data["recipes"]
    week_start = date.fromisoformat(plan["week_start"])

    # 2. Aggregate raw ingredients
    raw_ingredients = aggregate_ingredients(recipes)

    # 3. Claude consolidates
    consolidated = consolidate_with_claude(raw_ingredients)

    # 4. Write to Todoist
    write_to_todoist(consolidated, week_start)

    # 5. Save to Supabase (for web app display)
    save_shopping_list(sb, plan["id"], consolidated)

    # 6. Log
    sb.table("meal_pipeline_runs").insert({
        "job": "sunday_shopping",
        "status": "success",
        "details": {
            "plan_id": plan["id"],
            "items": len(consolidated),
            "backend": "todoist",
        }
    }).execute()

    logger.info("Sunday shopping list complete ✓ (%d items via Todoist)",
                len(consolidated))


if __name__ == "__main__":
    main()
