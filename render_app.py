import os
import sys

for var in ("TELEGRAM_BOT_TOKEN", "NEWS_API_KEY", "GROQ_API_KEY"):
    if var not in os.environ:
        print(f"Missing required env var: {var}", file=sys.stderr)
        sys.exit(1)

from bot import run_listener

run_listener(
    os.environ["TELEGRAM_BOT_TOKEN"],
    os.environ["NEWS_API_KEY"],
    os.environ["GROQ_API_KEY"],
)
