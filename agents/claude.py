"""
claude.py
---------
The one Claude call the three agents share.

Two bugs used to be copy-pasted across every call site: a hardcoded model id
that eventually 404s, and reading `response.content[0].text` — which breaks the
moment a model emits a thinking block first. Both are fixed here, once.
"""

import json
import os
import re

import anthropic

# Override with ANTHROPIC_MODEL to pin or upgrade without touching code.
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")

# Models sometimes wrap JSON in a ```json fence despite being told not to.
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


def response_text(response) -> str:
    """Join the text blocks, skipping thinking/tool blocks."""
    return "".join(b.text for b in response.content if b.type == "text").strip()


def parse_json_response(response, max_tokens: int):
    """Split out from ask_json so it is testable without a network call."""
    # Thinking tokens count against max_tokens, so a budget that used to be
    # ample can now truncate mid-JSON. Say so plainly instead of surfacing a
    # baffling JSONDecodeError.
    if response.stop_reason == "max_tokens":
        raise ValueError(
            f"{MODEL} hit the {max_tokens}-token cap before finishing its JSON; "
            f"raise max_tokens"
        )
    return json.loads(_FENCE.sub("", response_text(response)))


def ask_json(system: str, user: str, max_tokens: int = 2000):
    """
    Call Claude and parse the reply as JSON. Raises on unparseable output —
    callers decide whether that is fatal.
    """
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    response = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return parse_json_response(response, max_tokens)
