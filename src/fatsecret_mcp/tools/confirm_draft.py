"""confirm_draft: write a Draft the User approved to their Food Diary."""

from __future__ import annotations

from typing import Annotated

import anyio
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, ToolAnnotations
from pydantic import SecretStr

from fatsecret_mcp.config.models import User
from fatsecret_mcp.domain.confirmed_drafts import ConfirmedDrafts
from fatsecret_mcp.domain.diary_day import Clock
from fatsecret_mcp.domain.draft_token import InvalidDraft, open_draft
from fatsecret_mcp.domain.models import Confirmation, ConfirmedEntry, Draft
from fatsecret_mcp.fatsecret.diary import UserDiary
from fatsecret_mcp.fatsecret.errors import FatSecretError, UpstreamFailure
from fatsecret_mcp.fatsecret.gateway import AccessToken, FatSecretGateway
from fatsecret_mcp.tools.schemas import DraftConfirmation, DraftId, as_tool_result


def register(
    server: MCPServer,
    user: User,
    fatsecret: FatSecretGateway,
    diary: UserDiary,
    clock: Clock,
    signing_key: SecretStr,
) -> None:
    annotations = ToolAnnotations(
        title="Confirm Draft",
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=True,
    )
    confirmed_drafts = ConfirmedDrafts()
    # One confirm at a time, so a double tap can't write the same Draft twice.
    confirming = anyio.Lock()

    @server.tool(annotations=annotations)
    async def confirm_draft(draft_id: DraftId) -> Annotated[CallToolResult, DraftConfirmation]:
        """Write a Draft's Food Entries to the User's Food Diary, under its Meal and Diary Day.

        Call this only after the User explicitly approved this Draft in their latest message.
        After a correction, draft again and confirm the new Draft instead. Confirming the same
        Draft again writes nothing and returns the earlier result.
        """
        async with confirming:
            now = clock()

            try:
                draft = open_draft(
                    draft_id,
                    signing_key.get_secret_value(),
                    user_id=user.id,
                    now=now,
                )
            except InvalidDraft as error:
                raise ToolError(str(error)) from error

            earlier = confirmed_drafts.get(draft_id, now)

            if earlier is not None:
                return as_tool_result(DraftConfirmation.of(earlier, repeated=True))

            try:
                confirmation = await _write_entries(draft, user.fatsecret, fatsecret)
            finally:
                # Even a failed or cancelled write may have saved an entry, so the day is read afresh.
                with anyio.CancelScope(shield=True):
                    await diary.invalidate(draft.diary_day)

            confirmed_drafts.remember(draft_id, confirmation)

        return as_tool_result(DraftConfirmation.of(confirmation, repeated=False))


async def _write_entries(draft: Draft, user: AccessToken, fatsecret: FatSecretGateway) -> Confirmation:
    """Write the items in order, stopping at the first failure; fails when nothing was written."""
    entries: list[ConfirmedEntry] = []
    failure: FatSecretError | None = None

    for entry in draft.items:
        food_entry_id: str | None = None

        if failure is None:
            try:
                food_entry_id = await fatsecret.create_food_entry(user, entry, draft.meal, draft.diary_day)
            except FatSecretError as error:
                failure = error

        confirmed = ConfirmedEntry(
            entry=entry,
            food_entry_id=food_entry_id,
        )

        entries.append(confirmed)

    if failure is not None and entries[0].food_entry_id is None:
        raise ToolError(_nothing_written(failure))

    return Confirmation(
        draft=draft,
        entries=tuple(entries),
        failure=_unwritten(failure),
    )


def _nothing_written(failure: FatSecretError) -> str:
    if isinstance(failure, UpstreamFailure):
        # Without an answer, FatSecret may still have saved the first entry.
        return f"{failure} Check the Diary Day with get_diary_day before confirming this Draft again."

    return f"{failure} Nothing was written; the Draft can be confirmed again."


def _unwritten(failure: FatSecretError | None) -> str | None:
    if failure is None:
        return None
    if isinstance(failure, UpstreamFailure):
        return f"{failure} The first entry without a food_entry_id may have been saved anyway."

    return str(failure)
