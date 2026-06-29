import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

import feedparser
import requests
from groq import Groq

POSTED_DB = "posted_articles.json"
STATE_DB = "bot_state.json"
TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"

RSS_FEEDS = {
    "crypto": [
        "https://cointelegraph.com/rss",
        "https://coindesk.com/arc/outboundfeeds/rss/",
        "https://decrypt.co/feed",
        "https://cryptopotato.com/feed",
    ],
    "ai": [
        "https://techcrunch.com/feed/",
        "https://feeds.arstechnica.com/arstechnica/index",
    ],
}


def load_json(path: str) -> dict | list:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {} if path.endswith("_state.json") else []


def save_json(path: str, data: dict | list) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_posted_urls() -> set:
    data = load_json(POSTED_DB)
    return {item["url"] for item in data if "url" in item}


def save_article(url: str, title: str) -> None:
    data = load_json(POSTED_DB)
    posted = {item["url"] for item in data if "url" in item}
    if isinstance(data, list) and url not in posted:
        data.append({"url": url, "title": title, "posted_at": datetime.now(timezone.utc).isoformat()})
        save_json(POSTED_DB, data)


def send_telegram(bot_token: str, chat_id: str, text: str) -> str | None:
    try:
        resp = requests.post(
            TELEGRAM_API_URL.format(token=bot_token),
            json={"chat_id": chat_id, "text": text}, timeout=10,
        )
        result = resp.json()
        if result.get("ok"):
            return str(result["result"]["message_id"])
    except Exception:
        pass
    return None


def fetch_rss(categories: list[str], max_per_feed: int = 5) -> list[dict]:
    """Fetch articles from RSS feeds grouped by categories."""
    seen, articles = set(), []
    urls = []
    for cat in categories:
        urls.extend(RSS_FEEDS.get(cat, []))

    for feed_url in urls:
        try:
            parsed = feedparser.parse(feed_url)
            for entry in parsed.entries[:max_per_feed]:
                url = (entry.get("link") or "").strip()
                title = (entry.get("title") or "").strip()
                if not url or not title or url in seen:
                    continue
                seen.add(url)
                articles.append({
                    "title": title,
                    "url": url,
                    "source": {"name": getattr(parsed.feed, "title", "News")},
                    "description": (entry.get("summary") or entry.get("description") or "").strip(),
                    "published": entry.get("published", ""),
                })
        except Exception:
            continue
    return articles


def pick_unposted_article(articles: list[dict], posted: set) -> dict | None:
    for a in articles:
        if a["url"] not in posted:
            return a
    return None


def rewrite_headline(groq_client: Groq, title: str, url: str) -> str:
    resp = groq_client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {"role": "system", "content": (
                "You are a crypto-native observer. Rewrite raw news headlines into ultra-clean, "
                "observational takes.\n"
                "Rules:\n"
                "- Never use hashtags, generic emojis, robotic introductory phrases "
                "(like 'Here is an update:'), or corporate marketing buzzwords.\n"
                "- Keep the output to a single, concise sentence or thought under 250 characters.\n"
                "- Append the source URL at the very end of the final text."
            )},
            {"role": "user", "content": f"Headline: {title}\nSource URL: {url}"},
        ],
        temperature=0.7, max_tokens=150,
    )
    return resp.choices[0].message.content.strip()


def generate_digest(groq_api_key: str) -> str:
    """Fetch across crypto and ai RSS feeds, then write a conversational roundup."""
    articles = fetch_rss(["crypto", "ai"], max_per_feed=4)
    if not articles:
        return "Couldn't find any news right now."

    lines = []
    for a in articles:
        desc = a.get("description", "")
        lines.append(f"[{a['source']['name']}] {a['title']}\n{desc}\nSource: {a['url']}")

    groq_client = Groq(api_key=groq_api_key)
    resp = groq_client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {"role": "system", "content": (
                "You write a daily crypto & tech roundup. Your style is natural, "
                "conversational, and informative\u2014like a knowledgeable friend sharing "
                "what's happening across different corners of the space.\n\n"
                "Rules:\n"
                "- Write 400-600 words covering multiple stories\n"
                "- Use natural transitions between topics (don't just list them)\n"
                "- Mention specific prices, projects, and people where relevant\n"
                "- Keep it flowing and readable, not robotic or corporate\n"
                "- Never use hashtags, emojis, or intro phrases like 'Here is'\n"
                "- End with a short closing observation"
            )},
            {"role": "user", "content": "Latest stories:\n\n" + "\n\n".join(lines)},
        ],
        temperature=0.8, max_tokens=2000,
    )
    return resp.choices[0].message.content.strip()


def generate_summarized_digest(groq_api_key: str) -> str:
    """Fetch RSS and produce a tweet-length summarized digest (~5 tweets)."""
    articles = fetch_rss(["crypto", "ai"], max_per_feed=4)
    if not articles:
        return "Couldn't find any news right now."

    lines = []
    for a in articles:
        desc = a.get("description", "")
        lines.append(f"[{a['source']['name']}] {a['title']}\n{desc}\nSource: {a['url']}")

    groq_client = Groq(api_key=groq_api_key)
    resp = groq_client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {"role": "system", "content": (
                "Summarize the latest news into a tight, tweetable roundup. "
                "Aim for 5 tweets worth of content \u2014 about 200-250 words total. "
                "Hit the key developments across crypto and AI in a fast, scannable format. "
                "Group related stories. No hashtags, emojis, or fluff."
            )},
            {"role": "user", "content": "Latest stories:\n\n" + "\n\n".join(lines)},
        ],
        temperature=0.7, max_tokens=500,
    )
    return resp.choices[0].message.content.strip()


def generate_blog_post(topic: str, groq_api_key: str) -> str:
    """Fetch articles for a specific topic and write a deep-dive blog post."""
    cats = {"defi": ["crypto"], "ai": ["ai"], "crypto": ["crypto"]}
    articles = fetch_rss(cats.get(topic, ["crypto"]), max_per_feed=8)
    if not articles:
        return f"No recent news found on {topic}."

    lines = []
    for a in articles:
        desc = a.get("description", "")
        lines.append(f"- {a['title']}: {desc}\n  Source: {a['url']}")

    try:
        groq_client = Groq(api_key=groq_api_key)
        resp = groq_client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {"role": "system", "content": (
                    "You are a tech journalist writing a newsletter. Write a long-form blog post "
                    "based on the latest news items below.\n\n"
                    "Rules:\n"
                    "- Write 400-600 words in clear, engaging English\n"
                    "- Structure it like a real blog: headline, intro, body paragraphs, closing\n"
                    "- Cover the key developments and explain why they matter\n"
                    "- Never use hashtags, emojis, or marketing fluff\n"
                    "- End with a list of source URLs"
                )},
                {"role": "user", "content": "Latest stories:\n\n" + "\n\n".join(lines)},
            ],
            temperature=0.8, max_tokens=2000,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"Error generating report: {e}"


def split_long_message(text: str, limit: int = 4000) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts = []
    while text:
        if len(text) <= limit:
            parts.append(text)
            break
        split_at = text.rfind("\n\n", 0, limit)
        if split_at == -1:
            split_at = text.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        parts.append(text[:split_at])
        text = text[split_at:].strip()
    return parts


def post_news(bot_token: str, chat_id: str, groq_api_key: str) -> str | None:
    """Fetch from RSS, rewrite and send the latest unposted article."""
    posted = load_posted_urls()
    articles = fetch_rss(["crypto", "ai"], max_per_feed=3)
    article = pick_unposted_article(articles, posted)
    if not article:
        return None
    groq_client = Groq(api_key=groq_api_key)
    rewritten = rewrite_headline(groq_client, article["title"], article["url"])
    msg_id = send_telegram(bot_token, chat_id, rewritten)
    if msg_id:
        save_article(article["url"], article["title"])
    return msg_id


def handle_command(bot_token: str, chat_id: str, text: str, first_name: str,
                   groq_api_key: str) -> list[str]:
    responses = []

    if text == "/start":
        responses.append(
            f"Hey {first_name}! I track crypto, finance, and tech news.\n\n"
            f"Every 2 hours I\u2019ll send a top headline rewritten by AI.\n\n"
            f"/full_digest \u2014 Full daily roundup\n"
            f"/summarized_digest \u2014 Condensed tweet-length version\n"
            f"/latest \u2014 Get news right now\n"
            f"/report [topic] \u2014 Deep dive: defi, ai, crypto\n"
            f"/help \u2014 Commands\n"
            f"/status \u2014 Stats"
        )

    elif text == "/help":
        responses.append(
            "/start \u2014 Welcome\n"
            "/full_digest \u2014 Full daily roundup across crypto, AI, defi\n"
            "/summarized_digest \u2014 Tweet-length summary (~5 tweets)\n"
            "/digest \u2014 Alias for full_digest\n"
            "/latest \u2014 Post the top news story now\n"
            "/report defi \u2014 Deep dive into DeFi\n"
            "/report ai \u2014 Deep dive into AI\n"
            "/report crypto \u2014 Deep dive into crypto\n"
            "/status \u2014 How many articles posted\n"
            "/help \u2014 This menu"
        )

    elif text == "/status":
        posted = load_posted_urls()
        responses.append(
            f"Articles posted: {len(posted)}\n"
            f"Schedule: Every 2 hours\n"
            f"Topics: Crypto, Finance, Tech, AI"
        )

    elif text.startswith("/report"):
        parts = text.split(maxsplit=1)
        topic = parts[1].lower() if len(parts) > 1 else "crypto"
        if topic not in ("defi", "ai", "crypto"):
            topic = "crypto"
        try:
            blog = generate_blog_post(topic, groq_api_key)
            for chunk in split_long_message(blog):
                send_telegram(bot_token, chat_id, chunk)
            responses.append(f"Report generated on {topic}")
        except Exception as e:
            responses.append(f"Failed to generate report: {e}")

    elif text in ("/digest", "/full_digest"):
        try:
            digest = generate_digest(groq_api_key)
            for chunk in split_long_message(digest):
                send_telegram(bot_token, chat_id, chunk)
            responses.append("Full digest sent")
        except Exception as e:
            responses.append(f"Failed to generate digest: {e}")

    elif text == "/summarized_digest":
        try:
            summary = generate_summarized_digest(groq_api_key)
            send_telegram(bot_token, chat_id, summary)
            responses.append("Summarized digest sent")
        except Exception as e:
            responses.append(f"Failed to generate summarized digest: {e}")

    elif text == "/latest":
        try:
            msg_id = post_news(bot_token, chat_id, groq_api_key)
            if msg_id:
                responses.append("Done \u2014 posted above \u2705")
            else:
                responses.append("No fresh articles right now \u2014 everything\u2019s already been posted!")
        except Exception as e:
            responses.append(f"Error fetching news: {e}")

    else:
        responses.append(f"Unknown command: {text}\nSend /help to see available commands.")

    for r in responses:
        send_telegram(bot_token, chat_id, r)
    return responses


def run_listener(bot_token: str, groq_api_key: str) -> None:
    requests.get(f"https://api.telegram.org/bot{bot_token}/deleteWebhook", timeout=10)
    requests.post(f"https://api.telegram.org/bot{bot_token}/setMyCommands", json={
        "commands": [
            {"command": "start", "description": "Welcome message"},
            {"command": "full_digest", "description": "Full daily roundup: crypto, AI, defi"},
            {"command": "summarized_digest", "description": "Tweet-length summary of today"},
            {"command": "digest", "description": "Alias for full_digest"},
            {"command": "latest", "description": "Get the latest news right now"},
            {"command": "report", "description": "Deep dive on defi, ai, or crypto"},
            {"command": "status", "description": "Bot stats and info"},
            {"command": "help", "description": "Show available commands"},
        ]
    }, timeout=10)
    print("Listener started \u2014 polling RSS feeds...")

    state = load_json(STATE_DB) if os.path.exists(STATE_DB) else {}
    if not isinstance(state, dict):
        state = {}
    last_update_id = state.get("last_update_id", 0)
    last_news_time = 0
    last_chat_id = state.get("last_chat_id")
    news_interval = 7200

    while True:
        try:
            offset = last_update_id + 1
            resp = requests.get(
                f"https://api.telegram.org/bot{bot_token}/getUpdates?offset={offset}&timeout=30",
                timeout=35,
            )
            data = resp.json()
            if data.get("ok") and data.get("result"):
                for update in data["result"]:
                    uid = update["update_id"]
                    msg = update.get("message", {})
                    cid = str(msg.get("chat", {}).get("id", ""))
                    txt = msg.get("text", "").strip()
                    name = msg.get("from", {}).get("first_name", "")

                    if cid and txt and txt.startswith("/"):
                        need_news = any(
                            kw in txt.lower() for kw in ["/latest", "/next", "/start"]
                        )
                        handle_command(bot_token, cid, txt, name, groq_api_key)
                        if need_news:
                            msg_id = post_news(bot_token, cid, groq_api_key)
                            if msg_id:
                                last_news_time = time.time()

                    if cid and uid > last_update_id:
                        last_chat_id = cid
                        last_update_id = uid

                state["last_update_id"] = last_update_id
                state["last_chat_id"] = last_chat_id
                save_json(STATE_DB, state)

            if last_chat_id and time.time() - last_news_time >= news_interval:
                try:
                    msg_id = post_news(bot_token, last_chat_id, groq_api_key)
                    if msg_id:
                        last_news_time = time.time()
                        print(f"Scheduled post: {msg_id}")
                except Exception as e:
                    print(f"Scheduled post failed: {e}", file=sys.stderr)
                last_news_time = time.time()

        except requests.exceptions.Timeout:
            pass
        except Exception as e:
            print(f"Poll error: {e}", file=sys.stderr)
            time.sleep(10)


def main() -> None:
    for var in ("TELEGRAM_BOT_TOKEN", "GROQ_API_KEY"):
        if var not in os.environ:
            print(f"Missing required env var: {var}", file=sys.stderr)
            sys.exit(1)

    bot_token = os.environ["TELEGRAM_BOT_TOKEN"]
    groq_api_key = os.environ["GROQ_API_KEY"]

    if "--listen" in sys.argv:
        run_listener(bot_token, groq_api_key)
    else:
        run_cron(bot_token, groq_api_key)


def run_cron(bot_token: str, groq_api_key: str) -> None:
    """Scheduled run: process any pending commands first, then post news."""
    state = load_json(STATE_DB) if os.path.exists(STATE_DB) else {}
    if not isinstance(state, dict):
        state = {}

    offset = state.get("last_update_id", 0) + 1
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{bot_token}/getUpdates?offset={offset}&timeout=5",
            timeout=10,
        )
        data = resp.json()
        if data.get("ok") and data.get("result"):
            last_id = state.get("last_update_id", 0)
            for update in data["result"]:
                uid = update["update_id"]
                msg = update.get("message", {})
                cid = str(msg.get("chat", {}).get("id", ""))
                txt = msg.get("text", "").strip()
                first_name = msg.get("from", {}).get("first_name", "")
                if cid and txt:
                    handle_command(bot_token, cid, txt, first_name, groq_api_key)
                if uid > last_id:
                    last_id = uid
            state["last_update_id"] = last_id
            save_json(STATE_DB, state)
    except Exception as e:
        print(f"Command check failed: {e}", file=sys.stderr)

    state = load_json(STATE_DB) if os.path.exists(STATE_DB) else {}
    if not isinstance(state, dict):
        state = {}
    last_chat_id = state.get("last_chat_id")

    if not last_chat_id:
        try:
            resp = requests.get(f"https://api.telegram.org/bot{bot_token}/getUpdates", timeout=10)
            data = resp.json()
            if data.get("ok") and data.get("result"):
                last_chat_id = str(data["result"][-1]["message"]["chat"]["id"])
                state["last_chat_id"] = last_chat_id
                save_json(STATE_DB, state)
        except Exception:
            pass

    if not last_chat_id:
        print("No messages yet \u2014 message the bot on Telegram to start receiving updates")
        sys.exit(0)

    try:
        msg_id = post_news(bot_token, last_chat_id, groq_api_key)
        if msg_id:
            print(f"Posted \u2014 message ID: {msg_id}")
        else:
            print("All articles already posted; nothing to do")
    except Exception as e:
        print(f"News post failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
