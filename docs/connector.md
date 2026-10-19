# Connector

Over HTTP, each person adds the server to their own Claude account as a custom connector and signs in with their user `id` and connector password. This page explains how that sign-in works and how to sign someone out.

## Add the connector

1. Set `FATSECRET_MCP_BASE_URL` to the server's public HTTPS origin, for example `https://diary.example.com`, with no path, query or credentials. For local development use `http://127.0.0.1:8000`.
2. In Claude, add a custom connector with the URL `<origin>/mcp`. Leave the OAuth client ID and secret empty: Claude detects sign-in from the server's metadata and uses its own published identity.
3. Sign in with the user `id` and the connector password from `add-user`. The sign-in page names the host your browser returns to, which is always `claude.ai`.
4. The connector appears in Claude on the web, desktop and phone. In its tool settings, the four reading tools can be set to "Always allow". Keep `confirm_draft` on "Needs approval", so every write asks you first.

`GET /health` returns only `{"status":"ok"}`. An unauthenticated request to `/mcp` returns `401` and the address of the OAuth metadata.

## Only Claude's connector can sign in

The server offers no dynamic client registration. It pins two values used by Claude's web, desktop and mobile apps: the client ID `https://claude.ai/oauth/mcp-oauth-client-metadata` and the callback `https://claude.ai/api/mcp/auth_callback`, both in `src/fatsecret_mcp/auth/client_metadata.py`. Any other client ID is refused before any request is made, so a link to the sign-in page can't send your code anywhere else.

The server fetches that one client metadata document and requires it to list the callback as well. The fetch uses its own two connections, separate from FatSecret calls, and the document is kept for an hour; a failed fetch isn't kept. At most ten fetches run in a minute; beyond that, opening the sign-in page returns `503` with `Retry-After`.

If Claude changes either value, adding the connector fails with `invalid_request` before the sign-in page appears. The callback is listed in Anthropic's [Authentication for connectors](https://claude.com/docs/connectors/building/authentication); the client ID is not published there. To find the new values, open the failed sign-in page's address: its `client_id` and `redirect_uri` parameters are what Claude sent. Update the constants and redeploy.

## Codes and tokens

Authorization uses PKCE S256. Signed codes expire after sixty seconds and can be exchanged once per running instance. Signed access tokens last one hour and are bound to this server and person. They survive a restart with the same configuration; the record of used codes is kept in memory. Connector credentials are signed with `FATSECRET_MCP_SIGNING_KEY`, in a separate signing context from drafts. HTTP access logs are disabled because authorization URLs contain credentials.

Claude renews access with a signed refresh token. Each refresh returns a new refresh token valid for thirty days, so a connector used at least once a month stays signed in. Nothing is stored server-side, so an earlier refresh token keeps working until it expires.

## Sign someone out

- Every device of one person, for example after losing a phone: increase their `session_generation` in `FATSECRET_MCP_USERS` and redeploy. All their access and refresh tokens stop working, and they sign in again with the same password.
- Remove a person: delete their entry from `FATSECRET_MCP_USERS` and redeploy, then delete any other copy of their entry, such as a local `.env`. Their tokens stop working and they can't sign in.
- Everyone at once: change `FATSECRET_MCP_SIGNING_KEY` and redeploy. This also invalidates every open draft.

## Sign-in throttling

Sign-in allows at most sixty attempts across the server in a rolling minute and two password checks at a time. Extra attempts get HTTP 429 with `Retry-After`; rejected attempts don't extend the minute. Each sign-in form accepts at most five submissions, then asks you to start sign-in again from your connector.

After five failed sign-ins for one user name, sign-in with that name is paused for fifteen minutes, even with the correct password. The limit applies to every typed name in the same way, so it doesn't reveal which names exist. Someone who knows a person's `id` can therefore delay their next sign-in, but connectors that are already signed in keep working. The counters remember at most 1,024 names and 1,024 forms. They are kept in memory and reset when the server restarts.
