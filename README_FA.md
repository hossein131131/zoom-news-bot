# Zoom News Bot - GitHub Actions

این نسخه بدون Render و بدون پرداخت اجرا می‌شود.

## Secretهایی که باید در GitHub وارد شوند

در GitHub برو:
Settings → Secrets and variables → Actions → New repository secret

این‌ها را اضافه کن:

1. `TELEGRAM_BOT_TOKEN`
   مقدار: توکن ربات BotFather

2. `TELEGRAM_CHANNEL`
   مقدار: `@ZoomBreaking`

3. `OPENAI_API_KEY`
   اختیاری است؛ اگر نگذاری ربات بدون AI حرفه‌ای، خلاصه ساده می‌فرستد.

## اجرا
Actions → Zoom News Bot → Run workflow

بعد از آن هر ۱۵ دقیقه خودکار اجرا می‌شود.
