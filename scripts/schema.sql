-- =============================================================================
-- Mise en Place — Meal Planner Agent Schema
-- Run in Supabase SQL Editor in order.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ---------------------------------------------------------------------------
-- 1. recipes
--    Master library of all known recipes
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS recipes (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    name            TEXT        NOT NULL,
    description     TEXT,
    source_url      TEXT,                       -- instagram link, blog URL, etc.
    source_type     TEXT        DEFAULT 'manual', -- 'instagram','url','manual','sms'
    meal_type       TEXT        NOT NULL,        -- 'lunch' | 'dinner'
    ingredients     JSONB       NOT NULL DEFAULT '[]',
    -- [{"name": "chicken thighs", "amount": "2", "unit": "lbs", "category": "protein"}]
    instructions    TEXT,
    prep_time_mins  INTEGER,
    cook_time_mins  INTEGER,
    servings        INTEGER     DEFAULT 4,
    tags            TEXT[]      DEFAULT '{}',    -- ['make-ahead','vegetarian','quick']
    image_url       TEXT,
    times_suggested INTEGER     DEFAULT 0,
    times_made      INTEGER     DEFAULT 0,
    rating          NUMERIC(3,1),               -- 1-5 after being made
    in_queue        BOOLEAN     DEFAULT FALSE,   -- submitted via SMS/Instagram
    queue_priority  INTEGER     DEFAULT 0,       -- higher = suggested sooner
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS recipes_meal_type_idx ON recipes (meal_type);
CREATE INDEX IF NOT EXISTS recipes_in_queue_idx  ON recipes (in_queue, queue_priority DESC);
CREATE INDEX IF NOT EXISTS recipes_tags_gin_idx  ON recipes USING GIN (tags);

ALTER TABLE recipes ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Public read recipes"        ON recipes FOR SELECT USING (true);
CREATE POLICY "Service role all recipes"   ON recipes FOR ALL USING (auth.role() = 'service_role');


-- ---------------------------------------------------------------------------
-- 2. weekly_plans
--    One row per week; stores the 5 lunches + 3 dinners
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS weekly_plans (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    week_start      DATE        NOT NULL UNIQUE,  -- Monday of that week
    lunches         UUID[]      NOT NULL DEFAULT '{}',   -- 5 recipe IDs
    dinners         UUID[]      NOT NULL DEFAULT '{}',   -- 3 recipe IDs
    status          TEXT        DEFAULT 'suggested',     -- 'suggested'|'confirmed'|'shopping_done'
    confirmed_at    TIMESTAMPTZ,
    shopping_list   JSONB       DEFAULT '[]',
    -- [{"ingredient":"chicken thighs","amount":"4 lbs","category":"protein","checked":false}]
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS weekly_plans_week_start_idx ON weekly_plans (week_start DESC);

ALTER TABLE weekly_plans ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Public read plans"          ON weekly_plans FOR SELECT USING (true);
-- The anon key ships in the public web bundle, so anyone who loads the site can
-- run this UPDATE. Scope it to the current/future week (WITH CHECK blocks moving
-- a row into the past) so a stranger can't rewrite historical plans. Inserts and
-- deletes stay service-role-only. This is light hardening, not real auth — for a
-- private 2-person app the remaining surface (editing this week's plan) is
-- acceptable and recoverable, since the agents regenerate plans weekly.
DROP POLICY IF EXISTS "Public update plans" ON weekly_plans;
CREATE POLICY "Public update current plans" ON weekly_plans
    FOR UPDATE
    USING      (week_start >= date_trunc('week', CURRENT_DATE)::date)
    WITH CHECK (week_start >= date_trunc('week', CURRENT_DATE)::date);
CREATE POLICY "Service role all plans"     ON weekly_plans FOR ALL USING (auth.role() = 'service_role');


-- ---------------------------------------------------------------------------
-- 3. recipe_submissions
--    Inbound submissions via SMS (Instagram links or text descriptions)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS recipe_submissions (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    submitted_by    TEXT        NOT NULL,       -- phone number (E.164)
    raw_message     TEXT        NOT NULL,       -- original SMS text
    parsed_url      TEXT,                       -- extracted Instagram/URL
    status          TEXT        DEFAULT 'pending',  -- 'pending'|'processing'|'needs_decision'|'added'|'duplicate'|'failed'
    error_detail    TEXT,                       -- populated on failure: fetch/parse/extraction error
    recipe_id       UUID        REFERENCES recipes(id),
    submitted_at    TIMESTAMPTZ DEFAULT NOW(),
    processed_at    TIMESTAMPTZ
);

ALTER TABLE recipe_submissions ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Service role all submissions" ON recipe_submissions FOR ALL USING (auth.role() = 'service_role');


-- ---------------------------------------------------------------------------
-- 4. pipeline_runs  (audit log)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS meal_pipeline_runs (
    id          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    ran_at      TIMESTAMPTZ DEFAULT NOW(),
    job         TEXT        NOT NULL,   -- 'saturday_suggest'|'sunday_shopping'
    status      TEXT        NOT NULL,
    details     JSONB       DEFAULT '{}',
    error       TEXT
);

-- This was the one table in the file with RLS left off, which means anyone
-- holding the anon key (it ships in the public web bundle) could read the run
-- log — plan ids and raw error text. The frontend never reads this table and
-- the agents write with the service_role key, which bypasses RLS, so locking
-- it down costs nothing.
ALTER TABLE meal_pipeline_runs ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Service role all runs" ON meal_pipeline_runs FOR ALL USING (auth.role() = 'service_role');


-- ---------------------------------------------------------------------------
-- 5. Useful views
-- ---------------------------------------------------------------------------

-- This week's plan with full recipe details
CREATE OR REPLACE VIEW current_week_plan AS
WITH this_week AS (
    SELECT * FROM weekly_plans
    WHERE week_start = date_trunc('week', CURRENT_DATE)::date
    LIMIT 1
)
SELECT
    wp.id,
    wp.week_start,
    wp.status,
    wp.shopping_list,
    wp.notes,
    -- Expand lunch recipe IDs to names
    (
        SELECT json_agg(r.*)
        FROM recipes r
        WHERE r.id = ANY(wp.lunches)
    ) AS lunch_recipes,
    (
        SELECT json_agg(r.*)
        FROM recipes r
        WHERE r.id = ANY(wp.dinners)
    ) AS dinner_recipes
FROM this_week wp;


-- Queue view: recipes waiting to be suggested, priority order
CREATE OR REPLACE VIEW recipe_queue AS
SELECT id, name, meal_type, description, source_url, queue_priority, created_at
FROM recipes
WHERE in_queue = TRUE
ORDER BY queue_priority DESC, created_at ASC;


-- ---------------------------------------------------------------------------
-- 6. Auto-update updated_at on recipes and plans
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER trg_recipes_updated_at
    BEFORE UPDATE ON recipes
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE OR REPLACE TRIGGER trg_plans_updated_at
    BEFORE UPDATE ON weekly_plans
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ---------------------------------------------------------------------------
-- 6b. increment_times_suggested
--     Called by saturday_suggest via sb.rpc() for each chosen recipe.
--     Done in SQL so the read-modify-write is atomic in one round trip.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION increment_times_suggested(recipe_id UUID)
RETURNS void AS $$
    UPDATE recipes
    SET times_suggested = COALESCE(times_suggested, 0) + 1
    WHERE id = recipe_id;
$$ LANGUAGE sql;


-- ---------------------------------------------------------------------------
-- 6c. Normalise tags on write
--
--     tags[] is a free-text array, and most of it is written by Claude during
--     ingest — so "Mediterranean" and "mediterranean" both showed up, which
--     rendered as two chips in the web app AND, more seriously, meant
--     contains(tags, ['vegetarian']) in saturday_suggest would miss a recipe
--     tagged "Vegetarian". Normalising here rather than in each caller means
--     the guarantee holds no matter who writes the row.
--
--     Trims, lower-cases, drops blanks, de-duplicates. Note array_agg(DISTINCT)
--     returns tags alphabetically, so tag order becomes consistent rather than
--     insertion-ordered.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION normalize_tags()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.tags IS NULL THEN
        NEW.tags := '{}';
    ELSE
        NEW.tags := COALESCE((
            SELECT array_agg(DISTINCT lower(btrim(t)))
            FROM unnest(NEW.tags) AS t
            WHERE btrim(t) <> ''
        ), '{}');
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER trg_recipes_normalize_tags
    BEFORE INSERT OR UPDATE OF tags ON recipes
    FOR EACH ROW EXECUTE FUNCTION normalize_tags();

-- One-time backfill for rows written before the trigger existed.
UPDATE recipes
SET tags = COALESCE((
        SELECT array_agg(DISTINCT lower(btrim(t)))
        FROM unnest(tags) AS t
        WHERE btrim(t) <> ''
    ), '{}')
WHERE EXISTS (
    SELECT 1 FROM unnest(tags) AS t WHERE t <> lower(btrim(t))
);


-- ---------------------------------------------------------------------------
-- 6d. Weekly defaults + per-plan settings
--
--     meal_settings holds the household defaults (one row, id = 1): lunches
--     and dinners per week, and how many people eat them. The week needs
--     (lunches + dinners) * people servings. The Saturday cron and the web
--     app's Generate dialog start from these.
--
--     weekly_plans.settings records the values a plan was generated with, so
--     a one-off change in the Generate dialog stays with that week and
--     regenerating it reopens on the same numbers. Re-runnable.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS meal_settings (
    id          INTEGER     PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    lunches     INTEGER     NOT NULL DEFAULT 5 CHECK (lunches BETWEEN 0 AND 14),
    dinners     INTEGER     NOT NULL DEFAULT 3 CHECK (dinners BETWEEN 0 AND 14),
    people      INTEGER     NOT NULL DEFAULT 2 CHECK (people  BETWEEN 1 AND 8),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO meal_settings (id) VALUES (1) ON CONFLICT DO NOTHING;

ALTER TABLE meal_settings ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Public read settings"   ON meal_settings;
DROP POLICY IF EXISTS "Public update settings" ON meal_settings;
DROP POLICY IF EXISTS "Service role all settings" ON meal_settings;
CREATE POLICY "Public read settings"   ON meal_settings FOR SELECT USING (true);
-- Same trade-off as weekly_plans: the anon key is public, and for a private
-- 2-person app the worst a stranger can do is change three small numbers.
CREATE POLICY "Public update settings" ON meal_settings FOR UPDATE USING (true) WITH CHECK (true);
CREATE POLICY "Service role all settings" ON meal_settings FOR ALL USING (auth.role() = 'service_role');

CREATE OR REPLACE TRIGGER trg_settings_updated_at
    BEFORE UPDATE ON meal_settings
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE weekly_plans ADD COLUMN IF NOT EXISTS settings JSONB;


-- ---------------------------------------------------------------------------
-- 6e. Vegetarian toggles
--     Limit lunches and/or dinners to recipes tagged 'vegetarian'. Dinners
--     default on and lunches off, which is what the agent did before these
--     existed. Re-runnable.
-- ---------------------------------------------------------------------------
ALTER TABLE meal_settings
    ADD COLUMN IF NOT EXISTS vegetarian_lunches BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS vegetarian_dinners BOOLEAN NOT NULL DEFAULT true;


-- ---------------------------------------------------------------------------
-- 6f. Let the web app edit a recipe's meal type, tags and servings, and take
--     it out of the queue (in_queue)
--     The anon key is public, so keep this narrow: only those four columns
--     are updatable (column grant), and the policy rejects values the planner
--     can't use. Everything else on a recipe stays service-role-only.
--     Re-runnable.
-- ---------------------------------------------------------------------------
REVOKE UPDATE ON recipes FROM anon;
GRANT UPDATE (meal_type, tags, servings, in_queue) ON recipes TO anon;

DROP POLICY IF EXISTS "Public edit recipe basics" ON recipes;
CREATE POLICY "Public edit recipe basics" ON recipes
    FOR UPDATE
    USING (true)
    WITH CHECK (meal_type IN ('lunch', 'dinner')
                AND servings BETWEEN 1 AND 25
                AND cardinality(tags) <= 10);


-- ---------------------------------------------------------------------------
-- 6g. Duplicate detection
--
--     Two keys, derived by the database from columns that already exist, so
--     every row (seeds, ingest, rows from before this) has them and there is
--     one definition of "the same":
--
--       source_key  identifies the LINK. Instagram posts, reels and IGTV are
--                   keyed on their shortcode ("instagram:Cabc123"), so
--                   ?igsh=... tracking and username prefixes don't matter.
--                   Any other link is lower-cased, minus scheme, "www.",
--                   #fragment and trailing "/". The query string is kept,
--                   because sites like YouTube identify the page by it.
--                   Unique: one row per link.
--       name_key    identifies the RECIPE NAME: lower-cased, punctuation and
--                   spacing collapsed ("Lemon  Orzo Salad!" = "lemon orzo
--                   salad"). Letters outside a-z (accents, other scripts) are
--                   kept as they are, so "Pâté" and "Pt" stay different. Not
--                   unique, since two recipes can share a name. When a NEW
--                   link has the same name as an existing recipe, the web app
--                   asks what to do (replace, keep both, or use the old one).
--
--     find_duplicate_recipe() is what the ingest endpoint calls. Recipes with
--     no ingredients never match by name: they can't be planned, so they
--     shouldn't block a real recipe from being added.
--
--     Re-runnable, and safe to run again after the key rules change: the two
--     generated columns are dropped and re-added so every row is recomputed.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION recipe_name_key(p_name TEXT) RETURNS TEXT AS $$
    -- NULLIF: a name with no letters or digits at all gets no key rather than a
    -- shared empty one that would match every other such name.
    SELECT NULLIF(btrim(regexp_replace(lower(p_name), '[^a-z0-9\u0080-￿]+', ' ', 'g')), '');
$$ LANGUAGE sql IMMUTABLE;

CREATE OR REPLACE FUNCTION recipe_source_key(p_url TEXT) RETURNS TEXT AS $$
    -- The Instagram pattern is anchored to the host, so a URL that merely
    -- contains "instagram.com/p/..." somewhere (a redirector, another site)
    -- is not mistaken for a post.
    SELECT CASE
        WHEN p_url IS NULL OR btrim(p_url) = '' THEN NULL
        WHEN btrim(p_url) ~* '^(https?://)?([a-z0-9-]+\.)*instagram\.com/([A-Za-z0-9_.]+/)?(p|reel|reels|tv)/[A-Za-z0-9_-]+'
            THEN 'instagram:' || (regexp_match(
                btrim(p_url),
                '^(?:https?://)?(?:[a-z0-9-]+\.)*instagram\.com/(?:[A-Za-z0-9_.]+/)?(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)',
                'i'))[1]
        ELSE regexp_replace(lower(split_part(btrim(p_url), '#', 1)),
                            '^https?://(www\.)?|/+$', '', 'g')
    END;
$$ LANGUAGE sql IMMUTABLE;

-- Drop first so a changed rule recomputes the stored values (and the index on
-- source_key, created below, goes with its column).
ALTER TABLE recipes
    DROP COLUMN IF EXISTS source_key,
    DROP COLUMN IF EXISTS name_key;
ALTER TABLE recipes
    ADD COLUMN source_key TEXT GENERATED ALWAYS AS (recipe_source_key(source_url)) STORED,
    ADD COLUMN name_key   TEXT GENERATED ALWAYS AS (recipe_name_key(name)) STORED;

CREATE INDEX IF NOT EXISTS recipes_name_key_idx ON recipes (name_key);

-- Best match for a submission: the same link beats the same name. Created
-- before the unique index below, so that if the index fails (existing
-- duplicate links) the lookup still exists. Dropped first because the columns
-- it returns changed (source_url was added), which CREATE OR REPLACE refuses.
DROP FUNCTION IF EXISTS find_duplicate_recipe(TEXT, TEXT);
CREATE FUNCTION find_duplicate_recipe(p_url TEXT, p_name TEXT DEFAULT NULL)
RETURNS TABLE (id UUID, name TEXT, in_queue BOOLEAN, source_url TEXT, matched_on TEXT) AS $$
    SELECT m.id, m.name, m.in_queue, m.source_url, m.matched_on FROM (
        SELECT r.id, r.name, r.in_queue, r.source_url, 'url'::text AS matched_on
          FROM recipes r
         WHERE p_url IS NOT NULL AND r.source_key = recipe_source_key(p_url)
        UNION ALL
        SELECT r.id, r.name, r.in_queue, r.source_url, 'name'::text
          FROM recipes r
         WHERE p_name IS NOT NULL AND r.name_key = recipe_name_key(p_name)
           AND r.ingredients <> '[]'::jsonb
    ) m
    ORDER BY (m.matched_on = 'url') DESC
    LIMIT 1;
$$ LANGUAGE sql STABLE;

-- If this fails, two recipes already share a link. Find them with:
--   SELECT source_key, array_agg(name) FROM recipes
--   WHERE source_key IS NOT NULL GROUP BY 1 HAVING count(*) > 1;
-- then delete or re-link one of each pair and run this statement again.
CREATE UNIQUE INDEX IF NOT EXISTS recipes_source_key_uniq
    ON recipes (source_key) WHERE source_key IS NOT NULL;


-- ---------------------------------------------------------------------------
-- 6h. Park a parsed recipe while the user decides what to do about a name clash
--     When a NEW link parses to the same name as an existing recipe, the endpoint
--     stores the parsed recipe here (status 'needs_decision') instead of
--     re-fetching and re-parsing the link once the user has chosen. Re-runnable.
-- ---------------------------------------------------------------------------
ALTER TABLE recipe_submissions ADD COLUMN IF NOT EXISTS parsed_recipe JSONB;


-- ---------------------------------------------------------------------------
-- 7. Seed a few starter recipes so the app isn't empty
--
--    NOTE: the ON CONFLICT DO NOTHING below is a no-op — there is no unique
--    constraint on recipes.name — so re-running THIS block duplicates all six
--    starter recipes. Section 7b is written to be safely re-runnable instead.
-- ---------------------------------------------------------------------------
INSERT INTO recipes (name, meal_type, description, tags, prep_time_mins, cook_time_mins, servings, ingredients) VALUES
(
    'Greek Chicken Meal Prep Bowls',
    'lunch',
    'Lemony marinated chicken thighs over rice with cucumber, tomato, and tzatziki. Holds great in the fridge for 5 days.',
    ARRAY['make-ahead','high-protein','mediterranean'],
    20, 30, 5,
    '[{"name":"chicken thighs","amount":"2","unit":"lbs","category":"protein"},
      {"name":"jasmine rice","amount":"2","unit":"cups","category":"grain"},
      {"name":"cucumber","amount":"1","unit":"large","category":"produce"},
      {"name":"cherry tomatoes","amount":"1","unit":"pint","category":"produce"},
      {"name":"tzatziki","amount":"1","unit":"cup","category":"dairy"},
      {"name":"lemon","amount":"2","unit":"","category":"produce"},
      {"name":"olive oil","amount":"3","unit":"tbsp","category":"pantry"},
      {"name":"garlic","amount":"4","unit":"cloves","category":"produce"}]'::jsonb
),
(
    'Spicy Sesame Noodles',
    'lunch',
    'Cold sesame noodles with chili crisp, edamame, and shredded rotisserie chicken. No reheating needed.',
    ARRAY['make-ahead','cold','quick','asian'],
    15, 10, 5,
    '[{"name":"soba noodles","amount":"12","unit":"oz","category":"grain"},
      {"name":"rotisserie chicken","amount":"1","unit":"whole","category":"protein"},
      {"name":"edamame","amount":"1","unit":"cup","category":"produce"},
      {"name":"sesame oil","amount":"3","unit":"tbsp","category":"pantry"},
      {"name":"soy sauce","amount":"4","unit":"tbsp","category":"pantry"},
      {"name":"chili crisp","amount":"2","unit":"tbsp","category":"pantry"},
      {"name":"scallions","amount":"4","unit":"","category":"produce"},
      {"name":"sesame seeds","amount":"2","unit":"tbsp","category":"pantry"}]'::jsonb
),
(
    'White Bean & Kale Soup',
    'lunch',
    'Hearty Tuscan-style soup that thickens beautifully overnight. Eat warm or cold.',
    ARRAY['make-ahead','vegetarian','healthy','soup'],
    15, 35, 6,
    '[{"name":"cannellini beans","amount":"2","unit":"cans","category":"pantry"},
      {"name":"kale","amount":"1","unit":"bunch","category":"produce"},
      {"name":"diced tomatoes","amount":"1","unit":"can","category":"pantry"},
      {"name":"vegetable broth","amount":"4","unit":"cups","category":"pantry"},
      {"name":"parmesan rind","amount":"1","unit":"","category":"dairy"},
      {"name":"onion","amount":"1","unit":"large","category":"produce"},
      {"name":"garlic","amount":"5","unit":"cloves","category":"produce"},
      {"name":"olive oil","amount":"3","unit":"tbsp","category":"pantry"}]'::jsonb
),
(
    'Smash Burgers',
    'dinner',
    'Crispy-edged beef patties with American cheese, pickles, and special sauce on a toasted brioche bun.',
    ARRAY['quick','crowd-pleaser','american'],
    10, 15, 4,
    '[{"name":"80/20 ground beef","amount":"1.5","unit":"lbs","category":"protein"},
      {"name":"brioche buns","amount":"4","unit":"","category":"bakery"},
      {"name":"American cheese","amount":"8","unit":"slices","category":"dairy"},
      {"name":"dill pickles","amount":"1","unit":"jar","category":"pantry"},
      {"name":"yellow onion","amount":"1","unit":"","category":"produce"},
      {"name":"mayo","amount":"4","unit":"tbsp","category":"condiment"},
      {"name":"ketchup","amount":"2","unit":"tbsp","category":"condiment"},
      {"name":"yellow mustard","amount":"1","unit":"tbsp","category":"condiment"}]'::jsonb
),
(
    'Sheet Pan Salmon with Roasted Vegetables',
    'dinner',
    'One-pan dinner — salmon fillets with asparagus and cherry tomatoes, lemon-dill butter.',
    ARRAY['healthy','quick','one-pan'],
    10, 25, 4,
    '[{"name":"salmon fillets","amount":"4","unit":"6oz","category":"protein"},
      {"name":"asparagus","amount":"1","unit":"bunch","category":"produce"},
      {"name":"cherry tomatoes","amount":"1","unit":"pint","category":"produce"},
      {"name":"butter","amount":"4","unit":"tbsp","category":"dairy"},
      {"name":"dill","amount":"1","unit":"bunch","category":"produce"},
      {"name":"lemon","amount":"2","unit":"","category":"produce"},
      {"name":"olive oil","amount":"2","unit":"tbsp","category":"pantry"},
      {"name":"garlic","amount":"3","unit":"cloves","category":"produce"}]'::jsonb
),
(
    'Birria Tacos',
    'dinner',
    'Slow-braised beef birria with consommé for dipping. Weekend showstopper.',
    ARRAY['weekend','mexican','slow-cook','crowd-pleaser'],
    30, 240, 6,
    '[{"name":"chuck roast","amount":"3","unit":"lbs","category":"protein"},
      {"name":"dried guajillo chiles","amount":"6","unit":"","category":"pantry"},
      {"name":"dried ancho chiles","amount":"3","unit":"","category":"pantry"},
      {"name":"corn tortillas","amount":"24","unit":"","category":"bakery"},
      {"name":"Oaxacan cheese","amount":"8","unit":"oz","category":"dairy"},
      {"name":"white onion","amount":"1","unit":"","category":"produce"},
      {"name":"cilantro","amount":"1","unit":"bunch","category":"produce"},
      {"name":"beef broth","amount":"2","unit":"cups","category":"pantry"},
      {"name":"chipotle in adobo","amount":"2","unit":"tbsp","category":"pantry"}]'::jsonb
)
ON CONFLICT DO NOTHING;


-- ---------------------------------------------------------------------------
-- 7b. Extra seeds so a fresh install can actually produce a plan
--
--     saturday_suggest needs >= 5 lunches and >= 3 dinners TAGGED 'vegetarian'.
--     Section 7 alone ships 3 lunches and 0 vegetarian dinners, so the agent
--     exits with "Not enough recipes" on a clean setup. These top it up to
--     6 lunches and 4 vegetarian dinners (a little slack, so the selector has
--     a real choice rather than exactly one valid answer).
--
--     Safe to re-run: each row is skipped if a recipe of that name exists.
-- ---------------------------------------------------------------------------
INSERT INTO recipes (name, meal_type, description, tags,
                     prep_time_mins, cook_time_mins, servings, ingredients)
SELECT v.name, v.meal_type, v.description, v.tags,
       v.prep_time_mins, v.cook_time_mins, v.servings, v.ingredients
FROM (VALUES
(
    'Curried Chickpea Salad Jars'::text,
    'lunch'::text,
    'Mashed chickpeas with curry powder, apple, and celery. Packs into jars and keeps all week.'::text,
    ARRAY['make-ahead','vegetarian','cold','no-cook']::text[],
    15::integer, 0::integer, 5::integer,
    '[{"name":"chickpeas","amount":"3","unit":"cans","category":"pantry"},
      {"name":"greek yogurt","amount":"1","unit":"cup","category":"dairy"},
      {"name":"celery","amount":"4","unit":"stalks","category":"produce"},
      {"name":"apple","amount":"1","unit":"","category":"produce"},
      {"name":"curry powder","amount":"2","unit":"tbsp","category":"pantry"},
      {"name":"red onion","amount":"1","unit":"small","category":"produce"},
      {"name":"raisins","amount":"0.5","unit":"cup","category":"pantry"},
      {"name":"lemon","amount":"1","unit":"","category":"produce"}]'::jsonb
),
(
    'Farro Bowls with Roasted Squash',
    'lunch',
    'Nutty farro with maple-roasted butternut squash, feta, and arugula. Good warm or cold.',
    ARRAY['make-ahead','vegetarian','healthy','grain-bowl']::text[],
    20, 35, 5,
    '[{"name":"farro","amount":"2","unit":"cups","category":"grain"},
      {"name":"butternut squash","amount":"1","unit":"large","category":"produce"},
      {"name":"feta","amount":"6","unit":"oz","category":"dairy"},
      {"name":"arugula","amount":"5","unit":"oz","category":"produce"},
      {"name":"pepitas","amount":"0.5","unit":"cup","category":"pantry"},
      {"name":"maple syrup","amount":"2","unit":"tbsp","category":"pantry"},
      {"name":"olive oil","amount":"4","unit":"tbsp","category":"pantry"},
      {"name":"apple cider vinegar","amount":"3","unit":"tbsp","category":"condiment"}]'::jsonb
),
(
    'Peanut Noodle Slaw',
    'lunch',
    'Cold rice noodles tossed with cabbage slaw and a punchy peanut-lime dressing. No reheating.',
    ARRAY['make-ahead','vegetarian','cold','asian']::text[],
    20, 8, 5,
    '[{"name":"rice noodles","amount":"12","unit":"oz","category":"grain"},
      {"name":"red cabbage","amount":"1","unit":"small head","category":"produce"},
      {"name":"carrots","amount":"3","unit":"","category":"produce"},
      {"name":"peanut butter","amount":"0.5","unit":"cup","category":"pantry"},
      {"name":"lime","amount":"3","unit":"","category":"produce"},
      {"name":"soy sauce","amount":"3","unit":"tbsp","category":"pantry"},
      {"name":"fresh ginger","amount":"2","unit":"inch","category":"produce"},
      {"name":"cilantro","amount":"1","unit":"bunch","category":"produce"},
      {"name":"roasted peanuts","amount":"0.5","unit":"cup","category":"pantry"}]'::jsonb
),
(
    'Black Bean & Sweet Potato Enchiladas',
    'dinner',
    'Rolled corn tortillas with black beans and roasted sweet potato under red chile sauce.',
    ARRAY['vegetarian','mexican','crowd-pleaser']::text[],
    25, 35, 4,
    '[{"name":"corn tortillas","amount":"12","unit":"","category":"bakery"},
      {"name":"black beans","amount":"2","unit":"cans","category":"pantry"},
      {"name":"sweet potatoes","amount":"2","unit":"large","category":"produce"},
      {"name":"red enchilada sauce","amount":"2","unit":"cups","category":"pantry"},
      {"name":"monterey jack","amount":"8","unit":"oz","category":"dairy"},
      {"name":"yellow onion","amount":"1","unit":"","category":"produce"},
      {"name":"cumin","amount":"2","unit":"tsp","category":"pantry"},
      {"name":"cilantro","amount":"1","unit":"bunch","category":"produce"}]'::jsonb
),
(
    'Paneer Tikka Masala',
    'dinner',
    'Charred paneer in a creamy spiced tomato gravy. Serve with basmati and naan.',
    ARRAY['vegetarian','indian','weeknight']::text[],
    20, 30, 4,
    '[{"name":"paneer","amount":"14","unit":"oz","category":"dairy"},
      {"name":"crushed tomatoes","amount":"1","unit":"can","category":"pantry"},
      {"name":"heavy cream","amount":"0.5","unit":"cup","category":"dairy"},
      {"name":"basmati rice","amount":"2","unit":"cups","category":"grain"},
      {"name":"naan","amount":"4","unit":"","category":"bakery"},
      {"name":"garam masala","amount":"2","unit":"tbsp","category":"pantry"},
      {"name":"fresh ginger","amount":"2","unit":"inch","category":"produce"},
      {"name":"garlic","amount":"5","unit":"cloves","category":"produce"},
      {"name":"yellow onion","amount":"1","unit":"large","category":"produce"}]'::jsonb
),
(
    'Mushroom Ragu with Polenta',
    'dinner',
    'Slow-cooked mixed mushroom ragu over creamy parmesan polenta. Deeply savoury.',
    ARRAY['vegetarian','italian','comfort']::text[],
    15, 40, 4,
    '[{"name":"cremini mushrooms","amount":"1.5","unit":"lbs","category":"produce"},
      {"name":"dried porcini","amount":"1","unit":"oz","category":"pantry"},
      {"name":"polenta","amount":"1.5","unit":"cups","category":"grain"},
      {"name":"parmesan","amount":"4","unit":"oz","category":"dairy"},
      {"name":"tomato paste","amount":"3","unit":"tbsp","category":"pantry"},
      {"name":"red wine","amount":"1","unit":"cup","category":"pantry"},
      {"name":"shallots","amount":"3","unit":"","category":"produce"},
      {"name":"thyme","amount":"1","unit":"bunch","category":"produce"},
      {"name":"butter","amount":"3","unit":"tbsp","category":"dairy"}]'::jsonb
),
(
    'Crispy Tofu Banh Mi Bowls',
    'dinner',
    'Cornstarch-crisped tofu over rice with quick-pickled carrot and daikon, herbs, and sriracha mayo.',
    ARRAY['vegetarian','vietnamese','quick']::text[],
    20, 20, 4,
    '[{"name":"extra-firm tofu","amount":"28","unit":"oz","category":"protein"},
      {"name":"jasmine rice","amount":"2","unit":"cups","category":"grain"},
      {"name":"daikon","amount":"1","unit":"small","category":"produce"},
      {"name":"carrots","amount":"3","unit":"","category":"produce"},
      {"name":"rice vinegar","amount":"0.5","unit":"cup","category":"condiment"},
      {"name":"mayo","amount":"0.5","unit":"cup","category":"condiment"},
      {"name":"sriracha","amount":"3","unit":"tbsp","category":"condiment"},
      {"name":"cornstarch","amount":"0.5","unit":"cup","category":"pantry"},
      {"name":"cilantro","amount":"1","unit":"bunch","category":"produce"},
      {"name":"cucumber","amount":"1","unit":"","category":"produce"}]'::jsonb
)
) AS v(name, meal_type, description, tags,
       prep_time_mins, cook_time_mins, servings, ingredients)
WHERE NOT EXISTS (SELECT 1 FROM recipes r WHERE r.name = v.name);
