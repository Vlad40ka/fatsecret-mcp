"""Asking the Operator questions in the terminal. Tests replace it with scripted answers."""

from __future__ import annotations

from typing import Protocol


class Console(Protocol):
    def ask(self, question: str) -> str:
        """The Operator's answer to `question`, as typed."""
        ...

    def tell(self, text: str = "") -> None:
        """Show `text` to the Operator."""
        ...


class Terminal:
    def ask(self, question: str) -> str:
        return input(question)

    def tell(self, text: str = "") -> None:
        print(text, flush=True)
