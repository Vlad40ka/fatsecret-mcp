"""Everything about talking to the FatSecret Platform API.

- `gateway`: the interface the rest of the server uses, so it never depends on HTTP details.
- `client`: the real implementation, making signed HTTP calls.
- `oauth`: OAuth 1.0 request signing, for API calls and the PIN flow that signs a User in.
- `parsing`: FatSecret's JSON turned into domain objects.
- `errors`: what can go wrong, as errors with messages for the User and the Operator.
"""
