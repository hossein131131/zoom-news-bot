import os
import re
import json
import time
import sqlite3
import requests
from datetime import datetime, date
from typing import Optional, Dict, Any

import feedparser
from bs4 import BeautifulSoup
from telegram import Bot

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN") or ""
TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

MAX_POSTS_PER_RUN = int(os.getenv("MAX_POSTS_PER_RUN", "5"))
MAX_POSTS_PER_DAY = int(os.getenv("MAX_POSTS_PER_DAY", "80"))

DB_PATH = "zoom_news.db"
SOURCES_PATH = "sources.json"

bot = Bot(token=TELEGRAM_BOT_TOKEN)


def clean_html(text: str) -> str:
    if not text:
        return ""
    soup = BeautifulSoup(text, "html.parser")
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()


def init_db() -> None:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS posted (
            url TEXT PRIMARY KEY,
            title TEXT,
            posted_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_count (
            day TEXT PRIMARY KEY,
            count INTEGER
        )
    """)
    con.commit()
    con.close()


def already_posted(url: str) -> bool:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT 1 FROM posted WHERE url=?", (url,))
    exists = cur.fetchone() is not None
    con.close()
    return exists


def mark_posted(url: str, title: str) -> None:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    today = date.today().isoformat()
    cur.execute(
        "INSERT OR IGNORE INTO posted(url,title,posted_at) VALUES(?,?,?)",
        (url, title, datetime.utcnow().isoformat()),
    )
    cur.execute("INSERT OR IGNORE INTO daily_count(day,count) VALUES(?,0)", (today,))
    cur.execute("UPDATE daily_count SET count=count+1 WHERE day=?", (today,))
    con.commit()
    con.close()


def today_count() -> int:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    today = date.today().isoformat()
    cur.execute("SELECT count FROM daily_count WHERE day=?", (today,))
    row = cur.fetchone()
    con.close()
    return row[0] if row else 0


def load_sources() -> Dict[str, Any]:
    with open(SOURCES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_image(entry: Dict[str, Any]) -> Optional[str]:
    media = entry.get("media_content") or entry.get("media_thumbnail") or []
    if media and isinstance(media, list):
        url = media[0].get("url")
        if url:
            return url

    if "links" in entry:
        for link in entry.links:
            if str(link.get("type", "")).startswith("image"):
                return link.get("href")

    soup = BeautifulSoup(entry.get("summary", ""), "html.parser")
    img = soup.find("img")
    if img and img.get("src"):
        return img.get("src")

    return None


def classify_category(title: str, summary: str, default_category: str, keywords: Dict[str, list]) -> str:
    text = f"{title} {summary}".lower()

    if any(k.lower() in text for k in keywords.get("migration", [])):
        return "مهاجرت و ویزا"

    if any(k.lower() in text for k in keywords.get("market", [])):
        return "اقتصاد و بازار"

    if any(k.lower() in text for k in keywords.get("tech", [])):
        return "تکنولوژی"

    return default_category


def importance_score(title: str, summary: str, category: str, source_priority: int) -> int:
    text = f"{title} {summary}".lower()

    hot_keywords = [
        "iran", "ایران", "america", "آمریکا", "israel", "اسرائیل",
        "war", "جنگ", "attack", "حمله", "sanction", "تحریم",
        "nuclear", "هسته‌ای", "visa", "ویزا", "schengen", "شینگن",
        "dollar", "دلار", "gold", "طلا", "tether", "تتر",
        "bitcoin", "بیت‌کوین", "openai", "chatgpt", "apple", "samsung",
        "هوش مصنوعی", "موبایل", "breaking", "فوری"
    ]

    score = 0
    for k in hot_keywords:
        if k in text:
            score += 2

    if category in ["مهاجرت و ویزا", "اقتصاد و بازار", "تکنولوژی"]:
        score += 3

    if len(summary) > 80:
        score += 1

    score += max(0, 4 - int(source_priority or 3))
    return score


def fallback_post(title: str, summary: str, source: str, category: str, url: str) -> str:
    tags = {
        "مهاجرت و ویزا": "#مهاجرت #ویزا #شینگن",
        "اقتصاد و بازار": "#اقتصاد #دلار #طلا #تتر",
        "تکنولوژی": "#تکنولوژی #هوش_مصنوعی #موبایل",
        "جهان": "#جهان #خبر",
        "ایران و جهان": "#ایران #جهان",
        "داخلی": "#ایران",
    }.get(category, "#خبر")

    return f"""📌 {title}

📝 {clean_html(summary)[:700]}

🌍 منبع: {source}
🔗 لینک خبر:
{url}

{tags}

━━━━━━━━━━━━━━
📡 @ZoomBreaking
━━━━━━━━━━━━━━"""


def gemini_post(title: str, summary: str, source: str, category: str, url: str) -> Optional[str]:
    if not GEMINI_API_KEY:
        print("No GEMINI_API_KEY. Using fallback.")
        return None

    prompt = f"""
تو سردبیر حرفه‌ای کانال تلگرام فارسی «Zoom News» هستی.

خبر زیر را برای انتشار در کانال تلگرام فارسی‌سازی کن.

قوانین مهم:
- خروجی فقط فارسی باشد.
- متن انگلیسی در خروجی نیاور، مگر اسم برند یا اسم رسانه.
- خبر را ترجمه ماشینی نکن؛ فارسی روان و خبری بنویس.
- تیتر فارسی جذاب ولی دقیق بده.
- خلاصه ۳ تا ۵ خطی بده.
- چیزی که در متن خبر نیست اضافه نکن.
- اغراق و خبر زرد نساز.
- منبع را ذکر کن.
- لینک خبر را کامل بگذار.
- ایموجی مناسب استفاده کن.
- هشتگ‌های فارسی مناسب اضافه کن.
- خروجی فقط متن آماده ارسال تلگرام باشد.

قالب خروجی:
📌 تیتر فارسی

📝 خلاصه فارسی خبر

🌍 منبع: {source}
🔗 لینک خبر:
{url}

#هشتگ‌ها

━━━━━━━━━━━━━━
📡 @ZoomBreaking
━━━━━━━━━━━━━━

اطلاعات خبر:
دسته: {category}
منبع: {source}
لینک: {url}
تیتر اصلی: {title}
متن اصلی: {summary}
"""

    endpoint = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"

    try:
        response = requests.post(
            endpoint,
            params={"key": GEMINI_API_KEY},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.35,
                    "maxOutputTokens": 900
                }
            },
            timeout=35
        )

        data = response.json()

        if "error" in data:
            print("Gemini API error:", data["error"])
            return None

        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()

        if "@ZoomBreaking" not in text:
            text += "\n\n━━━━━━━━━━━━━━\n📡 @ZoomBreaking\n━━━━━━━━━━━━━━"

        return text

    except Exception as e:
        print("Gemini exception:", e)
        return None


def make_post(title: str, summary: str, source: str, category: str, url: str) -> str:
    ai_text = gemini_post(title, summary, source, category, url)
    if ai_text:
        return ai_text
    return fallback_post(title, summary, source, category, url)


async def send_post(text: str, image_url: Optional[str] = None) -> None:
    if image_url:
        try:
            await bot.send_photo(
                chat_id=TELEGRAM_CHANNEL,
                photo=image_url,
                caption=text[:1024]
            )
            return
        except Exception as e:
            print("send_photo failed:", e)

    await bot.send_message(
        chat_id=TELEGRAM_CHANNEL,
        text=text[:4096],
        disable_web_page_preview=False
    )


async def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")

    if not TELEGRAM_CHANNEL:
        raise RuntimeError("Missing TELEGRAM_CHANNEL")

    init_db()

    if today_count() >= MAX_POSTS_PER_DAY:
        print("Daily limit reached")
        return

    data = load_sources()
    sources = data.get("rss_sources", [])
    keywords = data.get("keywords", {})

    candidates = []

    for src in sources:
        try:
            feed = feedparser.parse(src["url"])

            for entry in feed.entries[:12]:
                url = entry.get("link", "")
                title = clean_html(entry.get("title", ""))
                summary = clean_html(entry.get("summary", entry.get("description", "")))

                if not url or not title:
                    continue

                if already_posted(url):
                    continue

                category = classify_category(title, summary, src.get("category", "خبر"), keywords)
                score = importance_score(title, summary, category, src.get("priority", 3))

                if score >= 2:
                    candidates.append((score, src, entry, title, summary, url, category))

        except Exception as e:
            print("Source error:", src.get("name"), e)

    candidates.sort(key=lambda x: x[0], reverse=True)

    slots = min(MAX_POSTS_PER_RUN, MAX_POSTS_PER_DAY - today_count())

    if not candidates:
        print("No new candidates found.")
        return

    for _, src, entry, title, summary, url, category in candidates[:slots]:
        post_text = make_post(title, summary, src["name"], category, url)
        image_url = extract_image(entry)

        await send_post(post_text, image_url)
        mark_posted(url, title)

        print("Posted:", title)
        time.sleep(2)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
