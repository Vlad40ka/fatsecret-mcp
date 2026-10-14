# FatSecret API fixtures

Responses from the FatSecret Platform API, recorded with a Basic-tier key. The gateway tests replay them instead of calling the API.

Each file holds one exchange:

- `request`: HTTP method, URL and the non-OAuth parameters. Calls use the method-based `rest/server.api` endpoint with OAuth 1.0 parameters in the query string.
- `response`: status, content type, and the body as `json` or, for the OAuth token endpoints, as form-encoded `text`.
- `source`: `recorded` means it came from the live API. `reconstructed` means the error can't be triggered safely, so the file is built from the [documented error format](https://platform.fatsecret.com/docs/guides/error-codes).

What was removed before committing:

- consumer keys, tokens, signatures and nonces (OAuth parameters are never stored; token values are replaced with placeholders like `ACCESS_TOKEN`);
- real `food_entry_id` values, replaced with fake ones that stay consistent across files.

Food Diary fixtures come from test entries logged on an otherwise empty day in January 2015 and deleted afterwards, not from real eating history.
