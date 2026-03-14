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
CREATE POLICY "Public update plans"        ON weekly_plans FOR UPDATE USING (true);
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
    status          TEXT        DEFAULT 'pending',  -- 'pending'|'processing'|'added'|'failed'
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
    job         TEXT        NOT NULL,   -- 'saturday_suggest'|'sunday_shopping'|'sms_inbound'
    status      TEXT        NOT NULL,
    details     JSONB       DEFAULT '{}',
    error       TEXT
);


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
-- 7. Seed a few starter recipes so the app isn't empty
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
