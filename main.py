import os
import re
import json
import sqlite3
import hashlib
from datetime import datetime, date
from typing import Dict, Any, Optional, List

import feedparser
from bs4 import BeautifulSoup
from telegram import Bot
from telegram.constants import ParseMode

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN") or ""
TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MAX_POSTS_PER_RUN = int(os.getenv("MAX_POSTS_PER_RUN", "6"))
MAX_POSTS_PER_DAY = int(os.getenv("MAX_POSTS_PER_DAY", "90"))

DB_PATH = "zoom_news.db"
SOURCES_PATH = "sources.json"

bot = Bot(token=TELEGRAM_BOT_TOKEN)
client = OpenAI(api_key=OPENAI_API_KEY) if OpenAI and OPENAI_API_KEY else None


def init_db():
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


def clean_html(text: str) -> str:
    if not text:
        return ""
    soup = BeautifulSoup(text, "html.parser")
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()


def already_posted(url: str) -> bool:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT 1 FROM posted WHERE url=?", (url,))
    ok = cur.fetchone() is not None
    con.close()
    return ok


def mark_posted(url: str, title: str):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    today = date.today().isoformat()
    cur.execute("INSERT OR IGNORE INTO posted(url,title,posted_at) VALUES(?,?,?)", (url, title, datetime.utcnow().isoformat()))
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


def load_sources():
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
    return img.get("src") if img else None


def classify_category(title: str, summary: str, default_category: str, keywords: Dict[str, List[str]]) -> str:
    text = f"{title} {summary}".lower()
    if any(k.lower() in text for k in keywords.get("migration", [])):
        return "مهاجرت و ویزا"
    if any(k.lower() in text for k in keywords.get("market", [])):
        return "اقتصاد و بازار"
    if any(k.lower() in text for k in keywords.get("tech", [])):
        return "تکنولوژی"
    return default_category


def importance_score(title: str, summary: str, category: str) -> int:
    text = f"{title} {summary}".lower()
    hot = [
        "iran", "ایران", "israel", "gaza", "war", "attack", "sanction", "nuclear",
        "آمریکا", "اسرائیل", "جنگ", "حمله", "تحریم", "هسته‌ای", "فوری", "breaking",
        "dollar", "gold", "visa", "schengen", "openai", "chatgpt", "apple", "samsung",
        "دلار", "طلا", "ویزا", "شینگن", "هوش مصنوعی", "موبایل"
    ]
    score = 0
    for k in hot:
        if k in text:
            score += 2
    if category in ["مهاجرت و ویزا", "اقتصاد و بازار", "تکنولوژی"]:
        score += 2
    if len(summary) > 80:
        score += 1
    return score


def hashtags(category: str) -> str:
    m = {
        "مهاجرت و ویزا": "#مهاجرت #ویزا #شینگن",
        "اقتصاد و بازار": "#اقتصاد #دلار #طلا #تتر",
        "تکنولوژی": "#تکنولوژی #هوش_مصنوعی #موبایل",
        "جهان": "#جهان #خبر",
        "ایران و جهان": "#ایران #جهان",
        "داخلی": "#ایران",
    }
    return m.get(category, "#خبر")


def ai_rewrite(title: str, summary: str, source: str, category: str, url: str) -> str:
    if client:
        prompt = f"""
تو سردبیر حرفه‌ای کانال تلگرام Zoom News هستی.
خبر را به فارسی روان، جذاب، دقیق و کوتاه آماده انتشار کن.

قوانین:
- اگر خبر انگلیسی است ترجمه طبیعی و حرفه‌ای فارسی بده.
- اگر فارسی است، بازنویسی تمیز و خلاصه بده.
- خبر زرد نساز و چیزی اضافه نکن.
- خروجی ۴ تا ۷ خط باشد.
- ایموجی مناسب بگذار.
- منبع را ذکر کن.
- در پایان هشتگ مناسب بده.
- خروجی فقط متن آماده تلگرام باشد.

دسته: {category}
منبع: {source}
لینک: {url}
تیتر: {title}
متن: {summary}
"""
        try:
            r = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.35,
                max_tokens=650,
            )
            return r.choices[0].message.content.strip()
        except Exception as e:
            print("AI error:", e)

    icon = "🚨" if "فوری" in title or category in ["ایران و جهان", "جهان"] else "📌"
    sm = clean_html(summary)[:700]
    return f"""{icon} <b>{title}</b>

📌 {sm}

🌍 منبع: {source}
🔗 لینک خبر: {url}

{hashtags(category)}

━━━━━━━━━━━━━━
📡 <b>Zoom News</b>
━━━━━━━━━━━━━━"""


async def send_post(text: str, image_url: Optional[str] = None):
    if image_url:
        try:
            await bot.send_photo(chat_id=TELEGRAM_CHANNEL, photo=image_url, caption=text[:1024], parse_mode=ParseMode.HTML)
            return
        except Exception as e:
            print("send_photo failed:", e)
    await bot.send_message(chat_id=TELEGRAM_CHANNEL, text=text[:4096], parse_mode=ParseMode.HTML, disable_web_page_preview=False)


async def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_TOKEN")
    if not TELEGRAM_CHANNEL:
        raise RuntimeError("Missing TELEGRAM_CHANNEL")

    init_db()
    data = load_sources()
    sources = data["rss_sources"]
    keywords = data.get("keywords", {})
    candidates = []

    if today_count() >= MAX_POSTS_PER_DAY:
        print("Daily limit reached")
        return

    for src in sources:
        try:
            feed = feedparser.parse(src["url"])
            for entry in feed.entries[:10]:
                url = entry.get("link", "")
                title = clean_html(entry.get("title", ""))
                summary = clean_html(entry.get("summary", entry.get("description", "")))
                if not url or not title or already_posted(url):
                    continue
                category = classify_category(title, summary, src.get("category", "خبر"), keywords)
                score = importance_score(title, summary, category) + (4 - int(src.get("priority", 3)))
                if score >= 2:
                    candidates.append((score, src, entry, title, summary, url, category))
        except Exception as e:
            print("source error:", src.get("name"), e)

    candidates.sort(key=lambda x: x[0], reverse=True)
    slots = min(MAX_POSTS_PER_RUN, MAX_POSTS_PER_DAY - today_count())

    for _, src, entry, title, summary, url, category in candidates[:slots]:
        text = ai_rewrite(title, summary, src["name"], category, url)
        image_url = extract_image(entry)
        await send_post(text, image_url)
        mark_posted(url, title)
        print("Posted:", title)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
