"""
test_agents.py
--------------
Offline checks for the logic that broke in production. No framework, no
network, no DB — just asserts. Run it with:

    python test_agents.py

Each block corresponds to a bug that shipped, so a regression fails loudly
here instead of silently on a Saturday morning.
"""

import os
from datetime import date, timedelta
from types import SimpleNamespace

from agents.claude import parse_json_response, response_text
from agents.saturday_suggest import (DEFAULT_SETTINGS, build_prompt, fetch_recipes,
                                    pick_fallback, plan_monday, servings,
                                    targets, validate_selection)


def block(text=None, thinking=None):
    """Fake a Messages API content block."""
    if thinking is not None:
        return SimpleNamespace(type="thinking", thinking=thinking)
    return SimpleNamespace(type="text", text=text)


def reply(*blocks, stop_reason="end_turn"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason)


def test_week_alignment():
    """
    The Saturday and Sunday agents must file/find a plan under the SAME Monday.
    They used to disagree by a week: Saturday wrote next Monday, Sunday looked
    up the current Monday, so Sunday shopped for the previous week or nothing.
    """
    for saturday in [date(2026, 8, 22), date(2026, 8, 29), date(2026, 1, 3),
                     date(2026, 12, 26)]:
        assert saturday.weekday() == 5, saturday
        sunday = saturday + timedelta(days=1)
        assert plan_monday(saturday) == plan_monday(sunday), saturday
        # and it really is the upcoming Monday, not some other day
        assert plan_monday(saturday).weekday() == 0
        assert plan_monday(saturday) > saturday

    # Mon-Fri: this week's Monday, so a manual Generate shows on This Week.
    for offset in range(5):
        d = date(2026, 8, 17) + timedelta(days=offset)
        assert plan_monday(d) == date(2026, 8, 17), d


def test_response_text_skips_thinking():
    """
    Reading content[0].text crashed with AttributeError as soon as a model
    emitted a thinking block first.
    """
    r = reply(block(thinking="hmm, five lunches..."), block(text='{"ok": 1}'))
    assert response_text(r) == '{"ok": 1}'
    assert parse_json_response(r, 2000) == {"ok": 1}

    # multiple text blocks are joined
    r = reply(block(text='{"a":'), block(text=' 2}'))
    assert parse_json_response(r, 2000) == {"a": 2}


def test_fenced_json_is_tolerated():
    for raw in ['```json\n{"a": 1}\n```', '```\n{"a": 1}\n```', '{"a": 1}']:
        assert parse_json_response(reply(block(text=raw)), 2000) == {"a": 1}


def test_truncation_is_named():
    """A max_tokens cutoff should say so, not surface a JSONDecodeError."""
    r = reply(block(text='{"lunch_ids": ["a", "b"'), stop_reason="max_tokens")
    try:
        parse_json_response(r, 500)
    except ValueError as exc:
        assert "500" in str(exc), exc
    else:
        raise AssertionError("expected ValueError on truncated output")


def test_validate_selection():
    """
    Claude's ids are untrusted: an invented id would be written into
    weekly_plans.lunches and match nothing downstream. Beyond that, each list
    must cover its servings target with no spare recipe. Recipes with no
    serving size count as 4.
    """
    lunches = {f"L{i}": {"id": f"L{i}", "servings": 4} for i in range(6)}
    lunches["L5"] = {"id": "L5"}                  # no serving size -> 4
    lunches["Lbig"] = {"id": "Lbig", "servings": 8}
    dinners = {f"D{i}": {"id": f"D{i}", "servings": 4} for i in range(4)}
    # 5 lunches + 3 dinners, 2 people: 10 lunch servings, 6 dinner servings
    lunch_target, dinner_target = 10, 6
    good = {"lunch_ids": ["L0", "L1", "L2"], "dinner_ids": ["D0", "D1"]}

    def check(case, lt=lunch_target, dt=dinner_target):
        validate_selection(case, lunches, dinners, lt, dt)

    check(good)                                                  # 12 and 8
    check({**good, "lunch_ids": ["L5", "L0", "L1"]})             # default of 4
    check({**good, "lunch_ids": ["Lbig", "L0"]})                 # 12 from two
    check({"lunch_ids": [], "dinner_ids": []}, 0, 0)             # nothing to eat
    check({"lunch_ids": ["L0"]}, 4, 0)                           # key missing, target 0

    bad_cases = [
        {**good, "lunch_ids": ["L0", "L1"]},                     # 8 < 10: short
        {**good, "lunch_ids": ["L0", "L1", "L2", "L3"]},         # 16: L3 is spare
        {**good, "lunch_ids": ["L0", "L0", "L1"]},               # duplicate
        {**good, "lunch_ids": ["L0", "L1", "nope"]},             # invented
        {**good, "dinner_ids": ["D0", "L0"]},                    # wrong pool
        {**good, "dinner_ids": []},                              # empty, target 6
        {"lunch_ids": good["lunch_ids"]},                        # key missing
    ]
    for case in bad_cases:
        try:
            check(case)
        except RuntimeError:
            pass
        else:
            raise AssertionError(f"expected rejection: {case}")


def test_vegetarian_toggles():
    """Each toggle filters its own meal type, and the prompt says so."""
    rows = []   # what every query "returns"

    class Query:
        def __init__(self, filtered):
            self.filtered, self.meal = filtered, None
        def select(self, *a): return self
        def eq(self, col, val):
            if col == "meal_type":
                self.meal = val
            return self
        def contains(self, col, vals):
            assert (col, vals) == ("tags", ["vegetarian"])
            self.filtered.append(self.meal)
            return self
        def order(self, *a, **k): return self
        def execute(self): return SimpleNamespace(data=rows)

    def filtered_for(**flags):
        got = []
        sb = SimpleNamespace(table=lambda name: Query(got))
        fetch_recipes(sb, {**DEFAULT_SETTINGS, **flags})
        return got

    assert filtered_for() == ["dinner"]                     # defaults: as before
    assert filtered_for(vegetarian_lunches=True) == ["lunch", "dinner"]
    assert filtered_for(vegetarian_dinners=False) == []
    assert filtered_for(vegetarian_lunches=True, vegetarian_dinners=False) == ["lunch"]

    # A recipe with no ingredients (a leftover name-only stub) is never planned.
    rows[:] = [{"id": "stub", "ingredients": []}, {"id": "real", "ingredients": [{"name": "rice"}]}]
    got = fetch_recipes(SimpleNamespace(table=lambda n: Query([])), DEFAULT_SETTINGS)
    assert [r["id"] for r in got["lunches"]] == ["real"], got
    assert [r["id"] for r in got["dinners"]] == ["real"], got

    on, off = DEFAULT_SETTINGS, {**DEFAULT_SETTINGS, "vegetarian_dinners": False}
    assert "vegetarian" in build_prompt(10, 6, on)
    assert "vegetarian" not in build_prompt(10, 6, off)


def test_ingest_tags_vegetarian_explicitly():
    """The planner filters on the 'vegetarian' tag, so ingest adds it from
    Claude's explicit answer instead of hoping it shows up in tags."""
    from agents.ingest import save_recipe_to_queue

    def saved_tags(**recipe):
        rows = []
        sb = SimpleNamespace(table=lambda n: SimpleNamespace(
            insert=lambda row: (rows.append(row), SimpleNamespace(
                execute=lambda: SimpleNamespace(data=[{"id": "x"}])))[1]))
        save_recipe_to_queue(sb, {"name": "n", "meal_type": "lunch", **recipe}, "u", "url")
        return rows[0]["tags"]

    assert saved_tags(vegetarian=True, tags=["quick"]) == ["quick", "vegetarian"]
    assert saved_tags(vegetarian=True, tags=["vegetarian"]) == ["vegetarian"]
    assert saved_tags(vegetarian=False, tags=["quick"]) == ["quick"]
    assert saved_tags(tags=["quick"]) == ["quick"]           # not answered: no guess
    assert saved_tags(vegetarian=True) == ["vegetarian"]     # no tags key at all


class FakeSB:
    """Just enough of the Supabase client for /ingest's duplicate handling."""
    def __init__(self, dup=None, rpc_error=False):
        self.dup, self.rpc_error = dup, rpc_error
        self.updates, self.rpc_calls = [], []

    def rpc(self, fn, args):
        self.rpc_calls.append((fn, args))
        if self.rpc_error:
            raise RuntimeError("function find_duplicate_recipe does not exist")
        return SimpleNamespace(execute=lambda: SimpleNamespace(
            data=[self.dup] if self.dup else []))

    def table(self, name):
        sb = self

        class Table:
            def insert(self, row): return self
            def update(self, vals): sb.updates.append((name, vals)); return self
            def eq(self, *a): return self
            def execute(self): return SimpleNamespace(data=[{"id": "sub1"}])
        return Table()


def test_duplicate_handling():
    """
    A submission that matches a library recipe is not added again. Already
    queued: say so. In the library only: queue it. The link check runs before
    anything is fetched, so a repeat link costs no Claude call.
    """
    import agents.ingest as ing
    from fastapi.testclient import TestClient

    queued = {"id": "r1", "name": "Lemon Orzo Salad", "in_queue": True}
    shelved = {**queued, "in_queue": False}

    assert ing.find_duplicate(FakeSB(dup=queued), url="u") == queued
    assert ing.find_duplicate(FakeSB(), url="u") is None
    assert ing.find_duplicate(FakeSB(rpc_error=True), url="u") is None  # 6g not applied

    sb = FakeSB()
    assert "already in your queue" in ing.duplicate_response(sb, queued)["message"]
    assert sb.updates == []
    resp = ing.duplicate_response(sb, shelved)
    assert "Queued it again" in resp["message"] and resp["duplicate"] is True
    assert sb.updates == [("recipes", {"in_queue": True, "queue_priority": 10})]

    os.environ["INGEST_TOKEN"] = "t"
    client, headers = TestClient(ing.app), {"X-Ingest-Token": "t"}

    def no_fetch(url):
        raise AssertionError("fetched a link that is already in the library")
    real_get_sb, real_fetch = ing.get_sb, ing.fetch_instagram_content
    ing.fetch_instagram_content = no_fetch
    try:
        sb = FakeSB(dup=queued)
        ing.get_sb = lambda: sb
        r = client.post("/ingest", headers=headers,
                        json={"url": "https://www.instagram.com/p/Cabc123/?igsh=x"})
        assert r.status_code == 200 and r.json()["duplicate"] is True, r.text
        assert sb.rpc_calls[0] == ("find_duplicate_recipe",
                                   {"p_url": "https://www.instagram.com/p/Cabc123/?igsh=x",
                                    "p_name": None})
        assert ("recipe_submissions", {"status": "duplicate", "recipe_id": "r1",
                                       "processed_at": sb.updates[0][1]["processed_at"]}) == sb.updates[0]

        # links only: a name-only submission is refused, not turned into a stub
        sb = FakeSB()
        ing.get_sb = lambda: sb
        r = client.post("/ingest", headers=headers, json={"name": "Lemon Orzo Salad"})
        assert r.status_code == 400, r.text
        assert sb.updates == [] and sb.rpc_calls == []
    finally:
        ing.get_sb, ing.fetch_instagram_content = real_get_sb, real_fetch


def test_servings_targets_and_fallback():
    """The formula is (lunches + dinners) * people, covered per meal type."""
    s = {"lunches": 5, "dinners": 3, "people": 2}
    assert targets(s) == (10, 6)
    assert sum(targets(s)) == (5 + 3) * 2
    assert servings({}) == 4 and servings({"servings": None}) == 4
    assert servings({"servings": 6}) == 6

    pool = [{"id": "a", "servings": 4, "times_suggested": 3},
            {"id": "b", "servings": 4, "times_suggested": 0},
            {"id": "c", "servings": 4, "times_suggested": 1, "in_queue": True}]
    assert pick_fallback(pool, 10) == ["c", "b", "a"]    # queue, then least-used
    assert pick_fallback(pool, 4) == ["c"]
    assert pick_fallback(pool, 0) == []


def test_week_label_is_portable():
    """strftime("%b %-d") is a glibc extension — it raises on Windows."""
    for d in [date(2026, 8, 7), date(2026, 8, 17), date(2026, 12, 1)]:
        label = f"{d:%b} {d.day}"
        assert label.split()[1] == str(d.day), label
    assert f"{date(2026, 8, 7):%b} {date(2026, 8, 7).day}" == "Aug 7"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
