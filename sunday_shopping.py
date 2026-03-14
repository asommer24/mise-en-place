"""
sunday_shopping.py
------------------
Runs Sunday at 1:00 AM. 

  1. Pulls the confirmed (or suggested) weekly plan
  2. Aggregates all ingredients across the 8 recipes
  3. Uses Claude to deduplicate and combine quantities
  4. Writes a categorized shopping list to:
       a. Supabase (for the web app to display)
       b. Apple Reminders via CalDAV (works on iPhone/Mac natively)
          — OR — Todoist if you prefer cross-platform

Env vars required:
    ANTHROPIC_API_KEY
    SUPABASE_URL
    SUPABASE_SERVICE_KEY

For Apple Reminders (CalDAV):
    CALDAV_URL            (iCloud CalDAV endpoint)
    CALDAV_USERNAME       (Apple ID email)
    CALDAV_PASSWORD       (app-specific password from appleid.apple.com)
    REMINDERS_LIST_NAME   (e.g. "Grocery List")

  — OR — for Todoist:
    TODOIST_API_KEY
    TODOIST_PROJECT_NAME  (e.g. "Groceries")
    SHOPPING_BACKEND      (set to "todoist" to use Todoist instead of CalDAV)
"""

import json
import logging
import os
import sys
from datetime import date, timedelta, datetime, timezone

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
# Step 4a: Write to Apple Reminders via CalDAV
# ---------------------------------------------------------------------------

def write_to_apple_reminders(items: list[dict], week_start: date) -> None:
    """
    Uses CalDAV to create a reminder for each ingredient in a dedicated list.
    
    Setup:
      1. Go to appleid.apple.com → Security → App-Specific Passwords → Generate
      2. Use that password as CALDAV_PASSWORD
      3. CALDAV_URL = https://caldav.icloud.com
      4. Create a "Grocery List" list in Reminders.app first
    """
    try:
        import caldav
    except ImportError:
        logger.error("caldav package not installed: pip install caldav")
        raise

    url      = os.environ["CALDAV_URL"]
    username = os.environ["CALDAV_USERNAME"]
    password = os.environ["CALDAV_PASSWORD"]
    list_name = os.getenv("REMINDERS_LIST_NAME", "Grocery List")

    client = caldav.DAVClient(url=url, username=username, password=password)
    principal = client.principal()

    # Find our target calendar/list
    calendars = principal.calendars()
    target = next((c for c in calendars
                   if list_name.lower() in c.name.lower()), None)
    if not target:
        logger.warning("List '%s' not found; using first calendar", list_name)
        target = calendars[0]

    # Delete old items from this list first (avoid duplicates)
    week_label = week_start.strftime("%b %-d")
    try:
        existing = target.search(todo=True)
        for task in existing:
            vtodo = task.vobject_instance.vtodo
            summary = str(vtodo.summary.value) if hasattr(vtodo, 'summary') else ""
            if summary.startswith(f"[{week_label}]"):
                task.delete()
    except Exception as exc:
        logger.warning("Could not clean old reminders: %s", exc)

    # Group by category for ordered creation
    by_category: dict[str, list] = {}
    for item in items:
        cat = item.get("category", "other")
        by_category.setdefault(cat, []).append(item)

    now = datetime.now(timezone.utc)
    created = 0

    for category in GROCERY_CATEGORIES:
        cat_items = by_category.get(category, [])
        for item in cat_items:
            amount_str = f"{item.get('amount','')} {item.get('unit','')}".strip()
            summary = f"[{week_label}] {item['name']}"
            if amount_str:
                summary += f" — {amount_str}"
            notes = f"For: {', '.join(item.get('for_recipes', []))}"

            # Build VTODO (iCalendar format)
            vtodo_str = (
                "BEGIN:VCALENDAR\r\n"
                "VERSION:2.0\r\n"
                "BEGIN:VTODO\r\n"
                f"SUMMARY:{summary}\r\n"
                f"DESCRIPTION:{notes}\r\n"
                f"CATEGORIES:{category.upper()}\r\n"
                "STATUS:NEEDS-ACTION\r\n"
                f"DTSTAMP:{now.strftime('%Y%m%dT%H%M%SZ')}\r\n"
                f"UID:{week_label}-{item['name'].replace(' ','-').lower()}@mise\r\n"
                "END:VTODO\r\n"
                "END:VCALENDAR\r\n"
            )
            target.save_todo(vtodo_str)
            created += 1

    logger.info("Created %d Apple Reminders in '%s'", created, list_name)


# ---------------------------------------------------------------------------
# Step 4b: Write to Todoist (alternative backend)
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
    backend = os.getenv("SHOPPING_BACKEND", "apple")  # 'apple' or 'todoist'

    # 1. Fetch plan
    data = fetch_plan_with_recipes(sb)
    plan = data["plan"]
    recipes = data["recipes"]
    week_start = date.fromisoformat(plan["week_start"])

    # 2. Aggregate raw ingredients
    raw_ingredients = aggregate_ingredients(recipes)

    # 3. Claude consolidates
    consolidated = consolidate_with_claude(raw_ingredients)

    # 4. Write to chosen backend
    if backend == "todoist":
        write_to_todoist(consolidated, week_start)
    else:
        write_to_apple_reminders(consolidated, week_start)

    # 5. Save to Supabase (for web app display)
    save_shopping_list(sb, plan["id"], consolidated)

    # 6. Log
    sb.table("meal_pipeline_runs").insert({
        "job": "sunday_shopping",
        "status": "success",
        "details": {
            "plan_id": plan["id"],
            "items": len(consolidated),
            "backend": backend,
        }
    }).execute()

    logger.info("Sunday shopping list complete ✓ (%d items via %s)",
                len(consolidated), backend)


if __name__ == "__main__":
    main()
