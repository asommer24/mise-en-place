# The Queue tab

The queue is the list of recipes you want soon. The Saturday planner favours
queued recipes, and a recipe leaves the queue when it is scheduled into a plan.
The Queue tab is the only place recipes are added from the app, and it takes
**links only**.

---

## Adding a recipe

Paste a recipe link (Instagram or any recipe page) into the box and press
**Add**. Pasted share text with a link inside it works too. Input with no link is
refused with a message.

1. The link is checked against the library first.
2. If it's new, the page or caption is fetched and Claude parses it into a
   recipe.
3. The recipe's name is then checked against the library.
4. If it's still new, it's added to the library and the queue.

The ingest endpoint (`POST /ingest`) only accepts requests that carry the
`X-Ingest-Token`, and only a `url`. The web app is the one caller that is set up
and supported.

**Why links only:** adding by name used to create a stub with no ingredients, and
nothing in the app can fill ingredients in. Such a recipe could be planned and add
nothing to the shopping list.

---

## Duplicates

### Same link

A link that is already in the library, queued or not, is **not added again**, and
nothing is fetched. What you see depends on where the match is:

| Match is | Result |
|---|---|
| Already in the queue | "*Name* is already in your queue." Nothing changes. |
| In the library, not queued | "*Name* is already in your library. Queued it again." It goes back on the queue. |

### Same name, different link

If a link is new but the recipe it parses to has the same name as one you already
have, a dialog asks what to do. The parsed recipe is held on the submission, so
your answer doesn't mean fetching or parsing the page again.

| Choice | What happens |
|---|---|
| **Replace the old recipe** | The old recipe is overwritten in place with the new one, and queued. It keeps its id, so past and current plans that use it keep working, and its times-suggested count carries over. |
| **Keep both** | The new recipe is added as *Name (label)*. The label starts as where the link points (`Instagram`, or the site, such as `allrecipes.com`) and you can edit it, for example to `NY Times`. |
| **Use the old recipe** | The new link is skipped and the existing recipe is queued instead. |

**Close without adding** leaves everything as it was: nothing is added or queued.
The submission stays recorded as waiting for a decision.

If you answer twice, or the matched recipe was deleted in the meantime, you're
told to paste the link again.

### The identifiers

Each recipe has two keys, **derived by the database** from columns it already
has. Every recipe, including older ones and the seeds, gets them automatically,
and there is one definition of "the same" for every writer.

| Key | Built from | Rule | Unique? |
|---|---|---|---|
| `source_key` | `source_url` | The link's identity (see below) | **Yes**, one recipe per link |
| `name_key` | `name` | Lower-cased, punctuation and spacing collapsed | No |

**`source_key`**
- Instagram posts, reels and IGTV are keyed on their **shortcode**:
  `instagram:Cabc123`. Tracking parameters (`?igsh=...`), `www.`, a username in
  the path and a trailing slash don't matter. These all give the same key:
  `https://www.instagram.com/p/Cabc123/?igsh=xyz`,
  `https://instagram.com/reel/Cabc123`,
  `https://www.instagram.com/chef.name/reel/Cabc123/`. The pattern is tied to the
  `instagram.com` host, so another site's URL that merely contains
  `instagram.com/p/...` isn't mistaken for a post.
- Any other link is lower-cased, minus `https://`, `www.`, the `#fragment` and
  trailing slashes. The **query string is kept**, because some sites identify
  the page by it (`youtube.com/watch?v=...`).
- Because it's unique in the database, even two submissions arriving at the same
  moment can't create two rows for one link. If that race does happen, the loser
  is reported as a duplicate.

**`name_key`**
- `Lemon  Orzo Salad!` and `lemon orzo salad` are the same key.
- Letters outside a-z (accents, other scripts) are kept, so `Pâté` and `Pt` stay
  different. Matching is accent-sensitive: `crème brûlée` and `creme brulee`
  don't match.
- A name with no letters or digits at all gets no key, so it never matches.
- **Recipes with no ingredients never match by name.** They can't be planned, so
  an old name-only stub doesn't block the real recipe from being added.

When a link could match on both, the same link wins over the same name.

### What it won't catch

- **The same recipe from two different links** when Claude names them
  differently ("Easy Lemon Orzo Salad" vs "Lemon Orzo Salad").
- **The same non-Instagram link with different query parameters**, such as
  `?utm_source=...`. If the extracted name matches, the name check asks you.

---

## Removing a recipe from the queue, and putting one back

Each queued recipe has a **remove** button. It takes the recipe **off the queue
only**:

- The recipe stays in the library and can still be picked by the planner. It just
  stops being a priority.
- There is no delete in the app. To delete a recipe entirely, use Supabase.

To queue a recipe that's already in the library, use **add to queue** on its card
in the Library tab. It shows only on recipes that aren't queued.

---

## Recipes added by name before this changed

Older name-only recipes have no ingredients. The planner skips any recipe with
none, and they no longer block a real recipe with the same name, but they still
show in the Library and the Queue. To clear them out, run in the Supabase SQL
editor. **Preview first:**

```sql
SELECT id, name, in_queue, source_url, created_at
FROM recipes
WHERE ingredients = '[]'::jsonb;
```

Check whether any are in a plan (those plans would show one recipe fewer):

```sql
SELECT wp.week_start
FROM weekly_plans wp
WHERE EXISTS (
    SELECT 1 FROM recipes r
    WHERE r.ingredients = '[]'::jsonb
      AND (r.id = ANY(wp.lunches) OR r.id = ANY(wp.dinners))
);
```

Then delete them. This permanently removes the rows. The first statement is
needed because `recipe_submissions.recipe_id` points at recipes:

```sql
BEGIN;
UPDATE recipe_submissions SET recipe_id = NULL
WHERE recipe_id IN (SELECT id FROM recipes WHERE ingredients = '[]'::jsonb);
DELETE FROM recipes WHERE ingredients = '[]'::jsonb;
COMMIT;
```

---

## The submission log

Every link submitted is recorded in `recipe_submissions`, with a status of
`processing`, `needs_decision`, `added`, `duplicate` or `failed`, and an error
detail when it failed. A duplicate records the recipe it matched. A failed save
is marked `failed` rather than left as `processing`.

---

## Database setup

| Needs | For | Without it |
|---|---|---|
| `schema.sql` **6f** | **Remove** and **add to queue** (it grants `in_queue` to the web app) | Both show an error and nothing changes |
| `schema.sql` **6g** | The `source_key` and `name_key` columns and `find_duplicate_recipe()` | No duplicate check at all; a warning is logged |
| `schema.sql` **6h** | Holding the parsed recipe while you decide on a name clash | A name clash is treated as a plain duplicate (no dialog); a warning is logged |

6g is safe to run again after the key rules change: it drops and re-adds the two
key columns so every row is recomputed. If its unique index fails to create, two
recipes already share a link. The query to find them is in a comment just above
that statement. Delete or re-link one of each pair, then run the statement
again. The lookup function is created before the index, so it exists either way.

---

## Where this lives in the code

| What | Where |
|---|---|
| Key definitions and the lookup function | `scripts/schema.sql`, section 6g |
| Parked recipe for a decision | `scripts/schema.sql`, section 6h |
| Duplicate check and responses | `agents/ingest.py`, `find_duplicate` and `duplicate_response` |
| Link flow | `agents/ingest.py`, `ingest` |
| Replace / keep both / use old | `agents/ingest.py`, `resolve` (`POST /ingest/resolve`) |
| The decision dialog | `frontend/src/App.jsx`, `ConflictModal` |
| Queue tab, remove and add to queue | `frontend/src/App.jsx`, `setQueuedState` and the Queue and Library tabs |
| Skipping recipes with no ingredients | `agents/saturday_suggest.py`, `fetch_recipes` |
| Queue permission | `scripts/schema.sql`, section 6f |
