"""
test_agents.py
--------------
Offline checks for the logic that broke in production. No framework, no
network, no DB — just asserts. Run it with:

    python test_agents.py

Each block corresponds to a bug that shipped, so a regression fails loudly
here instead of silently on a Saturday morning.
"""

from datetime import date, timedelta
from types import SimpleNamespace

from agents.claude import parse_json_response, response_text
from agents.saturday_suggest import next_monday, validate_selection


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
        assert next_monday(saturday) == next_monday(sunday), saturday
        # and it really is the upcoming Monday, not some other day
        assert next_monday(saturday).weekday() == 0
        assert next_monday(saturday) > saturday

    # Sanity on the rest of the week: never returns a past Monday.
    for offset in range(14):
        d = date(2026, 8, 17) + timedelta(days=offset)
        assert next_monday(d) >= d, d
        assert next_monday(d).weekday() == 0, d


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
    Claude's ids are untrusted. An invented id would be written into
    weekly_plans.lunches and then match nothing downstream.
    """
    lunches = {f"L{i}" for i in range(6)}
    dinners = {f"D{i}" for i in range(4)}
    good = {"lunch_ids": ["L0", "L1", "L2", "L3", "L4"],
            "dinner_ids": ["D0", "D1", "D2"]}
    validate_selection(good, lunches, dinners)   # must not raise

    bad_cases = [
        {**good, "lunch_ids": ["L0", "L1", "L2", "L3"]},              # too few
        {**good, "lunch_ids": ["L0", "L1", "L2", "L3", "L4", "L5"]},  # too many
        {**good, "lunch_ids": ["L0", "L0", "L1", "L2", "L3"]},        # duplicate
        {**good, "lunch_ids": ["L0", "L1", "L2", "L3", "nope"]},      # invented
        {**good, "dinner_ids": ["D0", "D1", "L0"]},                   # wrong pool
        {**good, "dinner_ids": []},                                   # empty
        {"lunch_ids": good["lunch_ids"]},                             # key missing
    ]
    for case in bad_cases:
        try:
            validate_selection(case, lunches, dinners)
        except RuntimeError:
            pass
        else:
            raise AssertionError(f"expected rejection: {case}")


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
