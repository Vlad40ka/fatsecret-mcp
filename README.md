# fatsecret-mcp

A self-hosted [MCP](https://modelcontextprotocol.io) server that lets Claude read your [FatSecret](https://www.fatsecret.com) food diary and check it against your daily calorie and macro targets. Logging food through Claude is next.

This project is unofficial. It is not affiliated with or endorsed by FatSecret, and it is not medical or dietary advice.

[Powered by fatsecret Platform API](https://platform.fatsecret.com)

## Status

Early development. Today the server runs locally over stdio and answers questions like "what have I eaten today and how much is left?" from your real FatSecret food diary.

## Run it locally

You need Python 3.13, [uv](https://docs.astral.sh/uv/), your own OAuth 1.0 consumer key and secret from the [FatSecret Platform API](https://platform.fatsecret.com), and an OAuth 1.0 access token for your FatSecret account.

Put the configuration in a `.env` file in the repository (it's git-ignored):

```sh
FATSECRET_CONSUMER_KEY=...
FATSECRET_CONSUMER_SECRET=...
FATSECRET_MCP_USERS='[{"id": "me", "display_name": "Me", "fatsecret_access_token": "...", "fatsecret_access_secret": "...", "daily_targets": {"kcal": 2200, "protein": 140, "fat": 70, "carbohydrate": 250}, "timezone": "Europe/Warsaw"}]'
```

`FATSECRET_MCP_USERS` lists everyone the server serves. With more than one person, set `FATSECRET_MCP_USER` to the `id` the server should act as.

Then add the server to Claude Code:

```sh
claude mcp add fatsecret -- uv run --directory /path/to/fatsecret-mcp --env-file .env fatsecret-mcp
```

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
