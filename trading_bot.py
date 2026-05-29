"""
🤖 AI Trading Bot v3.0 - SUPER EDITION
أقوى بوت تداول مدعوم بالذكاء الاصطناعي
"""

import os, logging, asyncio, aiohttp, time, math
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

MIN_SCORE           = 75
SCAN_INTERVAL_SEC   = 240     # فحص كل 4 دقائق
MAX_ALERTS_PER_HOUR = 12
COOLDOWN_HOURS      = 4       # لا تكرر نفس السهم قبل 4 ساعات
TOP_STOCKS_COUNT    = 300     # أفضل أسهم من السوق

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# حالة البوت
alert_count_this_hour = 0
last_hour_reset       = time.time()
alerted_symbols: dict[str, float] = {}
scanning_active       = True
last_scan_stats       = {"scanned": 0, "opportunities": 0, "sent": 0, "time": ""}
dynamic_watchlist: list[str] = []

# قائمة احتياطية (أفضل أسهم ثابتة)
FALLBACK_WATCHLIST = [
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
    "AMT","PLD","EQIX","CCI","O","NEE","DUK","SO","AEP","D",
    "VIST","CVE","HES","FANG","DVN","MRO","CIVI","SM","NOG","APA",
    "ADBE","INTU","PANW","FTNT","CYBR","S","TENB","RPM","VRNS","QLYS",
    "TSMC","TSM","ASML","WOLF","ON","SWKS","MPWR","ENTG","TER","COHU",
    "RIVN","LCID","NIO","LI","XPEV","FSR","CHPT","BLNK","EVgo","PTRA",
    "SQ","PYPL","ADYEY","AFRM","BNPL","RELY","FLYW","COUR","DUOL","CPNG",
]


# ══════════════════════════════════════════════
# MARKET SESSION
# ══════════════════════════════════════════════

def get_session() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    m   = now.hour * 60 + now.minute
    if 780 <= m < 810:     return "🌅 Pre-Market",    "premarket"
    elif 810 <= m < 1200:  return "📈 Regular Market", "regular"
    elif 1200 <= m < 1440: return "🌙 After-Hours",    "afterhours"
    else:                  return "🌃 Overnight",      "overnight"


# ══════════════════════════════════════════════
# DYNAMIC WATCHLIST — أفضل أسهم السوق اليوم
# ══════════════════════════════════════════════

async def fetch_dynamic_watchlist() -> list[str]:
    """جلب أكثر الأسهم تداولاً ونشاطاً في السوق الأمريكي"""
    symbols = set()

    # 1. أكثر الأسهم تداولاً (Most Active)
    try:
        url = f"https://finnhub.io/api/v1/stock/symbol?exchange=US&token={FINNHUB_API_KEY}"
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                data = await r.json()
        # فلتر: أسهم عادية فقط، بدون ETFs المعقدة
        for item in data:
            sym = item.get("symbol","")
            typ = item.get("type","")
            if typ in ("Common Stock","EQS") and "." not in sym and len(sym)<=5:
                symbols.add(sym)
        log.info(f"📋 Fetched {len(symbols)} symbols from exchange")
    except Exception as e:
        log.error(f"Dynamic watchlist error: {e}")

    # إذا فشل الجلب، استخدم القائمة الاحتياطية
    if len(symbols) < 50:
        log.warning("Using fallback watchlist")
        return FALLBACK_WATCHLIST

    # أولوية للأسهم المعروفة
    priority = set(FALLBACK_WATCHLIST)
    result   = [s for s in symbols if s in priority]
    rest     = [s for s in symbols if s not in priority]
    result  += rest[:TOP_STOCKS_COUNT - len(result)]

    return result[:TOP_STOCKS_COUNT]


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


async def get_quote(symbol: str) -> dict:
    url = f"https://finnhub.io/api/v1/quote?symbol={symbol}&token={FINNHUB_API_KEY}"
    async with aiohttp.ClientSession() as s:
        async with s.get(url, timeout=aiohttp.ClientTimeout(total=6)) as r:
            return await r.json()


async def get_news_sentiment(symbol: str) -> dict:
    url = f"https://finnhub.io/api/v1/news-sentiment?symbol={symbol}&token={FINNHUB_API_KEY}"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=6)) as r:
                return await r.json()
    except Exception:
        return {}


async def get_basic_financials(symbol: str) -> dict:
    url = f"https://finnhub.io/api/v1/stock/metric?symbol={symbol}&metric=all&token={FINNHUB_API_KEY}"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=6)) as r:
                return await r.json()
    except Exception:
        return {}


# ══════════════════════════════════════════════
# TECHNICAL INDICATORS
# ══════════════════════════════════════════════

def calc_rsi(p: list, n=14) -> float:
    if len(p) < n+1: return 50.0
    g = [max(p[i]-p[i-1], 0) for i in range(1, len(p))]
    l = [max(p[i-1]-p[i], 0) for i in range(1, len(p))]
    ag = sum(g[-n:])/n; al = sum(l[-n:])/n
    return round(100-(100/(1+ag/al)), 2) if al else 100.0

def calc_ema(p: list, n: int) -> list:
    if len(p) < n: return p
    k = 2/(n+1); e = [sum(p[:n])/n]
    for x in p[n:]: e.append(x*k + e[-1]*(1-k))
    return e

def calc_macd(p):
    if len(p) < 26: return None, None, None
    e12=calc_ema(p,12); e26=calc_ema(p,26)
    n=min(len(e12),len(e26))
    ml=[e12[-n+i]-e26[-n+i] for i in range(n)]
    sig=calc_ema(ml,9)
    if not sig: return None,None,None
    return round(ml[-1],4), round(sig[-1],4), round(ml[-1]-sig[-1],4)

def calc_bb(p, n=20):
    if len(p)<n: return None,None,None
    r=p[-n:]; m=sum(r)/n
    std=(sum((x-m)**2 for x in r)/n)**0.5
    return round(m+2*std,2), round(m,2), round(m-2*std,2)

def calc_atr(h,l,c,n=14):
    if len(c)<n+1: return 0.0
    trs=[max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1])) for i in range(1,len(c))]
    return round(sum(trs[-n:])/n, 4)

def calc_stoch(h,l,c,n=14):
    if len(c)<n: return 50.0,50.0
    rh=max(h[-n:]); rl=min(l[-n:])
    if rh==rl: return 50.0,50.0
    k=((c[-1]-rl)/(rh-rl))*100
    rh2=max(h[-n-1:-1]) if len(h)>n else rh
    rl2=min(l[-n-1:-1]) if len(l)>n else rl
    k2=((c[-2]-rl2)/(rh2-rl2))*100 if rh2!=rl2 and len(c)>n else k
    return round(k,2), round((k+k2)/2,2)

def calc_vwap(h,l,c,v):
    if not v or sum(v)==0: return c[-1]
    tp=[(h[i]+l[i]+c[i])/3 for i in range(len(c))]
    return round(sum(tp[i]*v[i] for i in range(len(c)))/sum(v), 2)

def calc_williams_r(h,l,c,n=14):
    if len(c)<n: return -50.0
    rh=max(h[-n:]); rl=min(l[-n:])
    if rh==rl: return -50.0
    return round(((rh-c[-1])/(rh-rl))*-100, 2)

def calc_cci(h,l,c,n=20):
    if len(c)<n: return 0.0
    tp=[(h[i]+l[i]+c[i])/3 for i in range(len(c))]
    tp_n=tp[-n:]; mean=sum(tp_n)/n
    mad=sum(abs(x-mean) for x in tp_n)/n
    return round((tp[-1]-mean)/(0.015*mad),2) if mad else 0.0

def calc_obv(c,v):
    obv=0
    for i in range(1,len(c)):
        if c[i]>c[i-1]: obv+=v[i]
        elif c[i]<c[i-1]: obv-=v[i]
    return obv

def calc_support_resistance(h,l,c,n=20):
    """مستويات دعم ومقاومة بسيطة"""
    if len(c)<n: return None,None
    recent_h=h[-n:]; recent_l=l[-n:]
    resistance=round(max(recent_h),2)
    support   =round(min(recent_l),2)
    return support, resistance


# ══════════════════════════════════════════════
# CANDLESTICK PATTERNS
# ══════════════════════════════════════════════

def detect_patterns(o,h,l,c) -> list[str]:
    """كشف أنماط الشموع الانعكاسية"""
    patterns=[]
    if len(c)<3: return patterns
    p,ph,pl,po = c[-1],h[-1],l[-1],o[-1]
    p2,ph2,pl2,po2 = c[-2],h[-2],l[-2],o[-2]
    p3,ph3,pl3,po3 = c[-3],h[-3],l[-3],o[-3] if len(c)>=3 else (p2,ph2,pl2,po2)

    body=abs(p-po); rng=ph-pl
    body2=abs(p2-po2); rng2=ph2-pl2

    # Hammer (مطرقة) — انعكاس صاعد
    if body>0 and rng>0:
        lower_wick=min(p,po)-pl
        upper_wick=ph-max(p,po)
        if lower_wick>=body*2 and upper_wick<=body*0.3 and p>po:
            patterns.append("🔨 Hammer")

    # Doji — تردد
    if rng>0 and body/rng<0.1:
        patterns.append("✙ Doji")

    # Bullish Engulfing — ابتلاع صاعد
    if p>po and p2<po2 and p>po2 and po<p2:
        patterns.append("🕯️ Bullish Engulfing")

    # Morning Star — نجمة الصباح
    if len(c)>=3 and p3<po3 and body2/max(rng2,0.001)<0.3 and p>po and p>(p3+po3)/2:
        patterns.append("⭐ Morning Star")

    # Piercing Line
    if p2<po2 and p>po and po<p2 and p>(p2+po2)/2:
        patterns.append("📌 Piercing Line")

    # Three White Soldiers
    if len(c)>=3 and p>po and p2>po2 and p3>po3 and p>p2>p3:
        patterns.append("💪 Three White Soldiers")

    return patterns


# ══════════════════════════════════════════════
# FULL ANALYSIS ENGINE
# ══════════════════════════════════════════════

def full_analyze(candles: dict) -> dict:
    if not candles or candles.get("s") != "ok": return {}
    c=candles.get("c",[]); o=candles.get("o",[])
    h=candles.get("h",[]); l=candles.get("l",[]); v=candles.get("v",[])
    if len(c)<30: return {}

    rsi          = calc_rsi(c)
    macd,sig,hist= calc_macd(c)
    bbu,bbm,bbl  = calc_bb(c)
    atr          = calc_atr(h,l,c)
    sk,sd        = calc_stoch(h,l,c)
    vwap         = calc_vwap(h,l,c,v)
    wr           = calc_williams_r(h,l,c)
    cci          = calc_cci(h,l,c)
    obv          = calc_obv(c,v)
    sup,res      = calc_support_resistance(h,l,c)
    patterns     = detect_patterns(o,h,l,c)

    sma20  = round(sum(c[-20:])/20,2) if len(c)>=20 else None
    sma50  = round(sum(c[-50:])/50,2) if len(c)>=50 else None
    ema9   = round(calc_ema(c,9)[-1],2)  if len(c)>=9  else None
    ema21  = round(calc_ema(c,21)[-1],2) if len(c)>=21 else None
    ema50  = round(calc_ema(c,50)[-1],2) if len(c)>=50 else None

    avg_v  = sum(v[-20:])/20 if len(v)>=20 else 1
    vr     = round(v[-1]/avg_v,2) if avg_v else 1.0
    chg    = round((c[-1]-c[-2])/c[-2]*100,2) if len(c)>1 else 0

    # حساب الـ Trend Strength
    trend_up   = sum(1 for i in range(1,min(10,len(c))) if c[-i]>c[-i-1])
    trend_str  = round(trend_up/min(9,len(c)-1)*100)

    return dict(
        price=c[-1], prev=c[-2] if len(c)>1 else c[-1],
        change_pct=chg, high=h[-1], low=l[-1], open=o[-1],
        rsi=rsi, macd=macd, macd_signal=sig, macd_hist=hist,
        bb_upper=bbu, bb_mid=bbm, bb_lower=bbl,
        atr=atr, stoch_k=sk, stoch_d=sd, vwap=vwap,
        williams_r=wr, cci=cci, obv=obv,
        support=sup, resistance=res,
        sma20=sma20, sma50=sma50, ema9=ema9, ema21=ema21, ema50=ema50,
        vol_ratio=vr, volume=v[-1],
        patterns=patterns, trend_strength=trend_str
    )


# ══════════════════════════════════════════════
# SUPER SCORING ENGINE (12 مؤشر)
# ══════════════════════════════════════════════

def super_score(t: dict) -> tuple[int, str, list[str]]:
    if not t: return 0, "NONE", []
    s=0; reasons=[]; price=t.get("price",0)

    # ── RSI (0-22 نقطة) ──
    rsi=t.get("rsi",50)
    if rsi<25:      s+=22; reasons.append(f"RSI={rsi} 🔥 ذروة بيع قصوى")
    elif rsi<32:    s+=17; reasons.append(f"RSI={rsi} ذروة بيع قوية")
    elif rsi<40:    s+=10; reasons.append(f"RSI={rsi} منطقة شراء")
    elif rsi>75:    s-=15
    elif rsi>65:    s-=8

    # ── MACD (0-15) ──
    hist=t.get("macd_hist"); macd=t.get("macd")
    if hist is not None:
        if hist>0 and macd and macd>t.get("macd_signal",0):
            s+=15; reasons.append("MACD ✅ تقاطع صاعد قوي")
        elif hist>0:
            s+=8;  reasons.append("MACD إيجابي")
        elif hist<0:
            s-=8

    # ── Stochastic (0-16) ──
    k=t.get("stoch_k",50); d=t.get("stoch_d",50)
    if k<15 and d<15:   s+=16; reasons.append(f"Stoch={k:.0f} 🔥 ذروة بيع قصوى")
    elif k<25 and d<25: s+=11; reasons.append(f"Stoch={k:.0f} ذروة بيع")
    elif k>d and k<40:  s+=7;  reasons.append("Stoch تقاطع صاعد")
    elif k>80:          s-=10

    # ── Bollinger Bands (0-16) ──
    bbl=t.get("bb_lower"); bbu=t.get("bb_upper"); bbm=t.get("bb_mid")
    if bbl and price<=bbl:            s+=16; reasons.append("🎯 تحت الباند السفلي — فرصة قوية")
    elif bbl and price<=bbl*1.01:     s+=11; reasons.append("عند الباند السفلي")
    elif bbu and price>=bbu*0.99:     s-=12

    # ── Williams %R (0-10) ──
    wr=t.get("williams_r",-50)
    if wr<-80:   s+=10; reasons.append(f"Williams R={wr} ذروة بيع")
    elif wr<-60: s+=5
    elif wr>-20: s-=8

    # ── CCI (0-10) ──
    cci=t.get("cci",0)
    if cci<-150:    s+=10; reasons.append(f"CCI={cci:.0f} ذروة بيع")
    elif cci<-100:  s+=6
    elif cci>150:   s-=8

    # ── Moving Averages (0-15) ──
    sma20=t.get("sma20"); sma50=t.get("sma50")
    ema9=t.get("ema9"); ema21=t.get("ema21"); ema50=t.get("ema50")
    if sma20 and sma50 and sma20>sma50: s+=6; reasons.append("SMA20>SMA50")
    if ema9 and ema21 and ema9>ema21:   s+=5; reasons.append("EMA9>EMA21 زخم")
    if ema21 and ema50 and ema21>ema50: s+=4; reasons.append("EMA21>EMA50")

    # ── VWAP (0-8) ──
    vwap=t.get("vwap")
    if vwap and price>vwap: s+=8; reasons.append(f"فوق VWAP ${vwap}")
    elif vwap and price<vwap*0.99: s-=4

    # ── Support Level (0-8) ──
    sup=t.get("support"); res=t.get("resistance")
    if sup and price<=sup*1.02: s+=8; reasons.append(f"قرب الدعم ${sup} 🛡️")
    if res and price>=res*0.98: s-=6

    # ── Volume (0-14) ──
    vr=t.get("vol_ratio",1)
    if vr>=3.0:   s+=14; reasons.append(f"حجم {vr:.1f}x 🔥🔥🔥 انفجاري")
    elif vr>=2.0: s+=10; reasons.append(f"حجم {vr:.1f}x 🔥🔥 عالي")
    elif vr>=1.5: s+=6;  reasons.append(f"حجم {vr:.1f}x 🔥 فوق المتوسط")
    elif vr<0.5:  s-=8

    # ── Candlestick Patterns (0-10) ──
    patterns=t.get("patterns",[])
    if patterns:
        s+=min(len(patterns)*5, 10)
        reasons.append(f"نمط: {', '.join(patterns)}")

    # ── Trend Strength (0-8) ──
    ts=t.get("trend_strength",50)
    if ts>=70: s+=8; reasons.append(f"قوة اتجاه {ts}% صاعد")
    elif ts<=30: s-=5

    # تحديد نوع الإشارة
    signal="NONE"
    if s>=MIN_SCORE:
        if rsi<35 and bbl and price<=bbl*1.02:
            signal="SWING_BUY"
        elif hist and hist>0 and vr>=1.5:
            signal="DAY_BUY"
        elif patterns and rsi<45:
            signal="PATTERN_BUY"
        else:
            signal="BUY"

    return min(s,100), signal, reasons


def calc_targets(t: dict) -> dict:
    price=t.get("price",0); atr=t.get("atr",price*0.015)
    sup=t.get("support",price*0.97); bbl=t.get("bb_lower",price*0.97)
    sma20=t.get("sma20",price*0.98)

    sl=round(min(price-atr*1.5, sup*0.995, bbl*0.995, sma20*0.995), 2)
    risk=max(price-sl, price*0.01)

    tp1=round(price+risk*1.5,2)
    tp2=round(price+risk*2.5,2)
    tp3=round(price+risk*4.0,2)
    tp4=round(price+risk*6.0,2)

    res=t.get("resistance")
    if res and res>price: tp3=round(min(tp3,res*0.995),2)

    return dict(
        entry=price, sl=sl,
        tp1=tp1, tp2=tp2, tp3=tp3, tp4=tp4,
        risk_pct=round(risk/price*100,2),
        rr2=round(risk*2.5/risk,1),
        rr4=round(risk*4.0/risk,1)
    )


# ══════════════════════════════════════════════
# CLAUDE AI — تحليل نصي كامل
# ══════════════════════════════════════════════

async def claude_deep_analysis(symbol:str, t:dict, sc:int, reasons:list, tgt:dict, sentiment:dict, fins:dict) -> tuple[str,bool]:
    session,_=get_session()

    # مشاعر الأخبار
    sent_score=sentiment.get("companyNewsScore",0.5) if sentiment else 0.5
    sent_label="إيجابي 😊" if sent_score>0.6 else "سلبي 😟" if sent_score<0.4 else "محايد 😐"

    # بيانات مالية
    metric=fins.get("metric",{}) if fins else {}
    pe=metric.get("peNormalizedAnnual","N/A")
    beta=metric.get("beta","N/A")
    week52h=metric.get("52WeekHigh","N/A")
    week52l=metric.get("52WeekLow","N/A")

    prompt=f"""أنت كبير محللي وول ستريت. حلّل هذه الفرصة بعمق واحترافية عالية.

═══ بيانات {symbol} ═══
الجلسة: {session} | النقاط: {sc}/100
السعر: ${t.get('price')} | التغيير: {t.get('change_pct')}%
High: ${t.get('high')} | Low: ${t.get('low')}

═══ المؤشرات التقنية ═══
RSI: {t.get('rsi')} | MACD_Hist: {t.get('macd_hist')} | Stoch: {t.get('stoch_k')}
Williams%R: {t.get('williams_r')} | CCI: {t.get('cci')}
BB: Upper=${t.get('bb_upper')} Mid=${t.get('bb_mid')} Lower=${t.get('bb_lower')}
VWAP: ${t.get('vwap')} | ATR: {t.get('atr')}
EMA9/21/50: ${t.get('ema9')}/${t.get('ema21')}/${t.get('ema50')}
دعم: ${t.get('support')} | مقاومة: ${t.get('resistance')}
حجم: {t.get('vol_ratio')}x | قوة الاتجاه: {t.get('trend_strength')}%
أنماط الشموع: {', '.join(t.get('patterns',[])) or 'لا يوجد'}

═══ البيانات الأساسية ═══
P/E: {pe} | Beta: {beta}
52W High: ${week52h} | 52W Low: ${week52l}
مشاعر الأخبار: {sent_label} ({sent_score:.2f})

═══ الأهداف المقترحة ═══
دخول: ${tgt['entry']} | SL: ${tgt['sl']}
TP1: ${tgt['tp1']} | TP2: ${tgt['tp2']} | TP3: ${tgt['tp3']}
مخاطرة: {tgt['risk_pct']}% | R:R = 1:{tgt['rr4']}

الأسباب: {' | '.join(reasons[:5])}

أجب بالتنسيق التالي بالضبط:
VERDICT: [✅ قوية / ⚠️ متوسطة / ❌ ضعيفة]
ANALYSIS: [جملتان: تحليل المشهد التقني وسبب الدخول]
RISK: [جملة واحدة: أهم مخاطرة]
ACTION: [ادخل الآن / انتظر تراجع / تجنب]"""

    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key":ANTHROPIC_API_KEY,"anthropic-version":"2023-06-01","content-type":"application/json"},
                json={"model":"claude-haiku-4-5-20251001","max_tokens":300,
                      "messages":[{"role":"user","content":prompt}]},
                timeout=aiohttp.ClientTimeout(total=25)
            ) as r:
                data=await r.json()

        if "content" not in data: return "⚠️ AI غير متاح", True
        text=data["content"][0]["text"]

        # استخراج القرار
        approved = "تجنب" not in text and "❌" not in text.split("VERDICT:")[-1][:20]
        return text, approved

    except Exception as e:
        log.error(f"Claude error: {e}")
        return f"⚠️ {e}", True  # في حالة خطأ نكمل بدون AI


# ══════════════════════════════════════════════
# ALERT FORMATTER
# ══════════════════════════════════════════════

def format_super_alert(symbol:str, t:dict, sc:int, sig:str, reasons:list, tgt:dict, ai:str) -> str:
    session,_=get_session()
    grade = "🏆 A+" if sc>=90 else "⭐ A" if sc>=82 else "✅ B+" if sc>=75 else "📊 B"
    sig_label={"SWING_BUY":"🔄 Swing Trade","DAY_BUY":"⚡ Day Trade","PATTERN_BUY":"🕯️ Pattern Buy","BUY":"📈 Buy"}.get(sig,"📈")
    ep=tgt['entry']

    pct=lambda x: f"+{round((x-ep)/ep*100,1)}%"

    return (
        f"{'🚀' if sc>=85 else '📊'} *{symbol} — {sig_label}*\n"
        f"{'━'*22}\n"
        f"{grade} | النقاط: *{sc}/100* | {session}\n"
        f"💵 `${t['price']}` | {'+' if t['change_pct']>=0 else ''}{t['change_pct']}% | Vol: {t.get('vol_ratio')}x\n\n"
        f"🎯 *الأهداف:*\n"
        f"  📍 دخول: `${ep}`\n"
        f"  🥉 TP1: `${tgt['tp1']}` ({pct(tgt['tp1'])})\n"
        f"  🥈 TP2: `${tgt['tp2']}` ({pct(tgt['tp2'])})\n"
        f"  🥇 TP3: `${tgt['tp3']}` ({pct(tgt['tp3'])})\n"
        f"  💎 TP4: `${tgt['tp4']}` ({pct(tgt['tp4'])})\n"
        f"  🛑 SL: `${tgt['sl']}` (-{tgt['risk_pct']}%)\n"
        f"  📐 R:R = 1:{tgt['rr4']}\n\n"
        f"📊 *المؤشرات:*\n"
        f"  RSI:{t.get('rsi')} | Stoch:{t.get('stoch_k')} | WR:{t.get('williams_r')}\n"
        f"  MACD:{t.get('macd_hist')} | CCI:{t.get('cci')} | Vol:{t.get('vol_ratio')}x\n"
        f"  دعم:${t.get('support')} | مقاومة:${t.get('resistance')}\n"
        + (f"  🕯️ {' | '.join(t.get('patterns',[]))}\n" if t.get('patterns') else "")
        + f"\n🧠 *تحليل AI:*\n{ai}\n\n"
        f"💡 *الأسباب:*\n" + "\n".join(f"  • {r}" for r in reasons[:6])
    )


# ══════════════════════════════════════════════
# SCANNER ENGINE
# ══════════════════════════════════════════════

async def scan_one(symbol: str):
    try:
        candles=await get_candles(symbol, resolution="D", count=65)
        t=full_analyze(candles)
        if not t: return None

        # فلتر أولي سريع — توفير API calls
        rsi=t.get("rsi",50); vr=t.get("vol_ratio",1)
        if rsi>55 and vr<1.3: return None  # لا توجد إشارة واضحة

        sc,sig,reasons=super_score(t)
        if sc<MIN_SCORE or sig=="NONE": return None

        return sc, sig, t, reasons
    except Exception as e:
        log.debug(f"scan {symbol}: {e}")
        return None


async def run_scanner(app: Application):
    global alert_count_this_hour, last_hour_reset, scanning_active
    global dynamic_watchlist, last_scan_stats

    log.info("🚀 Super Scanner v3.0 started")

    # جلب القائمة الديناميكية أول مرة
    dynamic_watchlist = await fetch_dynamic_watchlist()
    log.info(f"📋 Watchlist: {len(dynamic_watchlist)} symbols")

    scan_count = 0

    while True:
        if not scanning_active:
            await asyncio.sleep(60); continue

        # إعادة ضبط عداد الساعة
        if time.time()-last_hour_reset > 3600:
            alert_count_this_hour=0; last_hour_reset=time.time()

        # تحديث القائمة الديناميكية كل 6 ساعات
        if scan_count % 90 == 0:
            dynamic_watchlist = await fetch_dynamic_watchlist()
            log.info(f"🔄 Watchlist updated: {len(dynamic_watchlist)} symbols")

        session,_=get_session()
        wl = dynamic_watchlist or FALLBACK_WATCHLIST
        log.info(f"🔍 Scanning {len(wl)} symbols | {session}")

        start_t=time.time(); opportunities=0; sent=0

        for i in range(0, len(wl), 8):
            if alert_count_this_hour >= MAX_ALERTS_PER_HOUR: break
            batch=wl[i:i+8]
            results=await asyncio.gather(*[scan_one(s) for s in batch], return_exceptions=True)

            for sym,res in zip(batch,results):
                if not res or isinstance(res,Exception): continue
                sc,sig,t,reasons=res; opportunities+=1

                # cooldown
                if time.time()-alerted_symbols.get(sym,0) < COOLDOWN_HOURS*3600: continue

                # جلب بيانات إضافية للأسهم المؤهلة
                sentiment, fins = await asyncio.gather(
                    get_news_sentiment(sym),
                    get_basic_financials(sym),
                    return_exceptions=True
                )
                if isinstance(sentiment, Exception): sentiment={}
                if isinstance(fins, Exception): fins={}

                tgt=calc_targets(t)
                ai_text, approved = await claude_deep_analysis(sym,t,sc,reasons,tgt,sentiment,fins)

                if not approved:
                    log.info(f"🚫 AI rejected {sym}"); continue

                msg=format_super_alert(sym,t,sc,sig,reasons,tgt,ai_text)

                s_sent=False
                for cid in ALERT_CHAT_IDS:
                    try:
                        await app.bot.send_message(cid, msg, parse_mode="Markdown")
                        s_sent=True
                    except Exception as e:
                        log.error(f"Send {cid}: {e}")

                if s_sent:
                    alerted_symbols[sym]=time.time()
                    alert_count_this_hour+=1; sent+=1
                    log.info(f"✅ Alert: {sym} sc={sc} sig={sig}")

            await asyncio.sleep(1.5)

        elapsed=round(time.time()-start_t)
        last_scan_stats={"scanned":len(wl),"opportunities":opportunities,
                         "sent":sent,"time":f"{elapsed}s"}
        log.info(f"✅ Scan done | found={opportunities} sent={sent} time={elapsed}s")
        scan_count+=1
        await asyncio.sleep(SCAN_INTERVAL_SEC)


# ══════════════════════════════════════════════
# TELEGRAM HANDLERS
# ══════════════════════════════════════════════

async def start(update:Update, ctx:ContextTypes.DEFAULT_TYPE):
    cid=update.effective_chat.id
    kb=[
        [InlineKeyboardButton("📡 تفعيل التنبيهات",callback_data="sub"),
         InlineKeyboardButton("🔕 إيقاف",callback_data="unsub")],
        [InlineKeyboardButton("🔍 فحص سريع",callback_data="scan"),
         InlineKeyboardButton("📊 حالة السوق",callback_data="status")],
        [InlineKeyboardButton("🏆 أفضل فرصة الآن",callback_data="best"),
         InlineKeyboardButton("⚙️ الإعدادات",callback_data="settings")],
    ]
    await update.message.reply_text(
        f"🤖 *بوت التداول الخارق v3.0*\n\n"
        f"🔍 يراقب *{len(dynamic_watchlist or FALLBACK_WATCHLIST)}+ سهم* ديناميكياً\n"
        f"⏰ يعمل *24/7* في كل الجلسات\n"
        f"🧠 *12 مؤشر + أنماط شموع + Claude AI*\n"
        f"📰 تحليل الأخبار والمشاعر\n"
        f"🎯 *4 أهداف ربح + SL + R:R*\n\n"
        f"🆔 Chat ID: `{cid}`\n\n"
        f"👇 اضغط *تفعيل التنبيهات* للبدء",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb))


async def btn(update:Update, ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); cid=q.message.chat_id

    if q.data=="sub":
        if cid not in ALERT_CHAT_IDS: ALERT_CHAT_IDS.append(cid)
        wl=dynamic_watchlist or FALLBACK_WATCHLIST
        await q.edit_message_text(
            f"✅ *تم تفعيل التنبيهات!*\n\n"
            f"🔍 {len(wl)} سهم تحت المراقبة\n"
            f"⏱️ فحص كل {SCAN_INTERVAL_SEC//60} دقائق\n"
            f"📊 الحد الأدنى: {MIN_SCORE}/100\n"
            f"🧠 12 مؤشر + Claude AI\n\n"
            f"سيصلك تنبيه فور اكتشاف فرصة 🚀",
            parse_mode="Markdown")

    elif q.data=="unsub":
        if cid in ALERT_CHAT_IDS: ALERT_CHAT_IDS.remove(cid)
        await q.edit_message_text("🔕 تم إيقاف التنبيهات.")

    elif q.data=="status":
        session,_=get_session(); st=last_scan_stats
        wl=dynamic_watchlist or FALLBACK_WATCHLIST
        await q.edit_message_text(
            f"📊 *حالة البوت*\n\n"
            f"⏰ الجلسة: {session}\n"
            f"🔍 الأسهم: {len(wl)}\n"
            f"📡 المسح: {'🟢 نشط' if scanning_active else '🔴 متوقف'}\n"
            f"🔔 تنبيهات/ساعة: {alert_count_this_hour}/{MAX_ALERTS_PER_HOUR}\n\n"
            f"📈 *آخر فحص:*\n"
            f"  فُحص: {st.get('scanned',0)} سهم\n"
            f"  فرص: {st.get('opportunities',0)}\n"
            f"  أُرسل: {st.get('sent',0)}\n"
            f"  وقت: {st.get('time','—')}\n\n"
            f"🕐 UTC: {datetime.now(timezone.utc).strftime('%H:%M:%S')}",
            parse_mode="Markdown")

    elif q.data=="settings":
        await q.edit_message_text(
            f"⚙️ *الإعدادات*\n\n"
            f"📊 الحد الأدنى: {MIN_SCORE}/100\n"
            f"⏱️ فترة المسح: {SCAN_INTERVAL_SEC//60} دقيقة\n"
            f"🔔 حد التنبيهات/ساعة: {MAX_ALERTS_PER_HOUR}\n"
            f"⏳ كولداون السهم: {COOLDOWN_HOURS} ساعات\n"
            f"📋 الأسهم: {len(dynamic_watchlist or FALLBACK_WATCHLIST)}\n"
            f"🧠 المؤشرات: 12 مؤشر\n"
            f"🕯️ أنماط الشموع: ✅\n"
            f"📰 تحليل المشاعر: ✅\n"
            f"💰 البيانات المالية: ✅",
            parse_mode="Markdown")

    elif q.data=="scan":
        await q.edit_message_text("⏳ فحص سريع لأفضل 40 سهم...")
        wl=(dynamic_watchlist or FALLBACK_WATCHLIST)[:40]
        found=[]
        for sym in wl:
            r=await scan_one(sym)
            if r:
                sc,sig,t,_=r
                sl={"SWING_BUY":"🔄","DAY_BUY":"⚡","PATTERN_BUY":"🕯️"}.get(sig,"📈")
                found.append((sc,f"{sl} *{sym}* — {sc}/100 | ${t['price']} | Vol:{t.get('vol_ratio')}x"))
        found.sort(key=lambda x:-x[0])
        if found:
            msg="🔍 *أفضل الفرص (فحص سريع)*\n\n"+"\n".join(x[1] for x in found[:8])
            msg+="\n\n_أرسل رمز السهم لتحليل كامل_"
        else:
            msg="🔍 لا توجد فرص قوية حالياً.\nجرب لاحقاً أو أرسل رمز سهم محدد."
        await q.edit_message_text(msg, parse_mode="Markdown")

    elif q.data=="best":
        await q.edit_message_text("🏆 أبحث عن أفضل فرصة...")
        wl=dynamic_watchlist or FALLBACK_WATCHLIST
        best=None; best_sc=0
        for sym in wl[:60]:
            r=await scan_one(sym)
            if r and r[0]>best_sc:
                best_sc=r[0]; best=(sym,)+r
        if best:
            sym,sc,sig,t,reasons=best
            tgt=calc_targets(t)
            ai,_=await claude_deep_analysis(sym,t,sc,reasons,tgt,{},{})
            msg=format_super_alert(sym,t,sc,sig,reasons,tgt,ai)
            await q.edit_message_text(msg, parse_mode="Markdown")
        else:
            await q.edit_message_text("🏆 لا توجد فرص بارزة حالياً. جرب لاحقاً.")


async def analyze_cmd(update:Update, ctx:ContextTypes.DEFAULT_TYPE):
    sym=update.message.text.strip().upper()
    if not sym.isalpha() or len(sym)>6: return

    msg=await update.message.reply_text(f"🔍 تحليل عميق لـ *{sym}*...", parse_mode="Markdown")
    try:
        candles, sentiment, fins = await asyncio.gather(
            get_candles(sym, resolution="D", count=65),
            get_news_sentiment(sym),
            get_basic_financials(sym),
            return_exceptions=True
        )
        if isinstance(candles,Exception) or not candles:
            await msg.edit_text(f"❌ لا توجد بيانات لـ `{sym}`"); return

        t=full_analyze(candles)
        if not t:
            await msg.edit_text(f"❌ بيانات غير كافية لـ `{sym}`"); return

        sc,sig,reasons=super_score(t)
        tgt=calc_targets(t)
        if isinstance(sentiment,Exception): sentiment={}
        if isinstance(fins,Exception): fins={}

        ai,_=await claude_deep_analysis(sym,t,sc,reasons,tgt,sentiment,fins)

        if sc>=MIN_SCORE:
            await msg.edit_text(format_super_alert(sym,t,sc,sig,reasons,tgt,ai), parse_mode="Markdown")
        else:
            session,_=get_session()
            await msg.edit_text(
                f"📊 *تحليل {sym}*\n"
                f"💵 `${t['price']}` | {t['change_pct']}% | {session}\n\n"
                f"RSI:{t.get('rsi')} | Stoch:{t.get('stoch_k')} | WR:{t.get('williams_r')}\n"
                f"CCI:{t.get('cci')} | Vol:{t.get('vol_ratio')}x\n"
                f"دعم:${t.get('support')} | مقاومة:${t.get('resistance')}\n"
                + (f"🕯️ {' | '.join(t.get('patterns',[]))}\n" if t.get('patterns') else "")
                + f"\n⚠️ النقاط: *{sc}/100* (الحد: {MIN_SCORE})\n\n"
                f"🧠 {ai}",
                parse_mode="Markdown")
    except Exception as e:
        log.error(f"analyze {sym}: {e}")
        await msg.edit_text(f"❌ خطأ: `{e}`", parse_mode="Markdown")


# ══════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════

async def post_init(app:Application):
    asyncio.create_task(run_scanner(app))
    log.info("✅ Super Scanner v3.0 started")


def main():
    app=(Application.builder()
         .token(TELEGRAM_BOT_TOKEN)
         .post_init(post_init)
         .build())
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help",  start))
    app.add_handler(CallbackQueryHandler(btn))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, analyze_cmd))
    log.info("🤖 Super Trading Bot v3.0 — Ultimate Edition")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
