"""Tool results: structured for the app, and the same JSON as compact text for the model."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from mcp import Client

pytestmark = pytest.mark.anyio
Connect = Callable[[str], Client]


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("get_diary_day", {}),
        ("get_period_summary", {"from_day": "2026-10-12", "to_day": "2026-10-14"}),
        ("search_foods", {"description": "skyr"}),
    ],
)
async def test_a_result_is_sent_once_as_compact_text_alongside_its_schema(
    connect: Connect, tool: str, arguments: dict[str, Any]
) -> None:
    async with connect("jakub") as client:
        tools = {listed.name: listed for listed in (await client.list_tools()).tools}
        result = await client.call_tool(tool, arguments)

    [block] = result.content
    text = block.model_dump()["text"]

    assert tools[tool].output_schema is not None
    assert json.loads(text) == result.structured_content
    assert "\n" not in text
    assert ": " not in text
