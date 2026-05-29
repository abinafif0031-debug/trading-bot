"""
🤖 AI Trading Bot v2.0 - Professional Scanner Edition
مراقبة السوق الأمريكي كاملاً 24/7
"""

import os, logging, asyncio, aiohttp, time
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)

# ══════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN")
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY",  "YOUR_ANTHROPIC_KEY")
FINNHUB_API_KEY    = os.getenv("FINNHUB_API_KEY",    "YOUR_FINNHUB_KEY")

ALERT_CHAT_IDS: list[int] = []

MIN_SCORE           = 72
MIN_VOLUME_RATIO    = 1.5
SCAN_INTERVAL_SEC   = 300
MAX_ALERTS_PER_HOUR = 10

WATCHLIST = [
    "AAPL","MSFT","NVDA","GOOGL","AMZN","META","TSLA","AVGO","ORCL","CRM",
    "AMD","INTC","QCOM","MU","AMAT","LRCX","MRVL","SMCI","ARM","KLAC",
    "JPM","BAC","WFC","GS","MS","BLK","V","MA","AXP","SCHW",
    "LLY","JNJ","UNH","ABBV","MRK","PFE","AMGN","GILD","REGN","VRTX",
    "COST","WMT","HD","MCD","SBUX","NKE","TGT","LOW","TJX","BABA",
    "XOM","CVX","COP","SLB","EOG","PSX","MPC","VLO","OXY","HAL",
    "CAT","DE","BA","HON","GE","RTX","LMT","NOC","UPS","FDX",
    "NFLX","DIS","CMCSA","T","VZ","TMUS","SNAP","PINS","RBLX","SPOT",
    "NOW","SNOW","PLTR","DDOG","ZS","CRWD","NET","MDB","GTLB","PATH",
    "SPY","QQQ","IWM","XLF","XLK","XLE","XLV","SOXL","ARKK","DIA",
    "MRNA","BNTX","BIIB","ILMN","ISRG","DXCM","ABT","EW","IDXX","ZBH",
    "MSTR","COIN","HOOD","SOFI","UPST","AFRM","RKLB","IONQ","QUBT","LUNR",
    "UBER","LYFT","ABNB","DASH","ROKU","ZM","TWLO","OKTA","BILL","DOCN",
    "AMT","PLD","EQIX","CCI","O","NEE","DUK","SO","D","AEP",
    "VIST","CVE","HES","FANG","DVN","MRO","APA","SM","CIVI","NOG",
]

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

alert_count_this_hour = 0
last_hour_reset = time.time()
alerted_symbols: dict[str, float] = {}
scanning_active = True


# ══════════════════════════════════════════════
# MARKET SESSION
# ══════════════════════════════════════════════

def get_market_session() -> str:
    now = datetime.now(timezone.utc)
    m = now.hour * 60 + now.minute
    if 780 <= m < 810:    return "🌅 Pre-Market"
    elif 810 <= m < 1200: return "📈 Regular Market"
    elif 1200 <= m < 1440:return "🌙 After-Hours"
    else:                 return "🌃 Overnight/Futures"


# ══════════════════════════════════════════════
# MARKET DATA
# ══════════════════════════════════════════════

async def get_candles(symbol: str, resolution="D", count=60) -> dict:
    to_ts   = int(time.time())
    from_ts = to_ts - (count * 86400 * 2)
    url = (f"https://finnhub.io/api/v1/stock/candle?symbol={symbol}"
           f"&resolution={resolution}&from={from_ts}&to={to_ts}&token={FINNHUB_API_KEY}")
    async with aiohttp.ClientSession() as s:
        async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
            return await r.json()


# ══════════════════════════════════════════════
# TECHNICAL ANALYSIS ENGINE
# ══════════════════════════════════════════════

def calc_rsi(prices: list, p=14) -> float:
    if len(prices) < p+1: return 50.0
    g = [max(prices[i]-prices[i-1],0) for i in range(1,len(prices))]
    l = [max(prices[i-1]-prices[i],0) for i in range(1,len(prices))]
    ag = sum(g[-p:])/p; al = sum(l[-p:])/p
    return round(100-(100/(1+ag/al)),2) if al else 100.0

def calc_ema(prices: list, p: int) -> list:
    if len(prices) < p: return prices
    k = 2/(p+1)
    e = [sum(prices[:p])/p]
    for x in prices[p:]: e.append(x*k+e[-1]*(1-k))
    return e

def calc_macd(prices):
    if len(prices)<26: return None,None,None
    e12=calc_ema(prices,12); e26=calc_ema(prices,26)
    n=min(len(e12),len(e26))
    ml=[e12[-n+i]-e26[-n+i] for i in range(n)]
    sig=calc_ema(ml,9)
    if not sig: return None,None,None
    return round(ml[-1],4),round(sig[-1],4),round(ml[-1]-sig[-1],4)

def calc_bb(prices,p=20):
    if len(prices)<p: return None,None,None
    r=prices[-p:]; m=sum(r)/p
    std=(sum((x-m)**2 for x in r)/p)**0.5
    return round(m+2*std,2),round(m,2),round(m-2*std,2)

def calc_atr(h,l,c,p=14):
    if len(c)<p+1: return 0.0
    trs=[max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1])) for i in range(1,len(c))]
    return round(sum(trs[-p:])/p,4)

def calc_stoch(h,l,c,p=14):
    if len(c)<p: return 50.0,50.0
    rh=max(h[-p:]); rl=min(l[-p:])
    if rh==rl: return 50.0,50.0
    k=((c[-1]-rl)/(rh-rl))*100
    rh2=max(h[-p-1:-1]) if len(h)>p else rh
    rl2=min(l[-p-1:-1]) if len(l)>p else rl
    k2=((c[-2]-rl2)/(rh2-rl2))*100 if rh2!=rl2 and len(c)>p else k
    return round(k,2),round((k+k2)/2,2)

def calc_vwap(h,l,c,v):
    if not v or sum(v)==0: return c[-1]
    tp=[(h[i]+l[i]+c[i])/3 for i in range(len(c))]
    return round(sum(tp[i]*v[i] for i in range(len(c)))/sum(v),2)

def analyze(candles:dict)->dict:
    if not candles or candles.get("s")!="ok": return {}
    c=candles.get("c",[]); o=candles.get("o",[])
    h=candles.get("h",[]); l=candles.get("l",[]); v=candles.get("v",[])
    if len(c)<30: return {}

    rsi=calc_rsi(c)
    macd,sig,hist=calc_macd(c)
    bbu,bbm,bbl=calc_bb(c)
    atr=calc_atr(h,l,c)
    sk,sd=calc_stoch(h,l,c)
    vwap=calc_vwap(h,l,c,v)
    sma20=round(sum(c[-20:])/20,2) if len(c)>=20 else None
    sma50=round(sum(c[-50:])/50,2) if len(c)>=50 else None
    ema9 =round(calc_ema(c,9)[-1],2) if len(c)>=9 else None
    ema21=round(calc_ema(c,21)[-1],2) if len(c)>=21 else None
    avg_v=sum(v[-20:])/20 if len(v)>=20 else 1
    vr=round(v[-1]/avg_v,2) if avg_v else 1.0
    chg=round((c[-1]-c[-2])/c[-2]*100,2) if len(c)>1 else 0

    return dict(price=c[-1],prev=c[-2] if len(c)>1 else c[-1],change_pct=chg,
                rsi=rsi,macd=macd,macd_signal=sig,macd_hist=hist,
                bb_upper=bbu,bb_mid=bbm,bb_lower=bbl,atr=atr,
                stoch_k=sk,stoch_d=sd,vwap=vwap,sma20=sma20,sma50=sma50,
                ema9=ema9,ema21=ema21,vol_ratio=vr,volume=v[-1],
                high=h[-1],low=l[-1])


# ══════════════════════════════════════════════
# SCORING ENGINE
# ══════════════════════════════════════════════

def score(t:dict)->tuple[int,str,str]:
    if not t: return 0,"NONE",""
    s=0; reasons=[]; price=t.get("price",0)
    rsi=t.get("rsi",50)

    # RSI
    if rsi<28:   s+=22; reasons.append(f"RSI={rsi} ذروة بيع قوية 🔥")
    elif rsi<38: s+=13; reasons.append(f"RSI={rsi} منطقة شراء")
    elif rsi>72: s-=12

    # MACD
    hist=t.get("macd_hist"); macd=t.get("macd")
    if hist is not None:
        if hist>0: s+=15; reasons.append("MACD تقاطع صاعد ✅")
        else: s-=5

    # Stochastic
    k=t.get("stoch_k",50); d=t.get("stoch_d",50)
    if k<20 and d<20: s+=16; reasons.append(f"Stoch={k:.0f} ذروة بيع")
    elif k>d and k<40: s+=10; reasons.append("Stoch تقاطع صاعد")
    elif k>80: s-=8

    # Bollinger
    bbl=t.get("bb_lower"); bbu=t.get("bb_upper")
    if bbl and price<=bbl*1.01: s+=16; reasons.append("عند الباند السفلي 🎯")
    elif bbu and price>=bbu*0.99: s-=10

    # Moving Averages
    sma20=t.get("sma20"); sma50=t.get("sma50")
    ema9=t.get("ema9"); ema21=t.get("ema21")
    if sma20 and sma50 and sma20>sma50: s+=8; reasons.append("SMA20>SMA50 اتجاه صاعد")
    if ema9 and ema21 and ema9>ema21: s+=7; reasons.append("EMA9>EMA21 زخم إيجابي")
    if sma50 and price>sma50: s+=5

    # VWAP
    vwap=t.get("vwap")
    if vwap and price>vwap: s+=8; reasons.append(f"فوق VWAP ${vwap}")

    # Volume
    vr=t.get("vol_ratio",1)
    if vr>=2.5:   s+=14; reasons.append(f"حجم {vr:.1f}x المتوسط 🔥🔥")
    elif vr>=1.5: s+=8;  reasons.append(f"حجم {vr:.1f}x المتوسط")
    elif vr<0.5:  s-=6

    # Price Action
    chg=t.get("change_pct",0)
    if -4<=chg<=-1: s+=5; reasons.append(f"تصحيح صحي {chg}%")

    sig="NONE"
    if s>=MIN_SCORE:
        if rsi<38 and bbl and price<=bbl*1.02: sig="SWING_BUY"
        elif hist and hist>0 and vr>=1.5: sig="DAY_BUY"
        else: sig="BUY"

    return min(s,100), sig, " | ".join(reasons)


def targets(t:dict)->dict:
    price=t.get("price",0); atr=t.get("atr",price*0.02)
    bbl=t.get("bb_lower",price*0.97); sma20=t.get("sma20",price*0.98)
    sl=round(min(price-atr*1.5, bbl*0.99, sma20*0.99),2)
    risk=price-sl if price-sl>0 else price*0.02
    return dict(entry=price, sl=sl,
                tp1=round(price+risk*1.5,2),
                tp2=round(price+risk*2.5,2),
                tp3=round(price+risk*4.0,2),
                risk_pct=round(risk/price*100,2),
                rr=round(risk*2.5/risk,1))


# ══════════════════════════════════════════════
# CLAUDE AI CONFIRMATION
# ══════════════════════════════════════════════

async def claude_confirm(symbol:str, t:dict, sc:int, reasons:str, tgt:dict)->str:
    prompt=(f"سهم {symbol} | نقاط {sc}/100 | جلسة {get_market_session()}\n"
            f"السعر ${t.get('price')} | RSI={t.get('rsi')} | MACD_Hist={t.get('macd_hist')}\n"
            f"Stoch={t.get('stoch_k')} | Vol={t.get('vol_ratio')}x | BB_Lower=${t.get('bb_lower')}\n"
            f"الأسباب: {reasons}\n"
            f"SL=${tgt['sl']} TP1=${tgt['tp1']} TP2=${tgt['tp2']} TP3=${tgt['tp3']} R:R=1:{tgt['rr']}\n\n"
            f"أجب فقط بـ 3 أسطر:\n"
            f"1. ✅ قوي / ⚠️ متوسط / ❌ ضعيف\n"
            f"2. أهم ملاحظة (سطر واحد)\n"
            f"3. الكلمة: ادخل / انتظر / تجنب")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post("https://api.anthropic.com/v1/messages",
                headers={"x-api-key":ANTHROPIC_API_KEY,"anthropic-version":"2023-06-01","content-type":"application/json"},
                json={"model":"claude-haiku-4-5-20251001","max_tokens":150,"messages":[{"role":"user","content":prompt}]},
                timeout=aiohttp.ClientTimeout(total=20)) as r:
                data=await r.json()
        return data["content"][0]["text"] if "content" in data else "⚠️ غير متاح"
    except Exception as e:
        return f"⚠️ {e}"


# ══════════════════════════════════════════════
# ALERT FORMATTER
# ══════════════════════════════════════════════

def fmt_alert(symbol:str, t:dict, sc:int, sig:str, reasons:str, tgt:dict, ai:str)->str:
    session=get_market_session()
    em="🚀" if sc>=85 else "📊"
    sl={"SWING_BUY":"🔄 Swing","DAY_BUY":"⚡ Day Trade","BUY":"📈 Buy"}.get(sig,"📈")
    ep=tgt['entry']
    return (f"{em} *تنبيه دخول — {symbol}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏷️ {sl} | النقاط: *{sc}/100*\n"
            f"⏰ {session}\n"
            f"💵 `${t['price']}` | {'+' if t['change_pct']>=0 else ''}{t['change_pct']}%\n\n"
            f"🎯 *الأهداف:*\n"
            f"  دخول: `${ep}`\n"
            f"  TP1: `${tgt['tp1']}` (+{round((tgt['tp1']-ep)/ep*100,1)}%)\n"
            f"  TP2: `${tgt['tp2']}` (+{round((tgt['tp2']-ep)/ep*100,1)}%)\n"
            f"  TP3: `${tgt['tp3']}` (+{round((tgt['tp3']-ep)/ep*100,1)}%)\n"
            f"  🛑 SL: `${tgt['sl']}` (-{tgt['risk_pct']}%)\n"
            f"  📐 R:R = 1:{tgt['rr']}\n\n"
            f"📈 RSI:{t.get('rsi')} | Stoch:{t.get('stoch_k')} | MACD:{t.get('macd_hist')} | Vol:{t.get('vol_ratio')}x\n\n"
            f"🧠 *AI:* {ai}\n\n"
            f"💡 {reasons}")


# ══════════════════════════════════════════════
# SCANNER
# ══════════════════════════════════════════════

async def scan_one(symbol:str):
    try:
        candles=await get_candles(symbol)
        t=analyze(candles)
        if not t or t.get("vol_ratio",0)<MIN_VOLUME_RATIO: return None
        sc,sig,reasons=score(t)
        if sc<MIN_SCORE or sig=="NONE": return None
        return sc,sig,t,reasons
    except Exception as e:
        log.debug(f"scan {symbol}: {e}")
        return None


async def run_scanner(app:Application):
    global alert_count_this_hour, last_hour_reset, scanning_active
    log.info("🔍 Scanner started")

    while True:
        if not scanning_active:
            await asyncio.sleep(60); continue

        if time.time()-last_hour_reset>3600:
            alert_count_this_hour=0; last_hour_reset=time.time()

        log.info(f"🔍 Scanning {len(WATCHLIST)} symbols | {get_market_session()}")

        for i in range(0,len(WATCHLIST),10):
            if alert_count_this_hour>=MAX_ALERTS_PER_HOUR: break
            batch=WATCHLIST[i:i+10]
            results=await asyncio.gather(*[scan_one(s) for s in batch], return_exceptions=True)

            for sym,res in zip(batch,results):
                if not res or isinstance(res,Exception): continue
                sc,sig,t,reasons=res
                if time.time()-alerted_symbols.get(sym,0)<14400: continue

                tgt=targets(t)
                ai=await claude_confirm(sym,t,sc,reasons,tgt)
                if "تجنب" in ai or "❌" in ai:
                    log.info(f"🚫 Claude rejected {sym}"); continue

                msg=fmt_alert(sym,t,sc,sig,reasons,tgt,ai)
                sent=False
                for cid in ALERT_CHAT_IDS:
                    try:
                        await app.bot.send_message(cid,msg,parse_mode="Markdown")
                        sent=True
                    except Exception as e:
                        log.error(f"Send error {cid}: {e}")
                if sent:
                    alerted_symbols[sym]=time.time()
                    alert_count_this_hour+=1
                    log.info(f"✅ Alert: {sym} score={sc}")

            await asyncio.sleep(2)

        log.info(f"✅ Scan done. Next in {SCAN_INTERVAL_SEC//60}min")
        await asyncio.sleep(SCAN_INTERVAL_SEC)


# ══════════════════════════════════════════════
# TELEGRAM HANDLERS
# ══════════════════════════════════════════════

async def start(update:Update, ctx:ContextTypes.DEFAULT_TYPE):
    chat_id=update.effective_chat.id
    kb=[[InlineKeyboardButton("📡 تفعيل التنبيهات",callback_data="sub"),
         InlineKeyboardButton("🔕 إيقاف",callback_data="unsub")],
        [InlineKeyboardButton("🔍 فحص سريع",callback_data="scan"),
         InlineKeyboardButton("📈 حالة السوق",callback_data="status")],
        [InlineKeyboardButton("⚙️ الإعدادات",callback_data="settings")]]
    await update.message.reply_text(
        f"🤖 *بوت التداول الذكي v2.0*\n\n"
        f"🔍 يراقب *{len(WATCHLIST)} سهم* أمريكي تلقائياً\n"
        f"⏰ يعمل *24/7* Pre / Regular / After / Night\n"
        f"🧠 *8 مؤشرات + Claude AI* لتأكيد الفرص\n"
        f"🎯 تنبيهات بـ *دخول + TP1/2/3 + SL + R:R*\n\n"
        f"🆔 Chat ID: `{chat_id}`\n\n"
        f"👇 اضغط *تفعيل التنبيهات* للبدء",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb))


async def btn(update:Update, ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    cid=q.message.chat_id

    if q.data=="sub":
        if cid not in ALERT_CHAT_IDS: ALERT_CHAT_IDS.append(cid)
        await q.edit_message_text(
            f"✅ *تم تفعيل التنبيهات!*\n\n"
            f"🔍 {len(WATCHLIST)} سهم تحت المراقبة\n"
            f"⏱️ فحص كل {SCAN_INTERVAL_SEC//60} دقائق\n"
            f"📊 الحد الأدنى: {MIN_SCORE}/100 نقطة\n\n"
            f"ستصلك تنبيهات فور اكتشاف فرصة 🚀",
            parse_mode="Markdown")

    elif q.data=="unsub":
        if cid in ALERT_CHAT_IDS: ALERT_CHAT_IDS.remove(cid)
        await q.edit_message_text("🔕 تم إيقاف التنبيهات.")

    elif q.data=="status":
        await q.edit_message_text(
            f"📊 *حالة السوق*\n\n"
            f"⏰ الجلسة: {get_market_session()}\n"
            f"🔍 الأسهم: {len(WATCHLIST)}\n"
            f"🔔 تنبيهات هذه الساعة: {alert_count_this_hour}/{MAX_ALERTS_PER_HOUR}\n"
            f"📡 المسح: {'🟢 نشط' if scanning_active else '🔴 متوقف'}\n"
            f"🕐 UTC: {datetime.now(timezone.utc).strftime('%H:%M:%S')}",
            parse_mode="Markdown")

    elif q.data=="settings":
        await q.edit_message_text(
            f"⚙️ *الإعدادات الحالية*\n\n"
            f"📊 الحد الأدنى: {MIN_SCORE}/100\n"
            f"📈 الحجم المطلوب: {MIN_VOLUME_RATIO}x\n"
            f"⏱️ فترة المسح: {SCAN_INTERVAL_SEC//60} دقيقة\n"
            f"🔔 حد التنبيهات/ساعة: {MAX_ALERTS_PER_HOUR}\n"
            f"📋 الأسهم: {len(WATCHLIST)}",
            parse_mode="Markdown")

    elif q.data=="scan":
        await q.edit_message_text("⏳ فحص 30 سهم... لحظة")
        found=[]
        for sym in WATCHLIST[:30]:
            r=await scan_one(sym)
            if r:
                sc,sig,t,_=r
                found.append(f"🎯 *{sym}* — {sc}/100 | ${t['price']}")
        if found:
            msg=f"🔍 *نتائج الفحص السريع*\n\n"+"\n".join(found[:8])
            msg+="\n\n_أرسل رمز أي سهم لتحليل كامل_"
        else:
            msg="🔍 لا توجد فرص قوية الآن في العينة.\nجرب لاحقاً أو أرسل رمز سهم محدد."
        await q.edit_message_text(msg, parse_mode="Markdown")


async def analyze_cmd(update:Update, ctx:ContextTypes.DEFAULT_TYPE):
    sym=update.message.text.strip().upper()
    if not sym.isalpha() or len(sym)>6: return

    msg=await update.message.reply_text(f"🔍 تحليل *{sym}*...", parse_mode="Markdown")
    try:
        candles=await get_candles(sym)
        t=analyze(candles)
        if not t:
            await msg.edit_text(f"❌ لا توجد بيانات كافية لـ `{sym}`"); return

        sc,sig,reasons=score(t)
        tgt=targets(t)

        if sc>=MIN_SCORE:
            ai=await claude_confirm(sym,t,sc,reasons,tgt)
            await msg.edit_text(fmt_alert(sym,t,sc,sig,reasons,tgt,ai), parse_mode="Markdown")
        else:
            await msg.edit_text(
                f"📊 *تحليل {sym}*\n"
                f"💵 `${t['price']}` | {t['change_pct']}%\n"
                f"⏰ {get_market_session()}\n\n"
                f"RSI:{t.get('rsi')} | Stoch:{t.get('stoch_k')} | Vol:{t.get('vol_ratio')}x\n\n"
                f"⚠️ النقاط: *{sc}/100* (الحد: {MIN_SCORE})\n"
                f"💡 {reasons if reasons else 'لا توجد إشارات قوية حالياً'}",
                parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ خطأ: `{e}`", parse_mode="Markdown")


# ══════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════

async def post_init(app:Application):
    asyncio.create_task(run_scanner(app))
    log.info("✅ Scanner started")


def main():
    app=(Application.builder()
         .token(TELEGRAM_BOT_TOKEN)
         .post_init(post_init)
         .build())
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help",  start))
    app.add_handler(CallbackQueryHandler(btn))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, analyze_cmd))
    log.info("🤖 Trading Bot v2.0 — Full US Market Scanner")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
