# fatsecret-mcp

A self-hosted [MCP](https://modelcontextprotocol.io) server that lets Claude read your [FatSecret](https://www.fatsecret.com) food diary, check it against your daily calorie and macro targets, and log food entries after you approve a draft.

This project is unofficial. It is not affiliated with or endorsed by FatSecret, and it is not medical or dietary advice.

[Powered by fatsecret Platform API](https://platform.fatsecret.com)

## Status

Early development. The server runs locally over stdio, answers diary questions, searches foods, prepares drafts and saves the drafts you confirm. Drafts show amounts, calories, macros, assumptions, a suggested meal and the day's totals after adding. They expire after thirty minutes; nothing is written to your diary until you confirm one.

## Run it locally

You need Python 3.13, [uv](https://docs.astral.sh/uv/), your own OAuth 1.0 consumer key and secret from the [FatSecret Platform API](https://platform.fatsecret.com), and an OAuth 1.0 access token for your FatSecret account.

Put the configuration in a `.env` file in the repository (it's git-ignored):

```sh
FATSECRET_CONSUMER_KEY=...
FATSECRET_CONSUMER_SECRET=...
FATSECRET_MCP_SIGNING_KEY=...
FATSECRET_MCP_USERS='[{"id": "me", "display_name": "Me", "fatsecret_access_token": "...", "fatsecret_access_secret": "...", "daily_targets": {"kcal": 2200, "protein": 140, "fat": 70, "carbohydrate": 250}, "timezone": "Europe/Warsaw"}]'
```

`FATSECRET_MCP_USERS` lists everyone the server serves. With more than one person, set `FATSECRET_MCP_USER` to the `id` the server should act as.

Generate a random signing key once with `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'` and put it in `FATSECRET_MCP_SIGNING_KEY` (at least 32 characters). Keep it across restarts; changing it invalidates drafts signed with the old key. Draft ids are signed, self-contained values holding entry names, ids, units, meal, diary day, user id and expiry; they contain no nutrition values.

Each user's optional `meal_hours` sets local meal start times, for example `"meal_hours": {"breakfast": "06:00", "lunch": "12:00", "dinner": "18:00", "snacks": "22:00"}` inside their user object. A meal lasts until the next start, including across midnight; times must differ. Omitted starts default to 04:00, 11:30, 16:00 and 21:00 respectively. Between midnight and 04:00, drafts ask whether to use Snacks on the previous day. An explicit meal or diary day overrides suggestions.

To try drafting, ask Claude to find a food and propose an amount. `draft_entries` takes `items` with `food_id`, `serving_id`, `number_of_units` and an optional `assumption`. Units follow the serving returned by `search_foods`: a serving described as "100 g" with 100 units needs 150 units for 150 g. Amounts must be positive and finite, with at most 10,000 units, 10,000 g or ml where weight or volume is known, and 20,000 kcal per item.

When you approve a draft, Claude calls `confirm_draft` with its `draft_id`, and Claude Code asks you to allow the write. Each item becomes one food entry in your FatSecret diary, under the draft's name, meal and day. A draft can be confirmed only by the user it was made for and only before it expires. Confirming it again writes nothing and returns the earlier result. That holds until the draft expires or the server restarts. If FatSecret fails part-way through, the result lists which entries were saved; draft the rest again.

Food search uses only the FatSecret catalog that a free API key can reach: US foods, searched in English. Pass a description in your own language and Claude adds an English rendering. Search never reads your diary, so each search costs one catalog request plus one per food shown (up to five).

Past-day entries are shared across diary questions in memory until the server stops or twelve hours pass from fetching them. Today is always read fresh. If you edit a past day in the FatSecret app, ask Claude to refresh it: `get_diary_day` and `get_period_summary` accept `refresh=true`. Period summaries reuse cached entries and fetch month totals for dates not yet loaded.

Then add the server to Claude Code:

```sh
claude mcp add fatsecret -- uv run --directory /path/to/fatsecret-mcp --env-file .env fatsecret-mcp
```

## Roadmap

- **Your own recent foods.** Products you often log, such as local or branded ones the US catalog lacks, aren't searched yet. Reading them from your FatSecret diary took too many API requests, so this needs another approach, for example another food database whose product is then logged to FatSecret as a new entry.
- **Remember products logged from a label.** A product logged from a photo of its nutrition label will need its label again next time. The server could remember such products per user, which needs persistent storage.

## Development

```sh
uv sync
uv run ruff check && uv run ruff format --check
uv run pyright
uv run pytest
```

The tests never call FatSecret. They replay recorded responses from `tests/fixtures/fatsecret/`. An opt-in smoke test reads your real diary: `FATSECRET_LIVE_SMOKE=1 uv run --env-file .env pytest tests/test_live_smoke.py`.

## License

[MIT](LICENSE)
