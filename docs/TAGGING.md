# How recipes get their meal type, tags and servings

Three fields on each `recipes` row decide whether the planner can pick a recipe
and how much of the week it covers:

| Field | Type | What it decides |
|---|---|---|
| `meal_type` | `'lunch'` or `'dinner'` | Which pool the recipe is drawn from |
| `tags` | `text[]` | Free-text labels. Only `vegetarian` changes planning |
| `servings` | integer | How much of the week's servings target it covers |

**Lunch vs dinner is the `meal_type` column, not a tag.** A recipe is one or the
other, never both.

---

## When and how each field is set

| Source | `meal_type` | `tags` | `servings` |
|---|---|---|---|
| **Instagram / URL link** (Queue tab) | Claude decides | Claude decides, plus `vegetarian` from its explicit answer | Claude's reading of the recipe |
| **Seed recipes** (`scripts/schema.sql`) | Set by hand | Set by hand | Set by hand |
| **Edit button** (Library or Queue card) | You choose | You edit | You edit |

### Link ingest: how Claude decides

`agents/ingest.py` fetches the post's caption (or the page text, first 6,000
characters) and asks Claude to return a structured recipe. Nobody reviews the
result before it is saved.

**`meal_type`**
- `lunch` if the dish is a bowl, salad, soup, wrap, grain dish, or anything
  described as meal-prep friendly or make-ahead.
- `dinner` for everything else.
- The returned value is not validated at ingest, so an unexpected word from
  Claude would be stored as is.

**`vegetarian`**
- Claude answers an explicit true/false question: true only if the recipe has no
  meat, poultry, fish or shellfish. Eggs and dairy are fine. It answers false if
  the recipe contains any of those, or if it can't tell.
- When the answer is true, the code adds the `vegetarian` tag itself.
- If Claude doesn't answer at all, no tag is added. Nothing is guessed.

**Other tags**
- Free text from Claude, with no fixed vocabulary. The prompt's example is
  `["make-ahead", "healthy"]`. Expect things like cuisine, `quick`, `soup`.
- Nothing is enforced about them. They are for searching and filtering, and for
  giving the planner some context about variety.

### Recipes with no ingredients

Recipes can no longer be added by name only; the Queue takes links only (see
[QUEUE.md](QUEUE.md)). Name-only recipes added earlier have `meal_type = dinner`,
no tags and no ingredients. The edit dialog can fix the first two, but nothing in
the app adds ingredients, so the planner never picks a recipe with none.

### Recipes ingested before the vegetarian question existed

They only have the `vegetarian` tag if Claude happened to include it. Check the
audit query below and flip the rest by hand.

---

## Normalisation

A database trigger (`schema.sql` 6c) runs on every write to `tags`. It trims,
lower-cases, drops blanks and removes duplicates. The tags come back in
alphabetical order, not the order they were entered. So `Vegetarian`,
` vegetarian ` and `vegetarian` all end up as one `vegetarian` tag.

The planner's filter matches the exact string `vegetarian`. `veg`, `vegan` and
`plant-based` do not count.

---

## What the planner does with them

- **Pools.** Lunches come from `meal_type = 'lunch'`, dinners from
  `meal_type = 'dinner'`. Swapping a recipe offers only the same `meal_type`.
- **No ingredients, no plan.** A recipe with an empty ingredient list is left
  out of both pools, since it would add nothing to the shopping list.
- **Vegetarian switches** (Settings tab, see [SETTINGS.md](SETTINGS.md)). Each meal type has its own switch. When
  one is on, that pool is limited to recipes tagged `vegetarian`. Defaults:
  dinners on, lunches off.
- **Make-ahead is not enforced.** The selection prompt asks Claude to choose
  lunches that are make-ahead friendly, but the lunch pool is simply every
  `lunch` recipe. The `make-ahead` tag is informational.
- **Servings.** Each recipe's `servings` counts toward its pool's target. A
  recipe with no serving size counts as 4.
- If a pool can't cover its target, the Saturday job stops. With a vegetarian
  switch on, the message says so, e.g. "Not enough lunches tagged vegetarian
  (or turn vegetarian off in Settings)".

In the Library tab, selected meal types are OR'd (lunch or dinner) and selected
tags are AND'd (must have all of them).

---

## Editing from the web app

Each recipe card in the Library and Queue tabs has an **edit** button for
`meal_type`, servings and tags. Vegetarian is a switch over the `vegetarian`
tag; other tags are added and removed as chips.

Limits, enforced by the `Public edit recipe basics` policy (`schema.sql` 6f):

- `meal_type` must be `lunch` or `dinner`.
- `servings` between 1 and 25.
- At most 10 tags, counting `vegetarian`.

The web app can change only those three columns. These limits apply to edits
made from the web app. Ingest writes with the service key and isn't subject to
them, so a recipe from ingest can arrive with more than 10 tags; the edit dialog
asks you to trim it before saving. The `meal_type` column itself has no CHECK
constraint.

---

## Audit query

Run in the Supabase SQL editor to see how your library is tagged:

```sql
SELECT meal_type,
       count(*) AS total,
       count(*) FILTER (WHERE 'vegetarian' = ANY(tags)) AS vegetarian,
       count(*) FILTER (WHERE tags = '{}') AS no_tags
FROM recipes GROUP BY meal_type;
```

---

## Where this lives in the code

| What | Where |
|---|---|
| Ingest prompt (`meal_type` and `vegetarian` rules) | `agents/ingest.py`, `RECIPE_EXTRACT_PROMPT` |
| Adding the `vegetarian` tag | `agents/ingest.py`, `save_recipe_to_queue` |
| Pools, vegetarian filter, servings | `agents/saturday_suggest.py`, `fetch_recipes` and `servings` |
| Tag normalisation | `scripts/schema.sql`, section 6c |
| Edit permissions and limits | `scripts/schema.sql`, section 6f |
| Edit dialog | `frontend/src/App.jsx`, `EditRecipeModal` |
