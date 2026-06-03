"""Starter registry: well-known companies on free, public ATS boards.

Each entry is (ats, token, display_name). Tokens are the company's slug within its ATS — the same
value that appears in the board URL (e.g. boards.greenhouse.io/<token>, jobs.lever.co/<token>).

Invalid/changed tokens are harmless: the fetcher tolerates a board that returns nothing and simply
skips it. This list only needs to be *useful*, not perfect — users grow the registry by adding
their own target companies, and we can expand the seed over time.
"""

from __future__ import annotations

# (ats, token, display_name)
SEED_COMPANIES: list[tuple[str, str, str]] = [
    # --- Greenhouse ---
    ("greenhouse", "anthropic", "Anthropic"),
    ("greenhouse", "stripe", "Stripe"),
    ("greenhouse", "databricks", "Databricks"),
    ("greenhouse", "coinbase", "Coinbase"),
    ("greenhouse", "reddit", "Reddit"),
    ("greenhouse", "robinhood", "Robinhood"),
    ("greenhouse", "instacart", "Instacart"),
    ("greenhouse", "doordash", "DoorDash"),
    ("greenhouse", "gusto", "Gusto"),
    ("greenhouse", "brex", "Brex"),
    ("greenhouse", "discord", "Discord"),
    ("greenhouse", "figma", "Figma"),
    ("greenhouse", "dropbox", "Dropbox"),
    ("greenhouse", "pinterest", "Pinterest"),
    ("greenhouse", "cloudflare", "Cloudflare"),
    ("greenhouse", "datadog", "Datadog"),
    ("greenhouse", "asana", "Asana"),
    ("greenhouse", "samsara", "Samsara"),
    ("greenhouse", "affirm", "Affirm"),
    ("greenhouse", "airbnb", "Airbnb"),
    ("greenhouse", "lyft", "Lyft"),
    ("greenhouse", "twitch", "Twitch"),
    ("greenhouse", "plaid", "Plaid"),
    ("greenhouse", "ramp", "Ramp"),
    ("greenhouse", "mongodb", "MongoDB"),
    # --- Lever ---
    ("lever", "lever", "Lever"),
    ("lever", "netlify", "Netlify"),
    ("lever", "spotify", "Spotify"),
    ("lever", "plaid", "Plaid"),
]
