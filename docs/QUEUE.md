# The Queue tab

The queue is the list of recipes you want soon. The Saturday planner favours
queued recipes, and a recipe leaves the queue when it is scheduled into a plan.
The Queue tab is the only place recipes are added from the app, and it takes
**links only**.

---

## Adding a recipe

Paste a recipe link (Instagram or any recipe page) into the box and press
**Add**. Anything that isn't a link is refused with a message.

1. The link is checked against the library first.
2. If it's new, the page or caption is fetched and Claude parses it into a
   recipe.
3. The recipe's name is then checked too.
4. If it's still new, it's added to the library and the queue.

The ingest endpoint (`POST /ingest`) only accepts requests that carry the
`X-Ingest-Token`, and only a `url`. The web app is the one caller that is set up
and supported.

**Why links only:** adding by name used to create a stub with no ingredients, and
nothing in the app can fill ingredients in. Such a recipe could be planned and add
nothing to the shopping list.

---

## Duplicates

A link that matches a recipe already in the library, whether it's queued or not,
is **not added again**. What you see depends on where the match is:

| Match is | Result |
|---|---|
| Already in the queue | "*Name* is already in your queue." Nothing changes. |
| In the library, not queued | "*Name* is already in your library. Queued it again." It goes back on the queue. |

The queue list refreshes after every add, so a re-queued recipe appears at once.

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
  `https://www.instagram.com/chef.name/reel/Cabc123/`.
- Any other link is lower-cased, minus `https://`, `www.`, the `#fragment` and
  trailing slashes. The **query string is kept**, because some sites identify
  the page by it (`youtube.com/watch?v=...`).
- Because it's unique in the database, even two submissions arriving at the same
  moment can't create two rows for one link.

**`name_key`**
- `Lemon  Orzo Salad!` and `lemon orzo salad` are the same key.
- It's not unique, since two different recipes can share a name. The ingest
  endpoint treats a name match as a duplicate anyway.
- A name with no letters or digits a-z/0-9 (for example, all non-Latin
  characters) gets no key, so it never matches anything by name.

When a link could match on both, the same link wins over the same name.

### What it won't catch

- **The same recipe from two different links** when Claude names them
  differently ("Easy Lemon Orzo Salad" vs "Lemon Orzo Salad").
- **The same non-Instagram link with different query parameters**, such as
  `?utm_source=...`. If the extracted name matches, the name check catches it.
- **A genuinely different recipe with the same name.** It's treated as a
  duplicate and there is no override.

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
none, so they can't reach a plan, but they still show in the Library and the
Queue. To list them:

```sql
SELECT id, name, in_queue FROM recipes WHERE ingredients = '[]'::jsonb;
```

Delete them in Supabase, or paste each one's link into the Queue to get a proper
recipe (the duplicate check compares names too, so delete the stub first).

---

## The submission log

Every link submitted is recorded in `recipe_submissions`, with a status of
`processing`, `added`, `duplicate` or `failed`, and an error detail when it
failed. A duplicate records the recipe it matched.

---

## Database setup

| Needs | For | Without it |
|---|---|---|
| `schema.sql` **6f** | **Remove** and **add to queue** (it grants `in_queue` to the web app) | Both show an error and nothing changes |
| `schema.sql` **6g** | The `source_key` and `name_key` columns and `find_duplicate_recipe()` | No duplicate check at all; a warning is logged |

If the unique index in 6g fails to create, two recipes already share a link. The
query to find them is in a comment just above that statement. Delete or
re-link one of each pair, then run the statement again.

---

## Where this lives in the code

| What | Where |
|---|---|
| Key definitions and the lookup function | `scripts/schema.sql`, section 6g |
| Duplicate check and responses | `agents/ingest.py`, `find_duplicate` and `duplicate_response` |
| Link flow | `agents/ingest.py`, `ingest` |
| Queue tab, remove and add to queue | `frontend/src/App.jsx`, `setQueuedState` and the Queue and Library tabs |
| Skipping recipes with no ingredients | `agents/saturday_suggest.py`, `fetch_recipes` |
| Queue permission | `scripts/schema.sql`, section 6f |
