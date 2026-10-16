"""Household configuration: the server's FatSecret API key and the fixed list of Users it serves.

- `models`: the shape of the configuration and its rules.
- `loader`: reading it from environment variables at startup.
- `writer`: adding a User to the `FATSECRET_MCP_USERS` value, for the Operator to paste.
"""
