"""The Food Diary's concepts and the calculations on them, with no MCP and no HTTP.

- `models`: the concepts as data.
- `diary_day`: which Diary Day it is for a User.
- `day_summary`: one Diary Day at a glance.
- `period_summary`: daily totals and Daily Targets across a bounded period.
- `meal_suggestion`: which Meal a Draft suggests from the User's local time.
- `drafts`: proposed Food Entries, their Diary Day and their totals.
- `stand_ins`: a catalog Food logged in place of a product missing from the catalog.
- `draft_token`: a Draft sealed into its signed Draft id, and opened again.
- `confirmed_drafts`: the Drafts already written, so a second confirm writes nothing.
"""
