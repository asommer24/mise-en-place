# The Settings tab

Settings holds the household defaults the planner works from: how many lunches
and dinners you eat in a week, how many people are eating, and whether lunches
and dinners should be vegetarian. You can open it directly with
`?tab=settings` (the Generate dialog's "Settings" link does this in a new tab).

---

## What you can set

| Setting | Range | Default | Meaning |
|---|---|---|---|
| Lunches per week | 0 to 14 | 5 | Make-ahead lunches eaten each week |
| Dinners per week | 0 to 14 | 3 | Dinners cooked each week |
| People eating | 1 to 8 | 2 | Everyone sharing these meals |
| Vegetarian lunches | on / off | off | Limit lunch recipes to the `vegetarian` tag |
| Vegetarian dinners | on / off | on | Limit dinner recipes to the `vegetarian` tag |

**Save defaults** writes all five. **Reset to 5 · 3 · 2** resets only the three
numbers in the form, and the vegetarian switches stay as they are. Neither
takes effect until you save.

The defaults for vegetarian match what the planner did before the switches
existed: dinners were filtered, lunches weren't.

---

## What it governs

### How many servings the week needs

```
servings needed = (lunches + dinners) × people
```

With the defaults that's `(5 + 3) × 2 = 16`. Lunches and dinners are covered
separately: `lunches × people` for lunches (10) and `dinners × people` for
dinners (6).

The planner then picks **whole recipes** whose `servings` add up to at least
each target, with no spare recipe. If any one recipe could be dropped and the
target would still be met, the pick is rejected. Recipes with no serving size
count as 4. Because recipes aren't scaled, plans usually land a little over, and
the extra servings are your leftovers.

The "about N recipes" figures on the Settings page assume 4 servings per recipe.
They're an estimate, not what the planner will choose.

### Which recipes are eligible

Each vegetarian switch limits its own pool to recipes tagged `vegetarian`. How
that tag gets set is in [TAGGING.md](TAGGING.md). If a pool can't cover its
target, generation stops with a message like "Not enough lunches tagged
vegetarian (or turn vegetarian off in Settings)".

### Who reads these settings

| Caller | Which numbers it uses |
|---|---|
| **Saturday cron** (GitHub Actions) | Always the saved defaults |
| **Generate / regenerate** on This Week | Opens on the week's numbers (see below), then sends them for that run |
| **`python -m agents.saturday_suggest`** | The saved defaults |

The Sunday shopping job doesn't read settings. It builds the list from whatever
recipes are in the week's plan.

---

## Per-week changes in the Generate dialog

The Generate and regenerate buttons open a dialog where lunches, dinners and
people can be changed **for that week only**.

- The values are stored on the plan (`weekly_plans.settings`), never on the
  saved defaults.
- Regenerating the same week reopens on that plan's numbers. A week with no plan,
  or one you cleared, opens on your defaults.
- Changing the defaults later doesn't touch plans that already exist.
- The vegetarian switches are not editable there. When at least one is on, a note
  at the bottom of the dialog says "Currently suggesting vegetarian meals for
  lunch / dinner / lunch and dinner", with a link to Settings. Vegetarian always
  comes from the saved defaults.

Under the plan's title, This Week shows a line such as `16 servings needed · 20
planned · 4 leftover`. It recalculates after a swap, and says "short" if the
picks no longer cover the target. Plans created before this feature have no
saved numbers, so they only show how many servings are planned.

---

## What it does not govern

- **The shopping list.** It's consolidated from the recipes in the plan at their
  written quantities. Settings doesn't scale ingredients.
- **A recipe's own meal type, tags or servings.** Those are edited per recipe
  (see [TAGGING.md](TAGGING.md)).
- **Which week is shown.** That's always the current plan week.

---

## Where the settings are stored

`meal_settings` is a one-row table (`id = 1`) created in `scripts/schema.sql`
sections 6d and 6e. The database enforces the ranges above. `weekly_plans.settings`
(a JSON column added in 6d) holds the per-week numbers.

If those sections haven't been applied, the app keeps working on the built-in
defaults, but **Save defaults** shows a "Couldn't save" error. A plan generated
without the `settings` column is saved without its per-week numbers, so
regenerating it reopens on the defaults.

The web app's public key can read and update `meal_settings`. For a private
two-person app the worst a stranger could do is change five small values.

---

## Where this lives in the code

| What | Where |
|---|---|
| Settings tab, steppers, switches | `frontend/src/App.jsx`, `SettingsTab` |
| Generate dialog and the vegetarian note | `frontend/src/App.jsx`, `GenerateModal` |
| Default values | `DEFAULT_SETTINGS` in `frontend/src/App.jsx` and `agents/saturday_suggest.py` (keep in sync) |
| Servings targets, picking, fallback | `agents/saturday_suggest.py`, `targets`, `validate_selection`, `pick_fallback` |
| Loading saved defaults | `agents/saturday_suggest.py`, `load_settings` |
| Per-run override and its validation | `agents/ingest.py`, `PlanSettings` and `/trigger/saturday-suggest` |
| Tables and policies | `scripts/schema.sql`, sections 6d and 6e |
