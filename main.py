import os
import re
import json
import time
import sqlite3
import logging
from datetime import datetime, date
from typing import Dict, Any, Optional, List

import feedparser
import httpx
from bs4 import BeautifulSoup
from apscheduler.schedulers.background import BackgroundScheduler
from telegram import Bot
from telegram.constants import ParseMode
from dotenv import load_dotenv

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MAX_POSTS_PER_DAY = int(os.getenv("MAX_POSTS_PER_DAY", "80"))
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "10"))

DB_PATH = "zoom_news.db"
SOURCES_PATH = "sources.json"

bot = Bot(token=TELEGRAM_BOT_TOKEN)
client = OpenAI(api_key=OPENAI_API_KEY) if OpenAI and OPENAI_API_KEY else None


def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS posted (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE,
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


def mark_posted(url: str, title: str):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("INSERT OR IGNORE INTO posted(url,title,posted_at) VALUES(?,?,?)",
                (url, title, datetime.utcnow().isoformat()))
    today = date.today().isoformat()
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


def clean_html(text: str) -> str:
    if not text:
        return ""
    soup = BeautifulSoup(text, "html.parser")
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()


def load_sources() -> List[Dict[str, Any]]:
    with open(SOURCES_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["rss_sources"]


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
    summary = entry.get("summary", "")
    soup = BeautifulSoup(summary, "html.parser")
    img = soup.find("img")
    return img.get("src") if img else None


def importance_score(title: str, summary: str, category: str) -> int:
    text = f"{title} {summary}".lower()
    keywords = [
        "iran", "ایران", "trump", "biden", "america", "israel", "gaza", "war",
        "تحریم", "آمریکا", "اسرائیل", "جنگ", "دلار", "طلا", "visa", "schengen",
        "مهاجرت", "ویزای", "شینگن", "openai", "chatgpt", "apple", "samsung",
        "bitcoin", "tether", "oil", "nuclear", "هسته‌ای", "فوری", "breaking"
    ]
    score = 0
    for k in keywords:
        if k in text:
            score += 2
    if category in ["مهاجرت", "تکنولوژی"]:
        score += 1
    return score


def ai_rewrite(title: str, summary: str, source: str, category: str, url: str) -> str:
    base_text = f"Title: {title}\nSummary: {summary}\nSource: {source}\nCategory: {category}\nURL: {url}"

    if client:
        prompt = f"""
تو دبیر حرفه‌ای یک کانال خبری فارسی به نام Zoom News هستی.
خبر زیر را به فارسی روان، جذاب و دقیق بازنویسی کن.

قوانین:
- اگر متن انگلیسی است، ترجمه طبیعی فارسی بده.
- اگر فارسی است، مرتب و حرفه‌ای بازنویسی کن.
- از شایعه‌سازی و اضافه کردن اطلاعات ناموجود خودداری کن.
- متن کامل کپی‌شده تولید نکن؛ خلاصه خبری ۳ تا ۶ خطی بده.
- تیتر جذاب ولی غیرزرد بساز.
- ایموجی مناسب استفاده کن.
- در پایان منبع را ذکر کن.
- هشتگ مناسب اضافه کن.
- خروجی فقط متن آماده ارسال تلگرام باشد.

خبر:
{base_text}
"""
        try:
            res = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.35,
                max_tokens=700,
            )
            return res.choices[0].message.content.strip()
        except Exception as e:
            logging.warning(f"AI failed: {e}")

    emoji = "🚨" if category in ["ایران و جهان", "جهان"] else "📌"
    hashtags = {
        "تکنولوژی": "#تکنولوژی #هوش_مصنوعی",
        "جهان": "#جهان #خبر",
        "ایران و جهان": "#ایران #جهان",
        "داخلی": "#ایران",
        "مهاجرت": "#مهاجرت #شینگن",
    }.get(category, "#خبر")
    summary = clean_html(summary)[:650]
    return f"""{emoji} <b>{title}</b>

📌 {summary}

🌍 منبع: {source}
🔗 لینک خبر: {url}

{hashtags}

━━━━━━━━━━━━━━
📡 <b>Zoom News</b>
━━━━━━━━━━━━━━"""


async def send_post(text: str, image_url: Optional[str] = None):
    if image_url:
        try:
            await bot.send_photo(
                chat_id=TELEGRAM_CHANNEL,
                photo=image_url,
                caption=text[:1024],
                parse_mode=ParseMode.HTML
            )
            return
        except Exception as e:
            logging.warning(f"send_photo failed: {e}")

    await bot.send_message(
        chat_id=TELEGRAM_CHANNEL,
        text=text[:4096],
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=False
    )


async def check_sources_once():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL:
        logging.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL missing")
        return

    if today_count() >= MAX_POSTS_PER_DAY:
        logging.info("Daily limit reached")
        return

    sources = load_sources()
    candidates = []

    for src in sources:
        try:
            feed = feedparser.parse(src["url"])
            for entry in feed.entries[:8]:
                url = entry.get("link", "")
                title = clean_html(entry.get("title", ""))
                summary = clean_html(entry.get("summary", entry.get("description", "")))
                if not url or not title or already_posted(url):
                    continue
                score = importance_score(title, summary, src.get("category", ""))
                if score >= 2:
                    candidates.append((score, src, entry, title, summary, url))
        except Exception as e:
            logging.warning(f"Source failed {src['name']}: {e}")

    candidates.sort(key=lambda x: x[0], reverse=True)
    slots = max(0, min(8, MAX_POSTS_PER_DAY - today_count()))

    for _, src, entry, title, summary, url in candidates[:slots]:
        try:
            text = ai_rewrite(title, summary, src["name"], src.get("category", "خبر"), url)
            image_url = extract_image(entry)
            await send_post(text, image_url)
            mark_posted(url, title)
            logging.info(f"Posted: {title}")
            time.sleep(4)
        except Exception as e:
            logging.error(f"Posting failed: {e}")


def run_job():
    import asyncio
    asyncio.run(check_sources_once())


def main():
    init_db()
    logging.info("Zoom News Bot started")
    run_job()
    scheduler = BackgroundScheduler()
    scheduler.add_job(run_job, "interval", minutes=CHECK_INTERVAL_MINUTES)
    scheduler.start()
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
