import os
import sys

from flask import Flask, request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bot import send_telegram, fetch_latest_news, rewrite_headline, load_posted_urls
from groq import Groq

app = Flask(__name__)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
NEWS_API_KEY = os.environ.get("NEWS_API_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True)
    if not data:
        return "ok", 200

    msg = data.get("message", {})
    chat_id = str(msg.get("chat", {}).get("id", ""))
    text = msg.get("text", "").strip()
    first_name = msg.get("from", {}).get("first_name", "")

    if not chat_id or not text or not text.startswith("/"):
        return "ok", 200

    if text == "/start":
        send_telegram(BOT_TOKEN, chat_id,
            f"Hey {first_name}! I track crypto, finance, and tech news.\n\n"
            f"Every 2 hours I'll send a top headline rewritten by AI.\n\n"
            f"/latest \u2014 Get news right now\n"
            f"/help \u2014 Commands\n"
            f"/status \u2014 Stats")

    elif text == "/help":
        send_telegram(BOT_TOKEN, chat_id,
            "/start \u2014 Welcome\n"
            "/latest \u2014 Post the top news story now\n"
            "/status \u2014 How many articles posted\n"
            "/help \u2014 This menu")

    elif text == "/status":
        posted = load_posted_urls()
        send_telegram(BOT_TOKEN, chat_id,
            f"Articles posted: {len(posted)}\n"
            f"Schedule: Every 2 hours\n"
            f"Topics: Crypto, Finance, Tech, AI")

    elif text == "/latest":
        try:
            articles = fetch_latest_news(NEWS_API_KEY)
            if articles:
                groq_client = Groq(api_key=GROQ_API_KEY)
                rewritten = rewrite_headline(groq_client, articles[0]["title"], articles[0]["url"])
                send_telegram(BOT_TOKEN, chat_id, rewritten)
                send_telegram(BOT_TOKEN, chat_id, "Done \u2014 posted above \u2705")
            else:
                send_telegram(BOT_TOKEN, chat_id, "No news found right now.")
        except Exception as e:
            send_telegram(BOT_TOKEN, chat_id, f"Error: {e}")

    return "ok", 200
