import os, re, json, sqlite3, html, time, requests
from datetime import datetime, date
from typing import Optional, Dict, Any, List

import feedparser
from bs4 import BeautifulSoup
from telegram import Bot
from telegram.constants import ParseMode

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN") or ""
TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
MAX_POSTS_PER_RUN = int(os.getenv("MAX_POSTS_PER_RUN", "5"))
MAX_POSTS_PER_DAY = int(os.getenv("MAX_POSTS_PER_DAY", "80"))

DB_PATH = "zoom_news.db"
SOURCES_PATH = "sources.json"
bot = Bot(token=TELEGRAM_BOT_TOKEN)

def clean_html(text: str) -> str:
    if not text: return ""
    soup = BeautifulSoup(text, "html.parser")
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()

def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS posted (url TEXT PRIMARY KEY, title TEXT, posted_at TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS daily_count (day TEXT PRIMARY KEY, count INTEGER)")
    con.commit(); con.close()

def already_posted(url: str) -> bool:
    con = sqlite3.connect(DB_PATH); cur = con.cursor()
    cur.execute("SELECT 1 FROM posted WHERE url=?", (url,))
    ok = cur.fetchone() is not None
    con.close()
    return ok

def mark_posted(url: str, title: str):
    con = sqlite3.connect(DB_PATH); cur = con.cursor()
    today = date.today().isoformat()
    cur.execute("INSERT OR IGNORE INTO posted(url,title,posted_at) VALUES(?,?,?)", (url,title,datetime.utcnow().isoformat()))
    cur.execute("INSERT OR IGNORE INTO daily_count(day,count) VALUES(?,0)", (today,))
    cur.execute("UPDATE daily_count SET count=count+1 WHERE day=?", (today,))
    con.commit(); con.close()

def today_count() -> int:
    con = sqlite3.connect(DB_PATH); cur = con.cursor()
    cur.execute("SELECT count FROM daily_count WHERE day=?", (date.today().isoformat(),))
    row = cur.fetchone(); con.close()
    return row[0] if row else 0

def extract_image(entry: Dict[str, Any]) -> Optional[str]:
    media = entry.get("media_content") or entry.get("media_thumbnail") or []
    if media and isinstance(media, list) and media[0].get("url"):
        return media[0]["url"]
    soup = BeautifulSoup(entry.get("summary",""), "html.parser")
    img = soup.find("img")
    return img.get("src") if img else None

def load_sources():
    with open(SOURCES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def category_for(title, summary, default, keywords):
    txt = (title + " " + summary).lower()
    if any(k.lower() in txt for k in keywords.get("migration", [])): return "مهاجرت و ویزا"
    if any(k.lower() in txt for k in keywords.get("market", [])): return "اقتصاد و بازار"
    if any(k.lower() in txt for k in keywords.get("tech", [])): return "تکنولوژی"
    return default

def score_news(title, summary, category):
    txt = (title + " " + summary).lower()
    keys = ["iran","ایران","america","آمریکا","israel","اسرائیل","war","جنگ","attack","حمله","sanction","تحریم","nuclear","هسته‌ای","visa","ویزا","schengen","شینگن","dollar","دلار","gold","طلا","openai","chatgpt","apple","samsung","هوش مصنوعی","موبایل","breaking","فوری"]
    s = sum(2 for k in keys if k in txt)
    if category in ["مهاجرت و ویزا","اقتصاد و بازار","تکنولوژی"]: s += 3
    if len(summary) > 90: s += 1
    return s

def tags(category):
    return {
        "مهاجرت و ویزا": "#مهاجرت #ویزا #شینگن",
        "اقتصاد و بازار": "#اقتصاد #دلار #طلا #تتر",
        "تکنولوژی": "#تکنولوژی #هوش_مصنوعی #موبایل",
        "جهان": "#جهان #خبر",
        "ایران و جهان": "#ایران #جهان",
        "داخلی": "#ایران",
    }.get(category, "#خبر")

def gemini_translate(title, summary, source, category, url):
    if not GEMINI_API_KEY:
        return None
    prompt = f"""
تو سردبیر حرفه‌ای کانال تلگرام فارسی Zoom News هستی.
خبر زیر را فارسی‌سازی کن، نه ترجمه ماشینی.

قوانین:
- خروجی فقط فارسی باشد.
- تیتر جذاب و دقیق فارسی بده.
- خلاصه ۳ تا ۵ خطی بده.
- چیزی که در خبر نیست اضافه نکن.
- خبر زرد و اغراق‌آمیز نساز.
- آخر متن منبع و لینک را بنویس.
- ایموجی مناسب استفاده کن.
- هشتگ فارسی مناسب بگذار.
- از HTML استفاده نکن.

دسته: {category}
منبع: {source}
لینک: {url}
عنوان اصلی: {title}
متن اصلی: {summary}
"""
    endpoint = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
    try:
        r = requests.post(
            endpoint,
            params={"key": GEMINI_API_KEY},
            json={"contents":[{"parts":[{"text": prompt}]}], "generationConfig":{"temperature":0.35, "maxOutputTokens":700}},
            timeout=25
        )
        data = r.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        return text
    except Exception as e:
        print("Gemini error:", e)
        return None

def fallback_persian(title, summary, source, category, url):
    # بدون AI هم ظاهر فارسی و مرتب می‌ماند، ولی عنوان انگلیسی ممکن است باقی بماند.
    body = clean_html(summary)[:700]
    return f"""📌 {title}

🔹 {body}

🌍 منبع: {source}
🔗 لینک خبر:
{url}

{tags(category)}

━━━━━━━━━━━━━━
📡 @ZoomBreaking
━━━━━━━━━━━━━━"""

def make_post(title, summary, source, category, url):
    ai = gemini_translate(title, summary, source, category, url)
    if ai:
        if "@ZoomBreaking" not in ai:
            ai += "\n\n━━━━━━━━━━━━━━\n📡 @ZoomBreaking\n━━━━━━━━━━━━━━"
        return ai
    return fallback_persian(title, summary, source, category, url)

async def send(text, image_url=None):
    if image_url:
        try:
            await bot.send_photo(chat_id=TELEGRAM_CHANNEL, photo=image_url, caption=text[:1024])
            return
        except Exception as e:
            print("photo failed:", e)
    await bot.send_message(chat_id=TELEGRAM_CHANNEL, text=text[:4096], disable_web_page_preview=False)

async def main():
    if not TELEGRAM_BOT_TOKEN: raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")
    if not TELEGRAM_CHANNEL: raise RuntimeError("Missing TELEGRAM_CHANNEL")
    init_db()
    if today_count() >= MAX_POSTS_PER_DAY:
        print("Daily limit reached"); return

    data = load_sources()
    cand = []
    for src in data["rss_sources"]:
        try:
            feed = feedparser.parse(src["url"])
            for entry in feed.entries[:10]:
                url = entry.get("link","")
                title = clean_html(entry.get("title",""))
                summary = clean_html(entry.get("summary", entry.get("description","")))
                if not url or not title or already_posted(url): continue
                cat = category_for(title, summary, src.get("category","خبر"), data.get("keywords",{}))
                sc = score_news(title, summary, cat) + (4 - int(src.get("priority",3)))
                if sc >= 2:
                    cand.append((sc, src, entry, title, summary, url, cat))
        except Exception as e:
            print("source error:", src.get("name"), e)

    cand.sort(key=lambda x: x[0], reverse=True)
    slots = min(MAX_POSTS_PER_RUN, MAX_POSTS_PER_DAY - today_count())
    for _, src, entry, title, summary, url, cat in cand[:slots]:
        post = make_post(title, summary, src["name"], cat, url)
        img = extract_image(entry)
        await send(post, img)
        mark_posted(url, title)
        print("Posted:", title)
        time.sleep(2)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
