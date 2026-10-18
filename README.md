# fatsecret-mcp

A self-hosted [MCP](https://modelcontextprotocol.io) server that lets Claude read your [FatSecret](https://www.fatsecret.com) food diary, check it against your daily calorie and macro targets, and log food entries after you approve a draft.

This project is unofficial. It is not affiliated with or endorsed by FatSecret, and it is not medical or dietary advice.

[Powered by fatsecret Platform API](https://platform.fatsecret.com)

## Status

Early development. The server runs locally over stdio or over HTTP with connector sign-in, answers diary questions, searches foods, prepares drafts and saves the drafts you confirm. Drafts show amounts, calories, macros, assumptions, a suggested meal and the day's totals after adding. They expire after thirty minutes; nothing is written to your diary until you confirm one.

## Run it locally

You need Python 3.13, [uv](https://docs.astral.sh/uv/) and your own OAuth 1.0 consumer key and secret from the [FatSecret Platform API](https://platform.fatsecret.com).

Put the configuration in a `.env` file in the repository (it's git-ignored):

```sh
FATSECRET_CONSUMER_KEY=...
FATSECRET_CONSUMER_SECRET=...
FATSECRET_MCP_SIGNING_KEY=...
FATSECRET_MCP_USERS='[{"id": "me", "display_name": "Me", "fatsecret_access_token": "...", "fatsecret_access_secret": "...", "daily_targets": {"kcal": 2200, "protein": 140, "fat": 70, "carbohydrate": 250}, "timezone": "Europe/Warsaw"}]'
```

`FATSECRET_MCP_USERS` lists everyone the server serves. You don't write it by hand: `add-user` (below) produces it. With more than one person, set `FATSECRET_MCP_USER` to the `id` the server should act as over stdio.

### Add a user

```sh
uv run --env-file .env fatsecret-mcp add-user
```

The command takes about two minutes per person:

1. It shows a FatSecret link. The new user opens it, signs in with their own FatSecret account (the one in the FatSecret app, not your developer account), allows access, and reads out the PIN that FatSecret shows.
2. You type their display name, a user `id` for signing in, their daily targets, their timezone (Europe/Warsaw by default) and, optionally, meal hours.
3. It generates a connector password and shows it once. Give it to the user straight away; the configuration keeps only its scrypt hash.
4. It prints the complete new `FATSECRET_MCP_USERS` value. Paste the whole value into `.env` or the server's secrets.

When `FATSECRET_MCP_USERS` is already set, the new user is added to it and everyone else is kept exactly as written. An `id` that's already taken is refused. To sign an existing user in to FatSecret again, remove them from the value first.

### Check the configuration

```sh
uv run --env-file .env fatsecret-mcp check
```

`check` validates the configuration as `serve` does at startup: the HTTP settings when `FATSECRET_MCP_BASE_URL` is set, otherwise the acting user for stdio. Then it reads each user's current diary day, one FatSecret request per user, and reports per user whether the connection works. A revoked access token, an exhausted daily quota and an IP restriction each get their own message. It prints no tokens, secrets or passwords, and exits with status 1 if anything failed.

Generate a random signing key once with `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'` and put it in `FATSECRET_MCP_SIGNING_KEY` (at least 32 characters). Keep it across restarts; changing it invalidates drafts signed with the old key. Draft ids are signed, self-contained values holding entry names, ids, units, meal, diary day, user id and expiry; they contain no nutrition values.

Each user's optional `meal_hours` sets local meal start times, for example `"meal_hours": {"breakfast": "06:00", "lunch": "12:00", "dinner": "18:00", "snacks": "22:00"}` inside their user object. A meal lasts until the next start, including across midnight; times must differ. Omitted starts default to 04:00, 11:30, 16:00 and 21:00 respectively. Between midnight and 04:00, drafts ask whether to use Snacks on the previous day. An explicit meal or diary day overrides suggestions.

To try drafting, ask Claude to find a food and propose an amount. `draft_entries` takes `items` with `food_id`, `serving_id`, `number_of_units` and an optional `assumption`. Units follow the serving returned by `search_foods`: a serving described as "100 g" with 100 units needs 150 units for 150 g. Amounts must be positive and finite, with at most 10,000 units, 10,000 g or ml where weight or volume is known, and 20,000 kcal per item.

When you approve a draft, Claude calls `confirm_draft` with its `draft_id`, and Claude Code asks you to allow the write. Each item becomes one food entry in your FatSecret diary, under the draft's name, meal and day. A draft can be confirmed only by the user it was made for and only before it expires. Confirming it again writes nothing and returns the earlier result. That holds until the draft expires or the server restarts. If FatSecret fails part-way through, the result lists which entries were saved; draft the rest again.

Food search uses only the FatSecret catalog that a free API key can reach: US foods, searched in English. Pass a description in your own language and Claude adds an English rendering. Search never reads your diary, so each search costs one catalog request plus one per food shown (up to five).

Past-day entries are shared across diary questions in memory until the server stops or twelve hours pass from fetching them. Today is always read fresh. If you edit a past day in the FatSecret app, ask Claude to refresh it: `get_diary_day` and `get_period_summary` accept `refresh=true`. Period summaries reuse cached entries and fetch month totals for dates not yet loaded.

Then add the server to Claude Code. Without a command, `fatsecret-mcp` serves over stdio, the same as `fatsecret-mcp serve`:

```sh
claude mcp add fatsecret -- uv run --directory /path/to/fatsecret-mcp --env-file .env fatsecret-mcp
```

## Run over HTTP

For HTTP, set `FATSECRET_MCP_BASE_URL` to the server's public HTTPS origin, for example `https://diary.example.com`. For local development, use `http://127.0.0.1:8000`. The value has no path, query or credentials; the connector URL is that origin plus `/mcp`.

Each user needs a connector password. `add-user` generates one and stores its scrypt hash in that user's `connector_password_hash` field. Their optional `session_generation` is an integer starting at `0`. Keep the existing instance signing key: it also signs connector credentials, using a separate signing context from drafts. Configuration errors stop the server before it starts listening.

```sh
uv run --env-file .env fatsecret-mcp serve --transport http --host 127.0.0.1 --port 8000
```

`GET /health` returns only `{"status":"ok"}`. An unauthenticated request to `/mcp` returns `401` and the address of the OAuth metadata. Once the server is reachable over HTTPS, add its `/mcp` URL as a custom connector in Claude. Sign in with your user `id` and connector password. The sign-in page names the host your browser returns to, which is always `claude.ai`. The server does not offer dynamic client registration.

Only Claude's own connector can start sign-in. The server pins two values used by Claude's web, desktop and mobile apps: the client ID `https://claude.ai/oauth/mcp-oauth-client-metadata` and the callback `https://claude.ai/api/mcp/auth_callback`, both in `auth/client_metadata.py`. It fetches that one client metadata document and requires it to list the callback as well. Any other client ID is refused before any request is made, so a link to the sign-in page can't send your code anywhere else. If Claude changes either value, adding the connector fails with `invalid_request` before the sign-in page appears. The callback is listed in Anthropic's [Authentication for connectors](https://claude.com/docs/connectors/building/authentication); the client ID is not published there. To find the new values, open the failed sign-in page's address: its `client_id` and `redirect_uri` parameters are what Claude sent. Update the constants and redeploy.

Authorization uses PKCE S256. Signed codes expire after sixty seconds and can be exchanged once per running instance. Signed access tokens last one hour and are bound to this server and user. They survive a restart with the same configuration; the record of used codes is kept in memory. HTTP access logs are disabled because authorization URLs contain credentials.

Claude renews access with a signed refresh token. Each refresh returns a new refresh token valid for thirty days, so a connector used at least once a month stays signed in. Nothing is stored server-side, so an earlier refresh token keeps working until it expires. To sign out every device of one user, for example after losing a phone, increase their `session_generation` and redeploy: all of their access and refresh tokens stop working, and they sign in again with the same password. Removing a user from `FATSECRET_MCP_USERS` invalidates their tokens and blocks their sign-in.

Sign-in allows at most sixty attempts across the server in a rolling minute and two password checks at a time. Extra attempts get HTTP 429 with `Retry-After`; rejected attempts do not extend the minute-long limit. Each sign-in form accepts at most five submissions, then asks you to start sign-in again from your connector.

After five failed sign-ins for one user name, sign-in with that name is paused for fifteen minutes, even with the correct password. The limit applies to every typed name in the same way, so it doesn't reveal which names exist. Someone who knows a user's `id` can therefore delay that user's next sign-in, but connectors that are already signed in keep working. The counters remember at most 1,024 names and 1,024 forms, without scanning every name on each attempt. They are kept in memory and reset when the server restarts.

## Run with Docker

CI builds and tests the image on pull requests. After a successful push to `main`, it publishes `ghcr.io/<owner>/<repository>:latest` and a tag with the full commit SHA, for Linux amd64 and arm64. Replace `<owner>` and `<repository>` with your GitHub repository's namespace and name.

The first publication creates a private GHCR package. The repository owner must change its visibility to **Public** in the package settings, then rerun CI. The final CI step checks that the published image can be pulled without signing in.

Use the same HTTP configuration as above, supplied at runtime. Docker's `--env-file` expects `KEY=value` lines without shell quotes: keep `FATSECRET_MCP_USERS` as a single JSON line, removing the surrounding single quotes from the example above.

```sh
docker run --rm --env-file .env -p 8080:8080 \
  -e FATSECRET_MCP_BASE_URL=http://127.0.0.1:8080 \
  'ghcr.io/<owner>/<repository>:latest'
```

Leave the server running. In a second terminal, check its health:

```sh
curl --fail http://127.0.0.1:8080/health
```

Expect `{"status":"ok"}`. If the server is still starting, retry the request. To stop it, press Ctrl+C in the first terminal.

The image starts HTTP on `0.0.0.0:8080`, runs as a non-root user, and checks `/health`. Set `PORT` to change the listening port and adjust the published port to match. On a container host, set `FATSECRET_MCP_BASE_URL` to its public HTTPS origin. Configuration and credentials are read only from the runtime environment.

To build and test locally, with Docker running:

```sh
docker build -t fatsecret-mcp:local .
FATSECRET_CONTAINER_IMAGE=fatsecret-mcp:local uv run pytest tests/test_container.py -v -s
```

The container tests use synthetic credentials, make no FatSecret calls, and report the time from starting the container to a healthy HTTP response. The build installs the locked runtime dependencies and compiles bytecode in a separate stage; the final image contains the installed package without build or development tools.

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
