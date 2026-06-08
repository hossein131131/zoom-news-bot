# Zoom News AI Bot

ربات اتوماتیک برای کانال تلگرام Zoom News:
- دریافت خبر از RSS منابع معتبر
- ترجمه و خلاصه‌سازی فارسی با AI
- ارسال خودکار به کانال
- کنترل تعداد پست روزانه
- جلوگیری از خبر تکراری
- قالب‌بندی شیک با ایموجی

## راه‌اندازی سریع روی Render

1. داخل GitHub یک Repository جدید بساز.
2. این فایل‌ها را داخلش آپلود کن.
3. برو Render.com و New Web Service بساز.
4. Repository را وصل کن.
5. تنظیمات:
   - Build Command:
     `pip install -r requirements.txt`
   - Start Command:
     `python main.py`
6. در بخش Environment این‌ها را بگذار:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHANNEL`
   - `OPENAI_API_KEY`
   - `MAX_POSTS_PER_DAY=80`
   - `CHECK_INTERVAL_MINUTES=10`

## نکته امنیتی
توکن ربات را هیچ‌وقت داخل چت یا اسکرین‌شات نفرست. اگر لو رفت، در BotFather دستور `/revoke` بزن.

## تست
بعد از اجرا، ربات هر چند دقیقه منابع را چک می‌کند و خبرهای مهم را در کانال منتشر می‌کند.
