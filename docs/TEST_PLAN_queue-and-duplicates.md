# Test plan: queue remove, duplicate detection, links only (PR #3)

Branch `feature/queue-remove-and-duplicates`. Pick this up cold: everything you
need is below. Delete this file after the PR merges if you don't want to keep it.

**What the branch changes**
- Queue takes **links only** (names refused; share text with a link inside works).
- **Remove** a queued recipe (off the queue only) and **add to queue** from Library.
- **Duplicate detection**: same link is never added twice; a new link whose recipe
  has the same name opens a **replace / keep both / use old** dialog.
- The planner **skips recipes with no ingredients**.
- iOS Shortcut / texting removed from UI and docs; privacy policy updated.
- Schema sections **6f, 6g, 6h**.

---

## 0. Before you start

**The local app writes to your live Supabase project.** Everything below adds,
edits or removes real rows. Note the start time so you can find and clean up what
you created:

```sql
SELECT now();   -- write this down: <START>
```

**Prerequisites**
- [ ] Branch checked out, `pip install -r requirements.txt`, `cd frontend && npm install`
- [ ] SQL applied in Supabase: **6f, 6g, 6h** (see `scripts/schema.sql`)
- [ ] Run the checks in **Appendix A**: every row matches, and the `PASS` test passes
- [ ] Backend: `uvicorn agents.ingest:app --reload` (http://localhost:8000)
- [ ] Frontend: `cd frontend && npm run dev` (http://localhost:5173), with
      `VITE_INGEST_URL=http://localhost:8000` in `frontend/.env`

Test the branch **locally**, not on a Vercel preview: a preview talks to the
Railway backend, which still runs `main` until the PR merges.

**Test data you need**
- `LINK_IG`: an Instagram post or reel with the recipe in its caption
- `LINK_B`: any recipe page that parses cleanly (a different site from `LINK_IG`)
- `LINK_B2` (nice to have): a *different* page for the same dish as `LINK_B`

**Useful queries while testing** (replace `<START>`):
```sql
SELECT id, name, in_queue, source_url, source_key, created_at
FROM recipes WHERE created_at > '<START>' ORDER BY created_at;

SELECT status, parsed_url, recipe_id, error_detail, submitted_at
FROM recipe_submissions WHERE submitted_at > '<START>' ORDER BY submitted_at;
```

---

## 1. Automated checks

- [ ] `python test_agents.py` prints `13 passed`
- [ ] `cd frontend && npx vite build` succeeds

These cover: link-before-fetch ordering, same-link duplicates, the name-clash
flow and all three answers, double-answering, name-only refusal, a failed save
being marked `failed` (or reported as a duplicate when it lost a race), the
vegetarian tag at ingest, and ingredient-less recipes skipped by the planner.
They do **not** cover the SQL or the UI, which is the rest of this plan.

---

## 2. Backend API (curl)

With the backend running:
```bash
cd /c/code/mise-en-place
export INGEST_TOKEN=$(grep '^INGEST_TOKEN=' .env | cut -d= -f2- | tr -d '\r')
H='Content-Type: application/json'
```

| # | Command | Expect |
|---|---|---|
| 2.1 | `curl -s -o /dev/null -w "%{http_code}" -X POST localhost:8000/ingest -H "$H" -d '{"url":"https://example.com"}'` | `401` (no token) |
| 2.2 | `curl -s -X POST localhost:8000/ingest -H "$H" -H "X-Ingest-Token: $INGEST_TOKEN" -d '{"name":"Lemon Orzo Salad"}'` | `400`, "Provide a recipe link as 'url'" |
| 2.3 | same, with `-d '{"url":"not a link"}'` | `400`, "Not a valid URL" |
| 2.4 | `curl -s -X POST localhost:8000/ingest/resolve -H "$H" -H "X-Ingest-Token: $INGEST_TOKEN" -d '{"submission_id":"00000000-0000-0000-0000-000000000099","action":"replace"}'` | `409`, "already made or has expired" |
| 2.5 | same with `"action":"nonsense"` | `422` |
| 2.6 | same with `"submission_id":"nope"` | `422` |
| 2.7 | `curl -s localhost:8000/health` | `{"status":"ok"}` |

- [ ] 2.1 to 2.7 as expected

---

## 3. Queue tab: adding and wording

| # | Steps | Expect |
|---|---|---|
| 3.1 | Open the Queue tab | No mention of texting or the iOS Shortcut. Placeholder "Paste a recipe link…". The intro mentions pasting a recipe link, not twice-adding, and **add to queue** on Library cards |
| 3.2 | With nothing queued (or in a clean state) | Empty state reads "paste a recipe link above to add one!" |
| 3.3 | Type `lemon orzo salad` and press Add | "Paste a recipe link (starting with http)." Nothing added, no request made (Network tab) |
| 3.4 | Paste `LINK_B`, Add | "Added …". The recipe appears in the queue **without reloading**. Check it in SQL: `in_queue = true`, `source_key` filled |
| 3.5 | Paste `LINK_IG`, Add | Added; a vegetarian recipe gets the `vegetarian` tag (open the edit dialog to see it) |
| 3.6 | Paste `Check this out https://…LINK_B2…` (text before the link) | Accepted as a link, not refused. (Use a new link, or expect the duplicate message from section 4) |
| 3.7 | Add a link to a page that isn't a recipe | A clear error ("Couldn't parse a recipe… Try a different link"), nothing added. `recipe_submissions.status = failed` with an `error_detail` |

- [ ] 3.1 to 3.7 as expected

---

## 4. Same link twice

| # | Steps | Expect |
|---|---|---|
| 4.1 | Paste `LINK_B` again (it's queued) | "ℹ️ … is already in your queue." No new row. Backend log shows **no fetch and no Claude call**. Submission `status = duplicate` |
| 4.2 | Paste `LINK_IG` with extra junk: `…/?igsh=abc123` appended | Same "already in your queue" |
| 4.3 | If your Instagram link has a username path (`instagram.com/<user>/reel/<code>/`) paste it as `instagram.com/reel/<code>` (or the reverse) | Same duplicate: the shortcode is the identity |
| 4.4 | Remove `LINK_B`'s recipe from the queue (section 5), then paste `LINK_B` | "ℹ️ … is already in your library. Queued it again." It's back in the queue. Still one row in SQL |
| 4.5 | Paste `LINK_B` with a changed fragment (`#foo`) or trailing `/` | Duplicate |
| 4.6 | Open two browser tabs, paste the same **new** link in both at once | One is added; the other reports a duplicate (not an error). SQL: one row |

- [ ] 4.1 to 4.6 as expected

---

## 5. Remove and add to queue

| # | Steps | Expect |
|---|---|---|
| 5.1 | On a queued card, click **remove** | The card leaves the Queue tab immediately. Queue count in the nav drops |
| 5.2 | Open the Library tab, find it | Still there. No **QUEUED** badge. It has an **add to queue** button |
| 5.3 | Click **add to queue** | The badge appears; the button disappears. It's back in the Queue tab |
| 5.4 | Reload the page | State persisted (SQL: `in_queue` matches) |
| 5.5 | Queued cards in the Library | Show **edit** but no **add to queue** |
| 5.6 | Failure path (optional): temporarily revoke the grant — `REVOKE UPDATE (in_queue) ON recipes FROM anon;` — then click remove | An alert explains it couldn't be removed, and the card **returns** to the queue. **Restore** with `GRANT UPDATE (in_queue) ON recipes TO anon;` and re-run 6f to be sure |

- [ ] 5.1 to 5.5 as expected (5.6 optional)

---

## 6. Same name, different link (the dialog)

**Make a clash on purpose.** You need a *new* link that parses to the same name as
an existing recipe:
1. Add `LINK_B` (note the recipe name it gets).
2. In SQL, point that row at a fake source so `LINK_B` no longer matches by link:
   ```sql
   UPDATE recipes SET source_url = 'https://example.com/test-old'
   WHERE source_url ILIKE '%<part of LINK_B>%';
   ```
3. Paste `LINK_B` again. Its link is "new", its name matches: the dialog opens.

**Do this on a recipe created for the test.** **Replace** overwrites the old row.

| # | Check | Expect |
|---|---|---|
| 6.1 | Dialog content | Title is the recipe name. Says it's already in your library, "(from example.com)", and "and in your queue" if it was queued. Shows the new link's label. Three choices |
| 6.2 | **Close without adding** | Dialog closes; nothing added, nothing queued. SQL: submission `status = needs_decision` |
| 6.3 | **Use the old recipe** | "Queued…" message. No new row; the old one is queued. Submission `status = duplicate` |
| 6.4 | Re-create the clash, then **Keep both** with the default label | New row named `Name (host)`, e.g. `Chicken Soup (allrecipes.com)`. Two rows. Both survive a reload |
| 6.5 | Re-create the clash, edit the label to `NY Times`, **Keep both** | New row named `Name (NY Times)`. The button text in the dialog previews the exact name before you click |
| 6.6 | Re-create the clash, then **Replace** | One row, **same id** as before (check SQL), with the new link's `source_url`, ingredients and times. Queued. `times_suggested` unchanged. Submission `status = added` |
| 6.7 | Replace a recipe that is in the current plan (set one up via Generate) | The plan still shows it (same id) |
| 6.8 | Answer the same question twice (curl `/ingest/resolve` again with the `submission_id` from `recipe_submissions`) | `409` the second time |
| 6.9 | Dialog buttons while a request is in flight | Disabled; no double submits |
| 6.10 | Backend stopped, then click a choice | "Network error — is the ingest service running?" inside the dialog; dialog stays open |

- [ ] 6.1 to 6.10 as expected

---

## 7. Recipes with no ingredients

Create a stub in SQL (it stands in for an old name-only recipe):
```sql
INSERT INTO recipes (name, meal_type, tags, ingredients, in_queue)
VALUES ('ZZ Test Stub', 'dinner', ARRAY['vegetarian'], '[]'::jsonb, true);
```

| # | Steps | Expect |
|---|---|---|
| 7.1 | Generate plans a few times (vegetarian dinners on) | `ZZ Test Stub` is **never** picked, despite being queued and tagged |
| 7.2 | Paste a link whose recipe name is `ZZ Test Stub` (create the situation by renaming a test recipe, or skip) | **No dialog**: an ingredient-less recipe never matches by name |
| 7.3 | Run the cleanup in `docs/QUEUE.md` ("Recipes added by name…") on this stub | Preview lists it; the delete removes it |

- [ ] 7.1 to 7.3 as expected

---

## 8. Regressions in nearby features

| # | Area | Steps | Expect |
|---|---|---|---|
| 8.1 | Generate | This Week → Generate → leave defaults → Generate plan | The pop-up runs, closes, and a plan appears with the servings summary line. Recipes with ingredients only |
| 8.2 | Vegetarian note | With a vegetarian switch on in Settings, open Generate | The note at the bottom names lunch/dinner; "Settings" opens `?tab=settings` in a new tab |
| 8.3 | Edit dialog | Edit a recipe from the Library and from the Queue | Meal type, servings (max 25), vegetarian switch, tags (max 10) save and show on the card |
| 8.4 | Swap | Swap a recipe in a plan | Replaces the right entry |
| 8.5 | Settings | Change a number, Save defaults, reload | Persisted |
| 8.6 | Shopping | Check items off | Persisted |

- [ ] 8.1 to 8.6 as expected

---

## 9. Layout and accessibility

- [ ] Queue and Library cards at phone width (~390px): **edit / remove / add to
      queue** buttons wrap sensibly, no horizontal scroll
- [ ] Conflict dialog at phone width: three choices stack readably, the label
      input fits, the dialog scrolls if tall
- [ ] Keyboard: Tab reaches every dialog button and the label input; the buttons
      have visible focus
- [ ] Screen-reader labels: card buttons read "Remove from queue: *name*",
      "Add to queue: *name*", "Edit *name*"

---

## 10. Failure and degrade paths (optional; touch live data)

Only if you want to confirm the fallbacks. Each can be undone.

| # | Scenario | Do | Expect | Undo |
|---|---|---|---|---|
| 10.1 | 6h missing | `ALTER TABLE recipe_submissions DROP COLUMN parsed_recipe;` then trigger a name clash | **No dialog**: reported as a plain duplicate; backend logs "can't park recipe… 6h" | `ALTER TABLE recipe_submissions ADD COLUMN parsed_recipe JSONB;` |
| 10.2 | Backend down | Stop uvicorn, paste a link | "Network error — is the ingest service running?" | Restart |
| 10.3 | Bad link | Paste a link that 404s | "Couldn't read that link. Try a different link."; submission `failed` | none |

- [ ] 10.x as expected (or skipped)

---

## 11. Docs and wording

- [ ] README: "Adding Recipes" is links only; no iOS Shortcut; the "Already set
      up?" paragraph lists 6d to 6h; the `docs/` links work
- [ ] `docs/QUEUE.md`: describes the dialog, the keys, the cleanup SQL; matches
      what you saw in sections 4 to 7
- [ ] `docs/TAGGING.md`: no stub flow
- [ ] `PRIVACY_POLICY`: submission section says links only; "Updated on October
      22, 2026" at the bottom

---

## 12. Clean up

Review before deleting: this removes every recipe created since `<START>`,
including any you want to keep.

```sql
SELECT id, name FROM recipes WHERE created_at > '<START>';

BEGIN;
UPDATE recipe_submissions SET recipe_id = NULL
WHERE recipe_id IN (SELECT id FROM recipes WHERE created_at > '<START>');
DELETE FROM recipes WHERE created_at > '<START>';
COMMIT;
```
Also remove `ZZ Test Stub` if section 7 left it, and restore anything you
changed in section 5.6 or 10.

---

## 13. Sign-off and merge

- [ ] Sections 1 to 9 pass; any failures are logged on the PR
- [ ] PR #3 reviewed; re-run `/code-review high` on the final diff if the code changed since the last run
- [ ] Merge PR #3 into `main`
- [ ] Deploy: **Railway** (backend, `railway up` if there's no auto-deploy) and **Vercel** (frontend). The SQL is already applied
- [ ] Production smoke test: add a new link, add it again (duplicate message), remove it, add it back from the Library, and generate one plan

**Rollback.** The SQL is additive and re-runnable, and the old columns and
behaviour are untouched, so reverting the PR is enough: the extra columns
(`source_key`, `name_key`, `parsed_recipe`) and the `in_queue` grant are harmless
to older code. Reverting the code does not remove them.

---

## Appendix A: verify the SQL (6g and 6h)

**Main check.** Every `got` should equal its `expected`:
```sql
SELECT 'key columns are generated' AS what,
       (SELECT count(*) FROM information_schema.columns
        WHERE table_name = 'recipes' AND column_name IN ('source_key','name_key')
          AND is_generated = 'ALWAYS')::text AS got,
       '2' AS expected
UNION ALL SELECT 'unique link index',
       (SELECT count(*) FROM pg_indexes WHERE indexname = 'recipes_source_key_uniq')::text, '1'
UNION ALL SELECT 'name index',
       (SELECT count(*) FROM pg_indexes WHERE indexname = 'recipes_name_key_idx')::text, '1'
UNION ALL SELECT 'parsed_recipe column (6h)',
       (SELECT count(*) FROM information_schema.columns
        WHERE table_name = 'recipe_submissions' AND column_name = 'parsed_recipe')::text, '1'
UNION ALL SELECT 'instagram link key',
       recipe_source_key('https://www.instagram.com/chef.name/reel/Cabc123/?igsh=x'), 'instagram:Cabc123'
UNION ALL SELECT 'other site not mistaken for instagram',
       recipe_source_key('https://notinstagram.com/p/abc'), 'notinstagram.com/p/abc'
UNION ALL SELECT 'plain link key',
       recipe_source_key('https://www.Example.com/Recipes/Soup/#top'), 'example.com/recipes/soup'
UNION ALL SELECT 'name key',
       recipe_name_key('Lemon  Orzo Salad!'), 'lemon orzo salad'
UNION ALL SELECT 'accented names stay different',
       (recipe_name_key('Pâté') <> recipe_name_key('Pt'))::text, 'true'
UNION ALL SELECT 'lookup by link',
       (SELECT matched_on FROM find_duplicate_recipe(
          '<a link already in your library>', NULL)), 'url'
UNION ALL SELECT 'lookup by name',
       (SELECT matched_on FROM find_duplicate_recipe(
          NULL, '<the name of a library recipe that has ingredients>')), 'name';
```
Fill in the two placeholders with a real recipe from your library.

**The unique index blocks a duplicate link.** It leaves no data behind and ends
in an error on purpose; the error starting with `PASS` is the success:
```sql
DO $$
BEGIN
  INSERT INTO recipes (name, meal_type, source_url)
  VALUES ('tmp a', 'lunch', 'https://example.com/dup-test');
  BEGIN
    INSERT INTO recipes (name, meal_type, source_url)
    VALUES ('tmp b', 'lunch', 'https://www.example.com/dup-test/');
    RAISE EXCEPTION 'FAIL: the duplicate link was accepted';
  EXCEPTION WHEN unique_violation THEN
    NULL;
  END;
  RAISE EXCEPTION 'PASS: duplicate rejected (this error undoes the test rows)';
END $$;
```
Afterwards `SELECT count(*) FROM recipes WHERE name LIKE 'tmp %';` returns 0.

**Anon permissions (6f).** The web app can update only the four allowed columns:
```sql
SELECT column_name FROM information_schema.column_privileges
WHERE table_name = 'recipes' AND grantee = 'anon' AND privilege_type = 'UPDATE'
ORDER BY 1;   -- expect: in_queue, meal_type, servings, tags
```
