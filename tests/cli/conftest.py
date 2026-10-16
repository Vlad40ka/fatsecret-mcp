from __future__ import annotations

import json
from collections.abc import Callable, Sequence

from tests.conftest import HOUSEHOLD_USERS

Answer = str | Callable[[], str]

ENVIRON = {
    "FATSECRET_CONSUMER_KEY": "consumer-key",
    "FATSECRET_CONSUMER_SECRET": "consumer-secret",
    "FATSECRET_MCP_SIGNING_KEY": "test-signing-key-0123456789abcdef",
    "FATSECRET_MCP_USERS": json.dumps(HOUSEHOLD_USERS),
}
"""A valid Household of two Users, for stdio."""


class ScriptedConsole:
    """The Operator at the terminal, giving scripted answers; a callable answer is computed when asked."""

    def __init__(self, answers: Sequence[Answer]) -> None:
        self._answers = list(answers)
        self.transcript: list[str] = []
        """Everything shown on the terminal, questions included, line by line."""

    def ask(self, question: str) -> str:
        self.transcript.append(question)

        assert self._answers, f"asked more than the script answers: {question!r}"
        answer = self._answers.pop(0)

        return answer() if callable(answer) else answer

    def tell(self, text: str = "") -> None:
        self.transcript.extend(text.splitlines() or [""])

    @property
    def text(self) -> str:
        return "\n".join(self.transcript)

    @property
    def unanswered(self) -> int:
        return len(self._answers)
