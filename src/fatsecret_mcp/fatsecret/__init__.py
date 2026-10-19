"""Everything about talking to the FatSecret Platform API.

- `gateway`: the interface the rest of the server uses, so it never depends on HTTP details.
- `client`: the real implementation, making signed HTTP calls.
- `request_limits`: pacing every call, and pausing after FatSecret's throttle or quota errors.
- `budget`: each User's daily share of the Household's request quota.
- `oauth`: OAuth 1.0 request signing, for API calls and the PIN flow that signs a User in.
- `parsing`: FatSecret's JSON turned into domain objects.
- `catalog`: catalog Foods with Servings that can be logged.
- `food_cache`: Foods fetched with their Servings, kept for an hour and shared by every User.
- `diary`: one User's Food Diary, with its short-lived cache of Diary Days.
- `errors`: what can go wrong, as errors with messages for the User and the Operator.
"""
