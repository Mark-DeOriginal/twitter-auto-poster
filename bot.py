import json
import os
import sys
from datetime import datetime, timezone

import requests
import tweepy
from groq import Groq

POSTED_DB = "posted_articles.json"
NEWS_API_URL = "https://newsapi.org/v2/everything"
MAX_TWEET_LENGTH = 260


def load_posted_urls() -> set:
    try:
        with open(POSTED_DB, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {item["url"] for item in data if "url" in item}
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_article(url: str, title: str) -> None:
    posted = load_posted_urls()
    with open(POSTED_DB, "r+", encoding="utf-8") as f:
        data = json.load(f)
        if url not in posted:
            data.append({"url": url, "title": title, "posted_at": datetime.now(timezone.utc).isoformat()})
        f.seek(0)
        json.dump(data, f, indent=2)
        f.truncate()


def fetch_latest_news(api_key: str) -> list[dict]:
    params = {
        "q": "crypto OR finance OR technology OR bitcoin OR ethereum OR stock OR market OR AI",
        "pageSize": 5,
        "sortBy": "publishedAt",
        "language": "en",
        "apiKey": api_key,
    }
    resp = requests.get(NEWS_API_URL, params=params, timeout=15)
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("status") != "ok":
        raise RuntimeError(f"NewsAPI error: {payload.get('message', 'unknown')}")
    articles = payload.get("articles", [])
    seen = set()
    deduped = []
    for a in articles:
        url = a.get("url", "").strip()
        title = a.get("title", "").strip()
        if not url or not title or url in seen:
            continue
        seen.add(url)
        deduped.append(a)
    return deduped


def pick_unposted_article(articles: list[dict], posted: set) -> dict | None:
    for a in articles:
        if a["url"] not in posted:
            return a
    return None


def rewrite_headline(groq_client: Groq, title: str, url: str) -> str:
    system_prompt = (
        "You are a crypto-native observer. Rewrite raw news headlines into ultra-clean, "
        "observational takes.\n"
        "Rules:\n"
        "- Never use hashtags, generic emojis, robotic introductory phrases "
        "(like 'Here is an update:'), or corporate marketing buzzwords.\n"
        "- Keep the output to a single, concise sentence or thought under 250 characters.\n"
        "- Append the source URL at the very end of the final text."
    )
    user_prompt = f"Headline: {title}\nSource URL: {url}"
    resp = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.7,
        max_tokens=150,
    )
    return resp.choices[0].message.content.strip()


def post_tweet(client: tweepy.Client, text: str) -> str:
    if len(text) > MAX_TWEET_LENGTH:
        text = text[: MAX_TWEET_LENGTH - 3].rstrip() + "..."
    resp = client.create_tweet(text=text)
    tweet_id = resp.data["id"]
    return tweet_id


def main() -> None:
    for var in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET",
                "NEWS_API_KEY", "GROQ_API_KEY"):
        if var not in os.environ:
            print(f"Missing required env var: {var}", file=sys.stderr)
            sys.exit(1)

    news_api_key = os.environ["NEWS_API_KEY"]
    groq_api_key = os.environ["GROQ_API_KEY"]

    posted = load_posted_urls()
    print(f"Already posted: {len(posted)} URLs")

    try:
        articles = fetch_latest_news(news_api_key)
    except Exception as e:
        print(f"Failed to fetch news: {e}", file=sys.stderr)
        sys.exit(1)

    if not articles:
        print("No articles returned from NewsAPI")
        sys.exit(0)

    article = pick_unposted_article(articles, posted)
    if article is None:
        print("All articles already posted; nothing to do")
        sys.exit(0)

    title = article["title"]
    url = article["url"]
    source = article.get("source", {}).get("name", "News")
    print(f"Selected: [{source}] {title}")

    groq_client = Groq(api_key=groq_api_key)
    try:
        rewritten = rewrite_headline(groq_client, title, url)
    except Exception as e:
        print(f"Groq API call failed: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Rewritten: {rewritten}")

    tweepy_client = tweepy.Client(
        consumer_key=os.environ["X_API_KEY"],
        consumer_secret=os.environ["X_API_SECRET"],
        access_token=os.environ["X_ACCESS_TOKEN"],
        access_token_secret=os.environ["X_ACCESS_SECRET"],
    )
    try:
        tweet_id = post_tweet(tweepy_client, rewritten)
    except Exception as e:
        print(f"Failed to post tweet: {e}", file=sys.stderr)
        sys.exit(1)

    save_article(url, title)
    print(f"Posted successfully — tweet ID: {tweet_id}")


if __name__ == "__main__":
    main()
