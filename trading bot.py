"""
🤖 AI Trading Bot - Telegram Professional Edition
مدعوم بالذكاء الاصطناعي للسوق الأمريكي
"""

import os
import logging
import asyncio
import aiohttp
import json
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)

# ══════════════════════════════════════════════
# CONFIG  — ضع مفاتيحك هنا أو في متغيرات البيئة
# ══════════════════════════════════════════════
TELEGRAM_BOT_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN")
ANTHROPIC_API_KEY    = os.getenv("ANTHROPIC_API_KEY",  "YOUR_ANTHROPIC_KEY")
FINNHUB_API_KEY      = os.getenv("FINNHUB_API_KEY",    "YOUR_FINNHUB_KEY")   # مجاني على finnhub.io
ALPHA_VANTAGE_KEY    = os.getenv("ALPHA_VANTAGE_KEY",  "YOUR_AV_KEY")        # مجاني على alphavantage.co

ALLOWED_USERS: list[int] = []   # اتركها فارغة للسماح للجميع، أو أضف IDs مثل [123456, 789012]

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# ══════════════════════════════════════════════
# HELPERS — Market Data
# ══════════════════════════════════════════════

async def get_quote(symbol: str) -> dict:
    """سعر لحظي + تغيير اليوم عبر Finnhub"""
    url = f"https://finnhub.io/api/v1/quote?symbol={symbol}&token={FINNHUB_API_KEY}"
    async with aiohttp.ClientSession() as s:
        async with s.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
            data = await r.json()
    # c=current, h=high, l=low, o=open, pc=prev_close, dp=change%
    return data


async def get_technicals(symbol: str) -> dict:
    """RSI + MACD + SMA via Alpha Vantage"""
    results = {}
    indicators = {
        "RSI":  f"https://www.alphavantage.co/query?function=RSI&symbol={symbol}&interval=daily&time_period=14&series_type=close&apikey={ALPHA_VANTAGE_KEY}",
        "MACD": f"https://www.alphavantage.co/query?function=MACD&symbol={symbol}&interval=daily&series_type=close&apikey={ALPHA_VANTAGE_KEY}",
        "SMA50":f"https://www.alphavantage.co/query?function=SMA&symbol={symbol}&interval=daily&time_period=50&series_type=close&apikey={ALPHA_VANTAGE_KEY}",
        "SMA200":f"https://www.alphavantage.co/query?function=SMA&symbol={symbol}&interval=daily&time_period=200&series_type=close&apikey={ALPHA_VANTAGE_KEY}",
    }
    async with aiohttp.ClientSession() as session:
        for key, url in indicators.items():
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    results[key] = await resp.json()
            except Exception as e:
                results[key] = {"error": str(e)}
    return results


def parse_technicals(raw: dict, symbol: str) -> dict:
    """استخراج آخر قيم المؤشرات"""
    out = {}
    try:
        rsi_series = raw["RSI"].get("Technical Analysis: RSI", {})
        latest_rsi_date = sorted(rsi_series.keys(), reverse=True)[0]
        out["RSI"] = float(rsi_series[latest_rsi_date]["RSI"])
    except Exception:
        out["RSI"] = None

    try:
        macd_series = raw["MACD"].get("Technical Analysis: MACD", {})
        latest_macd_date = sorted(macd_series.keys(), reverse=True)[0]
        out["MACD"] = float(macd_series[latest_macd_date]["MACD"])
        out["MACD_Signal"] = float(macd_series[latest_macd_date]["MACD_Signal"])
        out["MACD_Hist"] = float(macd_series[latest_macd_date]["MACD_Hist"])
    except Exception:
        out["MACD"] = out["MACD_Signal"] = out["MACD_Hist"] = None

    try:
        sma50_series = raw["SMA50"].get("Technical Analysis: SMA", {})
        latest_sma50 = sorted(sma50_series.keys(), reverse=True)[0]
        out["SMA50"] = float(sma50_series[latest_sma50]["SMA"])
    except Exception:
        out["SMA50"] = None

    try:
        sma200_series = raw["SMA200"].get("Technical Analysis: SMA", {})
        latest_sma200 = sorted(sma200_series.keys(), reverse=True)[0]
        out["SMA200"] = float(sma200_series[latest_sma200]["SMA"])
    except Exception:
        out["SMA200"] = None

    return out


# ══════════════════════════════════════════════
# CLAUDE AI ANALYSIS
# ══════════════════════════════════════════════

async def claude_analysis(symbol: str, quote: dict, tech: dict) -> str:
    """تحليل ذكاء اصطناعي كامل عبر Claude"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    system_prompt = """أنت محلل مالي خبير متخصص في السوق الأمريكي.
تحليلك دقيق، محترف، وعملي.
الرد دائماً بالعربية ومنسق بوضوح.
في نهاية كل تحليل أعطِ:
- توصية واضحة: شراء / بيع / انتظار
- نقطة دخول مقترحة
- هدف الربح (TP)
- وقف الخسارة (SL)
- نسبة المخاطرة/المكافأة (R:R)"""

    user_content = f"""
تحليل سهم {symbol} - {now}

📊 بيانات السوق:
- السعر الحالي: ${quote.get('c', 'N/A')}
- الافتتاح: ${quote.get('o', 'N/A')}
- أعلى اليوم: ${quote.get('h', 'N/A')}
- أدنى اليوم: ${quote.get('l', 'N/A')}
- إغلاق الأمس: ${quote.get('pc', 'N/A')}
- التغيير: {quote.get('dp', 'N/A')}%

📈 المؤشرات التقنية:
- RSI (14): {tech.get('RSI')}
- MACD: {tech.get('MACD')} | Signal: {tech.get('MACD_Signal')} | Hist: {tech.get('MACD_Hist')}
- SMA50: ${tech.get('SMA50')}
- SMA200: ${tech.get('SMA200')}

أعطني تحليلاً شاملاً وتوصية تداول واضحة.
"""

    payload = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1000,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_content}]
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            data = await resp.json()

    return data["content"][0]["text"]


# ══════════════════════════════════════════════
# TELEGRAM HANDLERS
# ══════════════════════════════════════════════

def is_allowed(user_id: int) -> bool:
    return not ALLOWED_USERS or user_id in ALLOWED_USERS


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ غير مصرح لك باستخدام هذا البوت.")
        return

    keyboard = [
        [InlineKeyboardButton("📊 تحليل سهم", callback_data="analyze")],
        [InlineKeyboardButton("🔥 أفضل الأسهم اليوم", callback_data="top_picks")],
        [InlineKeyboardButton("📖 كيف أستخدم البوت؟", callback_data="help")],
    ]
    text = (
        "🤖 *مرحباً بك في بوت التداول الذكي*\n\n"
        "أنا محلل تداول مدعوم بالذكاء الاصطناعي 🧠\n"
        "متخصص في السوق الأمريكي 🇺🇸\n\n"
        "اختر من القائمة أو أرسل رمز السهم مباشرة\n"
        "مثال: `AAPL` أو `TSLA` أو `NVDA`"
    )
    await update.message.reply_text(
        text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "analyze":
        await query.edit_message_text(
            "📊 أرسل لي رمز السهم الذي تريد تحليله\n\n"
            "أمثلة: `AAPL` `MSFT` `TSLA` `NVDA` `AMZN` `META`",
            parse_mode="Markdown"
        )

    elif query.data == "top_picks":
        await query.edit_message_text("⏳ جاري تحليل أفضل الأسهم...")
        top = ["NVDA", "AAPL", "MSFT", "META", "TSLA"]
        results = []
        for sym in top:
            try:
                q = await get_quote(sym)
                chg = q.get("dp", 0)
                price = q.get("c", 0)
                arrow = "🟢" if chg >= 0 else "🔴"
                results.append(f"{arrow} *{sym}* — ${price:.2f} ({chg:+.2f}%)")
            except Exception:
                results.append(f"⚠️ {sym} — خطأ في جلب البيانات")
        msg = "🔥 *أبرز الأسهم الأمريكية الآن*\n\n" + "\n".join(results)
        msg += "\n\n_أرسل رمز أي سهم لتحليل عميق بالذكاء الاصطناعي_"
        await query.edit_message_text(msg, parse_mode="Markdown")

    elif query.data == "help":
        help_text = (
            "📖 *كيف تستخدم البوت*\n\n"
            "1️⃣ أرسل رمز السهم مثل `AAPL`\n"
            "2️⃣ البوت يجلب السعر اللحظي والمؤشرات\n"
            "3️⃣ يحللها بالذكاء الاصطناعي\n"
            "4️⃣ يعطيك توصية: دخول / هدف / وقف خسارة\n\n"
            "*المؤشرات المستخدمة:*\n"
            "• RSI — قوة الزخم\n"
            "• MACD — اتجاه الزخم\n"
            "• SMA50/200 — الاتجاه العام\n\n"
            "⚠️ *تنبيه:* هذا البوت للأغراض التعليمية.\n"
            "التداول ينطوي على مخاطر. استشر خبيراً ماليًا."
        )
        await query.edit_message_text(help_text, parse_mode="Markdown")


async def analyze_symbol(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return

    symbol = update.message.text.strip().upper()

    # قبول فقط رموز نظيفة
    if not symbol.isalpha() or len(symbol) > 6:
        await update.message.reply_text(
            "⚠️ أرسل رمز سهم صحيح مثل `AAPL` أو `TSLA`",
            parse_mode="Markdown"
        )
        return

    msg = await update.message.reply_text(
        f"🔍 جاري تحليل *{symbol}* ...\n⏳ قد يستغرق حتى 30 ثانية",
        parse_mode="Markdown"
    )

    try:
        quote, raw_tech = await asyncio.gather(
            get_quote(symbol),
            get_technicals(symbol)
        )

        if not quote.get("c"):
            await msg.edit_text(f"❌ لم أجد بيانات للسهم `{symbol}`. تأكد من الرمز.")
            return

        tech = parse_technicals(raw_tech, symbol)
        analysis = await claude_analysis(symbol, quote, tech)

        header = (
            f"📊 *تحليل {symbol}* — {datetime.now(timezone.utc).strftime('%H:%M UTC')}\n"
            f"💵 السعر: `${quote.get('c', 'N/A')}` | "
            f"{'🟢' if float(quote.get('dp',0))>=0 else '🔴'} {quote.get('dp','N/A')}%\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        )

        keyboard = [[InlineKeyboardButton("🔄 تحديث التحليل", callback_data=f"refresh_{symbol}")]]

        await msg.edit_text(
            header + analysis,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        log.error(f"Error analyzing {symbol}: {e}")
        await msg.edit_text(
            f"❌ حدث خطأ أثناء التحليل.\nتفاصيل: `{e}`",
            parse_mode="Markdown"
        )


async def refresh_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("⏳ جاري التحديث...")
    symbol = query.data.split("_", 1)[1]

    try:
        quote, raw_tech = await asyncio.gather(
            get_quote(symbol),
            get_technicals(symbol)
        )
        tech = parse_technicals(raw_tech, symbol)
        analysis = await claude_analysis(symbol, quote, tech)

        header = (
            f"📊 *تحليل {symbol}* — {datetime.now(timezone.utc).strftime('%H:%M UTC')} 🔄\n"
            f"💵 السعر: `${quote.get('c', 'N/A')}` | "
            f"{'🟢' if float(quote.get('dp',0))>=0 else '🔴'} {quote.get('dp','N/A')}%\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        )

        keyboard = [[InlineKeyboardButton("🔄 تحديث التحليل", callback_data=f"refresh_{symbol}")]]
        await query.edit_message_text(
            header + analysis,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        await query.edit_message_text(f"❌ خطأ في التحديث: `{e}`", parse_mode="Markdown")


# ══════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════

def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",  start))
    app.add_handler(CommandHandler("help",   start))
    app.add_handler(CallbackQueryHandler(refresh_handler, pattern=r"^refresh_"))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, analyze_symbol))

    log.info("🤖 Trading Bot is running...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
