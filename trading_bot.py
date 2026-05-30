"""
🤖 Super Trading Bot v4.0 - ULTIMATE PRECISION EDITION
أعلى دقة ممكنة — نسبة خطأ لا تُذكر
"""

import os, logging, asyncio, aiohttp, time, json
from datetime import datetime, timezone, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

# ══════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN")
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY",  "YOUR_ANTHROPIC_KEY")
FINNHUB_API_KEY    = os.getenv("FINNHUB_API_KEY",    "YOUR_FINNHUB_KEY")
ALPHA_VANTAGE_KEY  = os.getenv("ALPHA_VANTAGE_KEY",  "YOUR_AV_KEY")

ALERT_CHAT_IDS: list[int] = []
MIN_SCORE           = 78       # رفعنا الحد لدقة أعلى
SCAN_INTERVAL_SEC   = 300
MAX_ALERTS_PER_HOUR = 10
COOLDOWN_HOURS      = 5
MIN_CONFIRMATIONS   = 4        # عدد المؤشرات الموافقة كحد أدنى

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
    "AMT","PLD","EQIX","CCI","O","NEE","DUK","SO","AEP","D",
    "ADBE","INTU","PANW","FTNT","CYBR","S","VRNS","QLYS","TENB","RPM",
    "TSM","ASML","WOLF","ON","SWKS","MPWR","ENTG","TER","COHU","FORM",
    "RIVN","LCID","NIO","LI","XPEV","CHPT","BLNK","EVGO","PTRA","ZEV",
    "SQ","PYPL","RELY","FLYW","COUR","DUOL","CPNG","MELI","SE","GRAB",
    "VIST","CVE","HES","FANG","DVN","MRO","CIVI","SM","NOG","APA",
]

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

alert_count_this_hour = 0
last_hour_reset       = time.time()
alerted_symbols: dict[str, float] = {}
scanning_active       = True
last_scan_stats       = {"scanned":0,"opportunities":0,"sent":0,"rejected":0,"time":""}
fear_greed_cache      = {"value": 50, "label": "Neutral", "ts": 0}
market_regime         = "BULL"  # BULL / BEAR / SIDEWAYS


# ══════════════════════════════════════════════
# MARKET SESSION
# ══════════════════════════════════════════════

def get_session():
    m = datetime.now(timezone.utc).hour * 60 + datetime.now(timezone.utc).minute
    if 780<=m<810:    return "🌅 Pre-Market"
    elif 810<=m<1200: return "📈 Regular Market"
    elif 1200<=m<1440:return "🌙 After-Hours"
    else:             return "🌃 Overnight"


# ══════════════════════════════════════════════
# DATA SOURCES
# ══════════════════════════════════════════════

async def get_yahoo_data(symbol: str, period="3mo") -> dict:
    """Yahoo Finance — بيانات يومية مجانية"""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"interval":"1d","range":period,"includePrePost":"false"}
    headers = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36","Accept":"application/json"}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url,params=params,headers=headers,timeout=aiohttp.ClientTimeout(total=12)) as r:
                data=await r.json()
        result=data["chart"]["result"][0]
        q=result["indicators"]["quote"][0]
        meta=result.get("meta",{})
        def clean(lst): return [x for x in lst if x is not None]
        c=clean(q.get("close",[])); o=clean(q.get("open",[]));
        h=clean(q.get("high",[])); l=clean(q.get("low",[])); v=clean(q.get("volume",[]))
        n=min(len(c),len(o),len(h),len(l),len(v))
        if n<25: return {}
        return {"c":c[-n:],"o":o[-n:],"h":h[-n:],"l":l[-n:],"v":v[-n:],
                "name":meta.get("shortName",symbol),"currency":meta.get("currency","USD"),
                "mktcap":meta.get("marketCap",0),"exchange":meta.get("exchangeName","")}
    except Exception as e:
        log.debug(f"Yahoo {symbol}: {e}"); return {}


async def get_yahoo_weekly(symbol: str) -> dict:
    """بيانات أسبوعية للتحليل متعدد الأطر الزمنية"""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"interval":"1wk","range":"1y","includePrePost":"false"}
    headers = {"User-Agent":"Mozilla/5.0","Accept":"application/json"}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url,params=params,headers=headers,timeout=aiohttp.ClientTimeout(total=10)) as r:
                data=await r.json()
        result=data["chart"]["result"][0]
        q=result["indicators"]["quote"][0]
        def clean(lst): return [x for x in lst if x is not None]
        c=clean(q.get("close",[])); h=clean(q.get("high",[])); l=clean(q.get("low",[])); v=clean(q.get("volume",[]))
        n=min(len(c),len(h),len(l),len(v))
        if n<10: return {}
        return {"c":c[-n:],"h":h[-n:],"l":l[-n:],"v":v[-n:]}
    except Exception as e:
        log.debug(f"Yahoo weekly {symbol}: {e}"); return {}


async def get_fear_greed():
    """مؤشر الخوف والطمع — CNN"""
    global fear_greed_cache
    if time.time()-fear_greed_cache["ts"]<3600: return fear_greed_cache
    try:
        url="https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
        headers={"User-Agent":"Mozilla/5.0","Accept":"application/json"}
        async with aiohttp.ClientSession() as s:
            async with s.get(url,headers=headers,timeout=aiohttp.ClientTimeout(total=8)) as r:
                data=await r.json()
        val=float(data["fear_and_greed"]["score"])
        if val<=25:   label="😱 خوف شديد"
        elif val<=45: label="😟 خوف"
        elif val<=55: label="😐 محايد"
        elif val<=75: label="😊 طمع"
        else:         label="🤑 طمع شديد"
        fear_greed_cache={"value":val,"label":label,"ts":time.time()}
        log.info(f"Fear&Greed: {val:.0f} {label}")
        return fear_greed_cache
    except Exception as e:
        log.debug(f"F&G: {e}"); return fear_greed_cache


async def get_finnhub_sentiment(symbol: str) -> dict:
    try:
        url=f"https://finnhub.io/api/v1/news-sentiment?symbol={symbol}&token={FINNHUB_API_KEY}"
        async with aiohttp.ClientSession() as s:
            async with s.get(url,timeout=aiohttp.ClientTimeout(total=6)) as r:
                return await r.json()
    except: return {}


async def get_earnings_calendar(symbol: str) -> dict:
    """تقويم نتائج الشركات من Finnhub"""
    try:
        today=datetime.now(timezone.utc)
        from_d=(today-timedelta(days=7)).strftime("%Y-%m-%d")
        to_d=(today+timedelta(days=30)).strftime("%Y-%m-%d")
        url=f"https://finnhub.io/api/v1/calendar/earnings?from={from_d}&to={to_d}&symbol={symbol}&token={FINNHUB_API_KEY}"
        async with aiohttp.ClientSession() as s:
            async with s.get(url,timeout=aiohttp.ClientTimeout(total=6)) as r:
                data=await r.json()
        earnings=data.get("earningsCalendar",[])
        if not earnings: return {}
        next_e=earnings[0]
        date_str=next_e.get("date","")
        if date_str:
            e_date=datetime.strptime(date_str,"%Y-%m-%d").replace(tzinfo=timezone.utc)
            days_until=(e_date-today).days
            return {"date":date_str,"days_until":days_until,
                    "estimate":next_e.get("epsEstimate","N/A"),
                    "hour":next_e.get("hour","N/A")}
        return {}
    except Exception as e:
        log.debug(f"Earnings {symbol}: {e}"); return {}


async def get_av_rsi(symbol: str) -> float | None:
    """RSI من Alpha Vantage للتحقق المزدوج"""
    try:
        url=(f"https://www.alphavantage.co/query?function=RSI&symbol={symbol}"
             f"&interval=daily&time_period=14&series_type=close&apikey={ALPHA_VANTAGE_KEY}")
        async with aiohttp.ClientSession() as s:
            async with s.get(url,timeout=aiohttp.ClientTimeout(total=10)) as r:
                data=await r.json()
        series=data.get("Technical Analysis: RSI",{})
        if not series: return None
        latest=sorted(series.keys(),reverse=True)[0]
        return float(series[latest]["RSI"])
    except: return None


async def get_av_macd(symbol: str) -> dict:
    """MACD من Alpha Vantage للتحقق المزدوج"""
    try:
        url=(f"https://www.alphavantage.co/query?function=MACD&symbol={symbol}"
             f"&interval=daily&series_type=close&apikey={ALPHA_VANTAGE_KEY}")
        async with aiohttp.ClientSession() as s:
            async with s.get(url,timeout=aiohttp.ClientTimeout(total=10)) as r:
                data=await r.json()
        series=data.get("Technical Analysis: MACD",{})
        if not series: return {}
        latest=sorted(series.keys(),reverse=True)[0]
        return {"macd":float(series[latest]["MACD"]),
                "signal":float(series[latest]["MACD_Signal"]),
                "hist":float(series[latest]["MACD_Hist"])}
    except: return {}


# ══════════════════════════════════════════════
# TECHNICAL INDICATORS
# ══════════════════════════════════════════════

def calc_rsi(p,n=14):
    if len(p)<n+1: return 50.0
    g=[max(p[i]-p[i-1],0) for i in range(1,len(p))]
    l=[max(p[i-1]-p[i],0) for i in range(1,len(p))]
    ag=sum(g[-n:])/n; al=sum(l[-n:])/n
    return round(100-(100/(1+ag/al)),2) if al else 100.0

def calc_ema(p,n):
    if len(p)<n: return p
    k=2/(n+1); e=[sum(p[:n])/n]
    for x in p[n:]: e.append(x*k+e[-1]*(1-k))
    return e

def calc_macd(p):
    if len(p)<26: return None,None,None
    e12=calc_ema(p,12); e26=calc_ema(p,26); n=min(len(e12),len(e26))
    ml=[e12[-n+i]-e26[-n+i] for i in range(n)]; sig=calc_ema(ml,9)
    return (round(ml[-1],4),round(sig[-1],4),round(ml[-1]-sig[-1],4)) if sig else (None,None,None)

def calc_bb(p,n=20):
    if len(p)<n: return None,None,None
    r=p[-n:]; m=sum(r)/n; std=(sum((x-m)**2 for x in r)/n)**0.5
    return round(m+2*std,2),round(m,2),round(m-2*std,2)

def calc_atr(h,l,c,n=14):
    if len(c)<n+1: return 0.0
    trs=[max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1])) for i in range(1,len(c))]
    return round(sum(trs[-n:])/n,4)

def calc_stoch(h,l,c,n=14):
    if len(c)<n: return 50.0,50.0
    rh=max(h[-n:]); rl=min(l[-n:])
    if rh==rl: return 50.0,50.0
    k=((c[-1]-rl)/(rh-rl))*100
    rh2=max(h[-n-1:-1]) if len(h)>n else rh; rl2=min(l[-n-1:-1]) if len(l)>n else rl
    k2=((c[-2]-rl2)/(rh2-rl2))*100 if rh2!=rl2 and len(c)>n else k
    return round(k,2),round((k+k2)/2,2)

def calc_vwap(h,l,c,v):
    if not v or sum(v)==0: return c[-1]
    tp=[(h[i]+l[i]+c[i])/3 for i in range(len(c))]
    return round(sum(tp[i]*v[i] for i in range(len(c)))/sum(v),2)

def calc_williams_r(h,l,c,n=14):
    if len(c)<n: return -50.0
    rh=max(h[-n:]); rl=min(l[-n:])
    return round(((rh-c[-1])/(rh-rl))*-100,2) if rh!=rl else -50.0

def calc_cci(h,l,c,n=20):
    if len(c)<n: return 0.0
    tp=[(h[i]+l[i]+c[i])/3 for i in range(len(c))]
    tp_n=tp[-n:]; mean=sum(tp_n)/n
    mad=sum(abs(x-mean) for x in tp_n)/n
    return round((tp[-1]-mean)/(0.015*mad),2) if mad else 0.0

def calc_obv(c,v):
    o=0
    for i in range(1,len(c)):
        if c[i]>c[i-1]: o+=v[i]
        elif c[i]<c[i-1]: o-=v[i]
    return o


# ══════════════════════════════════════════════
# BREAKOUT DETECTION
# ══════════════════════════════════════════════

def detect_breakout(h,l,c,v,n=20) -> dict:
    """كشف كسر مستويات الدعم والمقاومة"""
    if len(c)<n+2: return {"type":"NONE","strength":0}
    resistance = max(h[-n-1:-1])
    support    = min(l[-n-1:-1])
    price      = c[-1]
    avg_vol    = sum(v[-n-1:-1])/n
    curr_vol   = v[-1]
    vol_confirm= curr_vol > avg_vol * 1.5

    if price > resistance * 1.002 and vol_confirm:
        strength = min(int((price/resistance-1)*1000 + (curr_vol/avg_vol-1)*20), 100)
        return {"type":"BULLISH_BREAKOUT","resistance":round(resistance,2),
                "strength":strength,"vol_ratio":round(curr_vol/avg_vol,1)}
    elif price < support * 0.998:
        return {"type":"BREAKDOWN","support":round(support,2),"strength":50}
    elif price > resistance * 0.995:
        return {"type":"TESTING_RESISTANCE","resistance":round(resistance,2),"strength":30}
    return {"type":"NONE","strength":0}


def detect_rsi_divergence(p,h,l,rsi_val,n=10) -> str:
    """كشف Divergence بين السعر والـ RSI"""
    if len(p)<n+2: return "NONE"
    price_making_lower_lows = p[-1] < min(p[-n:-1])
    rsi_values=[calc_rsi(p[:i+1]) for i in range(len(p)-n,len(p))]
    rsi_making_higher_lows = rsi_val > min(rsi_values[:-1]) if rsi_values else False
    if price_making_lower_lows and rsi_making_higher_lows and rsi_val<50:
        return "BULLISH_DIVERGENCE"
    return "NONE"


def detect_patterns(o,h,l,c):
    if len(c)<3: return []
    pts=[]
    p,ph,pl,po=c[-1],h[-1],l[-1],o[-1]
    p2,ph2,pl2,po2=c[-2],h[-2],l[-2],o[-2]
    body=abs(p-po); rng=ph-pl
    body2=abs(p2-po2); rng2=ph2-pl2
    if body>0 and rng>0:
        lw=min(p,po)-pl; uw=ph-max(p,po)
        if lw>=body*2 and uw<=body*0.3 and p>po: pts.append("🔨 Hammer")
    if rng>0 and body/rng<0.1: pts.append("✙ Doji")
    if p>po and p2<po2 and p>po2 and po<p2: pts.append("🕯️ Bullish Engulfing")
    if len(c)>=3:
        p3,ph3,pl3,po3=c[-3],h[-3],l[-3],o[-3]
        if p3<po3 and body2/max(rng2,0.001)<0.3 and p>po and p>(p3+po3)/2:
            pts.append("⭐ Morning Star")
        if p>po and p2>po2 and p3>po3 and p>p2>p3:
            pts.append("💪 Three White Soldiers")
    if p2<po2 and p>po and po<p2 and p>(p2+po2)/2: pts.append("📌 Piercing Line")
    return pts


# ══════════════════════════════════════════════
# FULL ANALYSIS
# ══════════════════════════════════════════════

def analyze_daily(data: dict) -> dict:
    if not data: return {}
    c=data.get("c",[]); o=data.get("o",[])
    h=data.get("h",[]); l=data.get("l",[]); v=data.get("v",[])
    if len(c)<25: return {}

    MC,MS,MH = calc_macd(c)
    BU,BM,BL = calc_bb(c)
    SK,SD    = calc_stoch(h,l,c)
    AT       = calc_atr(h,l,c)
    VP       = calc_vwap(h,l,c,v)
    WR       = calc_williams_r(h,l,c)
    CC       = calc_cci(h,l,c)
    OBV      = calc_obv(c,v)
    PTS      = detect_patterns(o,h,l,c)
    BRK      = detect_breakout(h,l,c,v)
    RSI_V    = calc_rsi(c)
    DIV      = detect_rsi_divergence(c,h,l,RSI_V)

    sma20=round(sum(c[-20:])/20,2) if len(c)>=20 else None
    sma50=round(sum(c[-50:])/50,2) if len(c)>=50 else None
    e9 =round(calc_ema(c,9)[-1],2)  if len(c)>=9  else None
    e21=round(calc_ema(c,21)[-1],2) if len(c)>=21 else None
    e50=round(calc_ema(c,50)[-1],2) if len(c)>=50 else None

    avg_v=sum(v[-20:])/20 if len(v)>=20 else 1
    vr=round(v[-1]/avg_v,2) if avg_v else 1.0
    rvol_5=round(v[-1]/(sum(v[-5:])/5),2) if len(v)>=5 and sum(v[-5:])>0 else 1.0

    chg=round((c[-1]-c[-2])/c[-2]*100,2) if len(c)>1 else 0
    sup=round(min(l[-20:]),2); res_=round(max(h[-20:]),2)
    ts=sum(1 for i in range(1,min(10,len(c))) if c[-i]>c[-i-1])

    # OBV Trend
    obv_values=[calc_obv(c[:i+1],v[:i+1]) for i in range(max(0,len(c)-5),len(c))]
    obv_trend="UP" if len(obv_values)>1 and obv_values[-1]>obv_values[0] else "DOWN"

    return dict(
        price=round(c[-1],2), prev=round(c[-2],2) if len(c)>1 else round(c[-1],2),
        change_pct=chg, high=round(h[-1],2), low=round(l[-1],2), open=round(o[-1],2),
        rsi=RSI_V, macd=MC, macd_signal=MS, macd_hist=MH,
        bb_upper=BU, bb_mid=BM, bb_lower=BL,
        atr=AT, stoch_k=SK, stoch_d=SD, vwap=VP,
        williams_r=WR, cci=CC, obv_trend=obv_trend,
        support=sup, resistance=res_,
        sma20=sma20, sma50=sma50, ema9=e9, ema21=e21, ema50=e50,
        vol_ratio=vr, rvol_5=rvol_5, volume=int(v[-1]),
        patterns=PTS, breakout=BRK, divergence=DIV,
        trend_strength=round(ts/min(9,len(c)-1)*100),
        name=data.get("name",""), mktcap=data.get("mktcap",0)
    )


def analyze_weekly(data: dict) -> dict:
    if not data: return {}
    c=data.get("c",[]); h=data.get("h",[]); l=data.get("l",[]); v=data.get("v",[])
    if len(c)<8: return {}
    return dict(
        rsi_weekly=calc_rsi(c),
        macd_hist_weekly=calc_macd(c)[2],
        trend_weekly="UP" if c[-1]>c[-5] else "DOWN",
        above_sma20_weekly=c[-1]>sum(c[-20:])/20 if len(c)>=20 else False
    )


# ══════════════════════════════════════════════
# MULTI-LAYER SCORING ENGINE
# ══════════════════════════════════════════════

def super_score(t: dict, w: dict, fg: dict, av_rsi: float|None, av_macd: dict, earnings: dict) -> tuple:
    if not t: return 0,"NONE",[],0

    s=0; R=[]; confirmations=0; price=t.get("price",0)
    r=t.get("rsi",50)

    # ══ Layer 1: RSI (محلي + Alpha Vantage تحقق مزدوج) ══
    av_r=av_rsi if av_rsi else r
    rsi_avg=round((r+av_r)/2,1)
    if rsi_avg<25:   s+=22; R.append(f"RSI={rsi_avg} 🔥 ذروة بيع قصوى"); confirmations+=1
    elif rsi_avg<32: s+=16; R.append(f"RSI={rsi_avg} ذروة بيع قوية"); confirmations+=1
    elif rsi_avg<40: s+=9;  R.append(f"RSI={rsi_avg} منطقة شراء")
    elif rsi_avg>75: s-=18
    elif rsi_avg>65: s-=10

    # ══ Layer 2: MACD (محلي + AV تحقق مزدوج) ══
    h_=t.get("macd_hist"); mc=t.get("macd")
    av_h=av_macd.get("hist"); av_ok=av_h is not None and av_h>0
    if h_ is not None:
        if h_>0 and av_ok: s+=18; R.append("MACD ✅✅ تأكيد مزدوج صاعد"); confirmations+=1
        elif h_>0:         s+=10; R.append("MACD ✅ تقاطع صاعد")
        elif h_<0:         s-=10

    # ══ Layer 3: Stochastic ══
    k=t.get("stoch_k",50); d=t.get("stoch_d",50)
    if k<15 and d<15:   s+=16; R.append(f"Stoch={k:.0f} 🔥 ذروة بيع قصوى"); confirmations+=1
    elif k<25 and d<25: s+=11; R.append(f"Stoch={k:.0f} ذروة بيع"); confirmations+=1
    elif k>d and k<40:  s+=7;  R.append("Stoch تقاطع صاعد")
    elif k>80:          s-=12

    # ══ Layer 4: Bollinger Bands ══
    bbl=t.get("bb_lower"); bbu=t.get("bb_upper")
    if bbl and price<=bbl:        s+=16; R.append("🎯 كسر الباند السفلي — فرصة انعكاس"); confirmations+=1
    elif bbl and price<=bbl*1.01: s+=11; R.append("عند الباند السفلي"); confirmations+=1
    elif bbu and price>=bbu*0.99: s-=14

    # ══ Layer 5: Williams %R ══
    wr=t.get("williams_r",-50)
    if wr<-80:   s+=10; R.append(f"Williams%R={wr} ذروة بيع"); confirmations+=1
    elif wr<-60: s+=5
    elif wr>-20: s-=8

    # ══ Layer 6: CCI ══
    cc=t.get("cci",0)
    if cc<-150:   s+=10; R.append(f"CCI={cc:.0f} ذروة بيع"); confirmations+=1
    elif cc<-100: s+=6
    elif cc>150:  s-=8

    # ══ Layer 7: Moving Averages ══
    s20=t.get("sma20"); s50=t.get("sma50")
    e9=t.get("ema9"); e21=t.get("ema21"); e50=t.get("ema50")
    if s20 and s50 and s20>s50: s+=6; R.append("SMA20>SMA50 اتجاه صاعد")
    if e9 and e21 and e9>e21:   s+=5; R.append("EMA9>EMA21 زخم")
    if e21 and e50 and e21>e50: s+=4; R.append("EMA21>EMA50")

    # ══ Layer 8: VWAP ══
    vp=t.get("vwap")
    if vp and price>vp: s+=7; R.append(f"فوق VWAP ${vp}")
    elif vp and price<vp*0.99: s-=5

    # ══ Layer 9: Support/Resistance ══
    sup=t.get("support"); res=t.get("resistance")
    if sup and price<=sup*1.02: s+=8; R.append(f"قرب الدعم ${sup} 🛡️"); confirmations+=1
    if res and price>=res*0.98: s-=8

    # ══ Layer 10: Volume (Relative Volume) ══
    vr=t.get("vol_ratio",1); rvol5=t.get("rvol_5",1)
    if vr>=3.0 and rvol5>=2.0: s+=16; R.append(f"حجم انفجاري {vr:.1f}x 🔥🔥🔥"); confirmations+=1
    elif vr>=2.0:               s+=11; R.append(f"حجم {vr:.1f}x 🔥🔥")
    elif vr>=1.5:               s+=7;  R.append(f"حجم {vr:.1f}x 🔥")
    elif vr<0.5:                s-=10

    # ══ Layer 11: Breakout Detection ══
    brk=t.get("breakout",{})
    if brk.get("type")=="BULLISH_BREAKOUT":
        s+=15; R.append(f"🚀 Breakout! كسر مقاومة ${brk.get('resistance')} بحجم {brk.get('vol_ratio')}x"); confirmations+=1
    elif brk.get("type")=="TESTING_RESISTANCE":
        R.append(f"⚡ اختبار مقاومة ${brk.get('resistance')}")

    # ══ Layer 12: RSI Divergence ══
    div=t.get("divergence","NONE")
    if div=="BULLISH_DIVERGENCE":
        s+=12; R.append("📈 Bullish Divergence — انعكاس قادم"); confirmations+=1

    # ══ Layer 13: Candlestick Patterns ══
    pts=t.get("patterns",[])
    if pts: s+=min(len(pts)*5,12); R.append(f"نمط شموع: {', '.join(pts)}"); confirmations+=1

    # ══ Layer 14: OBV Trend ══
    obv=t.get("obv_trend","")
    if obv=="UP": s+=6; R.append("OBV صاعد — smart money يشتري")
    elif obv=="DOWN": s-=5

    # ══ Layer 15: Multi-Timeframe (Weekly) ══
    if w:
        w_rsi=w.get("rsi_weekly",50); w_trend=w.get("trend_weekly","")
        w_macd_h=w.get("macd_hist_weekly")
        if w_rsi<40 and w_trend=="UP":   s+=10; R.append(f"الأسبوعي: RSI={w_rsi} مع اتجاه صاعد ✅"); confirmations+=1
        elif w_rsi<40:                   s+=5;  R.append(f"الأسبوعي: RSI={w_rsi} منطقة شراء")
        if w_macd_h and w_macd_h>0:     s+=6;  R.append("MACD الأسبوعي إيجابي ✅")
        if w.get("above_sma20_weekly"):  s+=4

    # ══ Layer 16: Fear & Greed ══
    fg_val=fg.get("value",50)
    if fg_val<=25:   s+=10; R.append(f"خوف شديد {fg_val:.0f} 😱 — فرصة تاريخية"); confirmations+=1
    elif fg_val<=40: s+=5;  R.append(f"خوف {fg_val:.0f} 😟 — فرصة جيدة")
    elif fg_val>=80: s-=8;  R.append(f"طمع شديد {fg_val:.0f} 🤑 — خطر")
    elif fg_val>=65: s-=4

    # ══ Layer 17: Earnings Risk ══
    if earnings:
        days=earnings.get("days_until",99)
        if days is not None:
            if 0<=days<=3:    s-=15; R.append(f"⚠️ نتائج خلال {days} أيام — مخاطرة عالية")
            elif 4<=days<=7:  s-=8;  R.append(f"⚠️ نتائج خلال أسبوع")
            elif days<0:      R.append(f"✅ النتائج صدرت منذ {abs(days)} يوم")

    # ══ Layer 18: Trend Strength ══
    ts=t.get("trend_strength",50)
    if ts>=70: s+=8; R.append(f"قوة اتجاه {ts}% صاعد")
    elif ts<=30: s-=6

    # ══ تحديد الإشارة ══
    signal="NONE"
    conf_ok = confirmations >= MIN_CONFIRMATIONS
    if s>=MIN_SCORE and conf_ok:
        brk_type=brk.get("type","")
        if brk_type=="BULLISH_BREAKOUT":        signal="BREAKOUT_BUY"
        elif div=="BULLISH_DIVERGENCE":          signal="DIVERGENCE_BUY"
        elif r<35 and bbl and price<=bbl*1.02:  signal="SWING_BUY"
        elif h_ and h_>0 and vr>=1.5:           signal="DAY_BUY"
        elif pts and r<45:                      signal="PATTERN_BUY"
        else:                                   signal="BUY"

    return min(s,100), signal, R, confirmations


def calc_targets(t: dict) -> dict:
    p=t.get("price",0); at=t.get("atr",p*0.015)
    sup=t.get("support",p*0.97); bbl=t.get("bb_lower",p*0.97)
    s20=t.get("sma20",p*0.98)
    sl=round(min(p-at*1.5,sup*0.995,bbl*0.995,s20*0.995),2)
    risk=max(p-sl,p*0.01)
    tp1=round(p+risk*1.5,2); tp2=round(p+risk*2.5,2)
    tp3=round(p+risk*4.0,2); tp4=round(p+risk*6.0,2)
    res=t.get("resistance")
    if res and res>p: tp3=round(min(tp3,res*0.995),2)
    return dict(entry=p,sl=sl,tp1=tp1,tp2=tp2,tp3=tp3,tp4=tp4,
                risk_pct=round(risk/p*100,2),rr=round(risk*4.0/risk,1))


# ══════════════════════════════════════════════
# CLAUDE AI — تحليل نهائي متعمق
# ══════════════════════════════════════════════

async def claude_final_verdict(sym,t,w,sc,conf,reasons,tgt,sent,fg,earnings,av_rsi,av_macd):
    ses=get_session()
    ss=sent.get("companyNewsScore",0.5) if sent else 0.5
    sl_="إيجابي 😊" if ss>0.6 else "سلبي 😟" if ss<0.4 else "محايد 😐"
    fg_val=fg.get("value",50); fg_label=fg.get("label","محايد")
    earnings_info=f"نتائج خلال {earnings.get('days_until')} يوم (EPS تقدير: {earnings.get('estimate')})" if earnings else "لا نتائج قريبة"
    w_info=f"RSI أسبوعي:{w.get('rsi_weekly',50)} اتجاه:{w.get('trend_weekly','N/A')}" if w else "N/A"

    prompt=(
        f"أنت كبير محللي التداول في وول ستريت. مهمتك تحليل هذه الفرصة بدقة عالية جداً وتقديم حكم نهائي.\n\n"
        f"═══ {sym} ({t.get('name','')}) ═══\n"
        f"الجلسة: {ses} | النقاط: {sc}/100 | التأكيدات: {conf}/{MIN_CONFIRMATIONS}\n"
        f"السعر: ${t.get('price')} | التغيير: {t.get('change_pct')}%\n\n"
        f"═══ مؤشرات يومية ═══\n"
        f"RSI محلي: {t.get('rsi')} | RSI Alpha Vantage: {av_rsi}\n"
        f"MACD_Hist محلي: {t.get('macd_hist')} | MACD_Hist AV: {av_macd.get('hist')}\n"
        f"Stoch: {t.get('stoch_k')} | Williams%R: {t.get('williams_r')} | CCI: {t.get('cci')}\n"
        f"BB: Upper=${t.get('bb_upper')} Lower=${t.get('bb_lower')}\n"
        f"VWAP: ${t.get('vwap')} | دعم: ${t.get('support')} | مقاومة: ${t.get('resistance')}\n"
        f"Vol_Ratio: {t.get('vol_ratio')}x | RVOL5: {t.get('rvol_5')}x | OBV: {t.get('obv_trend')}\n"
        f"Breakout: {t.get('breakout',{}).get('type')} | Divergence: {t.get('divergence')}\n"
        f"أنماط: {', '.join(t.get('patterns',[]))} | اتجاه: {t.get('trend_strength')}%\n\n"
        f"═══ متعدد الأطر ═══\n"
        f"{w_info}\n\n"
        f"═══ بيانات خارجية ═══\n"
        f"Fear & Greed: {fg_val:.0f} ({fg_label})\n"
        f"مشاعر الأخبار: {sl_} ({ss:.2f})\n"
        f"الأرباح: {earnings_info}\n\n"
        f"═══ الأهداف ═══\n"
        f"دخول: ${tgt['entry']} | SL: ${tgt['sl']} | TP1-4: ${tgt['tp1']}/${tgt['tp2']}/${tgt['tp3']}/${tgt['tp4']}\n"
        f"مخاطرة: {tgt['risk_pct']}% | R:R = 1:{tgt['rr']}\n\n"
        f"الأسباب الرئيسية: {' | '.join(reasons[:5])}\n\n"
        f"قدّم تحليلك بهذا التنسيق الدقيق:\n"
        f"VERDICT: [✅ قوية جداً / ✅ قوية / ⚠️ متوسطة / ❌ ضعيفة]\n"
        f"CONFIDENCE: [نسبة ثقتك % من 0 إلى 100]\n"
        f"ANALYSIS: [3 جمل: المشهد التقني، أسباب الدخول، حالة السوق العامة]\n"
        f"RISK: [أهم مخاطرتين]\n"
        f"TIMING: [الآن / انتظر تراجع إلى X$ / تجنب]\n"
        f"ACTION: [ادخل الآن / انتظر / تجنب]"
    )
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post("https://api.anthropic.com/v1/messages",
                headers={"x-api-key":ANTHROPIC_API_KEY,"anthropic-version":"2023-06-01","content-type":"application/json"},
                json={"model":"claude-haiku-4-5-20251001","max_tokens":400,
                      "messages":[{"role":"user","content":prompt}]},
                timeout=aiohttp.ClientTimeout(total=30)) as r:
                data=await r.json()
        if "content" not in data: return "⚠️ AI غير متاح",True
        txt=data["content"][0]["text"]
        approved=("تجنب" not in txt.split("ACTION:")[-1] and
                  "❌" not in txt.split("VERDICT:")[-1][:30])
        return txt,approved
    except Exception as e:
        return f"⚠️ {e}",True


# ══════════════════════════════════════════════
# ALERT FORMATTER
# ══════════════════════════════════════════════

def format_alert(sym,t,sc,sig,conf,reasons,tgt,ai,fg,earnings):
    ses=get_session()
    grade="🏆 A+" if sc>=90 else "⭐ A" if sc>=82 else "✅ B+" if sc>=78 else "📊 B"
    sig_label={
        "BREAKOUT_BUY":"🚀 Breakout","DIVERGENCE_BUY":"📈 Divergence",
        "SWING_BUY":"🔄 Swing","DAY_BUY":"⚡ Day Trade",
        "PATTERN_BUY":"🕯️ Pattern","BUY":"📈 Buy"
    }.get(sig,"📈")
    ep=tgt['entry']; pct=lambda x:f"+{round((x-ep)/ep*100,1)}%"
    name=f" ({t.get('name','')})" if t.get('name') else ""
    fg_line=f"😱 خوف شديد {fg.get('value',50):.0f}" if fg.get('value',50)<=25 else f"😟 خوف {fg.get('value',50):.0f}" if fg.get('value',50)<=40 else ""
    earn_line=f"⚠️ نتائج خلال {earnings.get('days_until')} يوم" if earnings and earnings.get('days_until',99)<=7 else ""

    msg=(
        f"{'🚀' if sc>=85 else '📊'} *{sym}{name}*\n"
        f"{'━'*22}\n"
        f"{grade} | {sig_label} | *{sc}/100*\n"
        f"✅ تأكيدات: {conf}/{MIN_CONFIRMATIONS} | {ses}\n"
        f"💵 `${t['price']}` | {'+' if t['change_pct']>=0 else ''}{t['change_pct']}%"
        f" | Vol:{t.get('vol_ratio')}x\n"
    )
    if fg_line: msg+=f"📊 {fg_line}\n"
    if earn_line: msg+=f"{earn_line}\n"
    msg+=(
        f"\n🎯 *الأهداف:*\n"
        f"  📍 دخول: `${ep}`\n"
        f"  🥉 TP1: `${tgt['tp1']}` ({pct(tgt['tp1'])})\n"
        f"  🥈 TP2: `${tgt['tp2']}` ({pct(tgt['tp2'])})\n"
        f"  🥇 TP3: `${tgt['tp3']}` ({pct(tgt['tp3'])})\n"
        f"  💎 TP4: `${tgt['tp4']}` ({pct(tgt['tp4'])})\n"
        f"  🛑 SL: `${tgt['sl']}` (-{tgt['risk_pct']}%)\n"
        f"  📐 R:R = 1:{tgt['rr']}\n\n"
        f"📊 *المؤشرات:*\n"
        f"  RSI:{t.get('rsi')} | Stoch:{t.get('stoch_k')} | WR:{t.get('williams_r')}\n"
        f"  MACD:{t.get('macd_hist')} | CCI:{t.get('cci')} | VWAP:${t.get('vwap')}\n"
        f"  دعم:${t.get('support')} | مقاومة:${t.get('resistance')}\n"
        f"  OBV:{t.get('obv_trend')} | Div:{t.get('divergence')}\n"
    )
    brk=t.get("breakout",{})
    if brk.get("type")=="BULLISH_BREAKOUT":
        msg+=f"  🚀 Breakout! مقاومة ${brk.get('resistance')} | حجم {brk.get('vol_ratio')}x\n"
    if t.get("patterns"):
        msg+=f"  🕯️ {' | '.join(t.get('patterns',[]))}\n"
    msg+=(
        f"\n🧠 *تحليل Claude AI:*\n{ai}\n\n"
        f"💡 *الأسباب ({len(reasons)}):*\n"
        +"\n".join(f"  • {r}" for r in reasons[:6])
    )
    return msg


# ══════════════════════════════════════════════
# SCANNER
# ══════════════════════════════════════════════

async def scan_one(sym):
    try:
        data=await get_yahoo_data(sym)
        t=analyze_daily(data)
        if not t: return None
        r=t.get("rsi",50); vr=t.get("vol_ratio",1)
        if r>60 and vr<1.1: return None  # فلتر سريع
        # نقيّم أولاً بدون AV لتوفير API calls
        sc_pre,sig_pre,_,conf_pre=super_score(t,{},{},{},None,{},)
        if sc_pre<MIN_SCORE-15: return None
        return t
    except Exception as e:
        log.debug(f"scan {sym}: {e}"); return None

# إصلاح استدعاء super_score بدون w و fg
def super_score(t,w,fg,av_rsi,av_macd,earnings):
    if not t: return 0,"NONE",[],0
    s=0; R=[]; confirmations=0; price=t.get("price",0)
    r=t.get("rsi",50)
    av_r=av_rsi if av_rsi else r
    rsi_avg=round((r+av_r)/2,1)
    if rsi_avg<25:   s+=22; R.append(f"RSI={rsi_avg} 🔥 ذروة بيع قصوى"); confirmations+=1
    elif rsi_avg<32: s+=16; R.append(f"RSI={rsi_avg} ذروة بيع قوية"); confirmations+=1
    elif rsi_avg<40: s+=9;  R.append(f"RSI={rsi_avg} منطقة شراء")
    elif rsi_avg>75: s-=18
    elif rsi_avg>65: s-=10
    h_=t.get("macd_hist"); mc=t.get("macd")
    av_h=av_macd.get("hist") if av_macd else None
    av_ok=av_h is not None and av_h>0
    if h_ is not None:
        if h_>0 and av_ok: s+=18; R.append("MACD ✅✅ تأكيد مزدوج"); confirmations+=1
        elif h_>0:         s+=10; R.append("MACD ✅ تقاطع صاعد")
        elif h_<0:         s-=10
    k=t.get("stoch_k",50); d=t.get("stoch_d",50)
    if k<15 and d<15:   s+=16; R.append(f"Stoch={k:.0f} 🔥 ذروة بيع قصوى"); confirmations+=1
    elif k<25 and d<25: s+=11; R.append(f"Stoch={k:.0f} ذروة بيع"); confirmations+=1
    elif k>d and k<40:  s+=7;  R.append("Stoch تقاطع صاعد")
    elif k>80:          s-=12
    bbl=t.get("bb_lower"); bbu=t.get("bb_upper")
    if bbl and price<=bbl:        s+=16; R.append("🎯 كسر الباند السفلي"); confirmations+=1
    elif bbl and price<=bbl*1.01: s+=11; R.append("عند الباند السفلي"); confirmations+=1
    elif bbu and price>=bbu*0.99: s-=14
    wr=t.get("williams_r",-50)
    if wr<-80:   s+=10; R.append(f"Williams%R={wr} ذروة بيع"); confirmations+=1
    elif wr<-60: s+=5
    elif wr>-20: s-=8
    cc=t.get("cci",0)
    if cc<-150:   s+=10; R.append(f"CCI={cc:.0f} ذروة بيع"); confirmations+=1
    elif cc<-100: s+=6
    elif cc>150:  s-=8
    s20=t.get("sma20"); s50=t.get("sma50")
    e9=t.get("ema9"); e21=t.get("ema21"); e50=t.get("ema50")
    if s20 and s50 and s20>s50: s+=6; R.append("SMA20>SMA50")
    if e9 and e21 and e9>e21:   s+=5; R.append("EMA9>EMA21 زخم")
    if e21 and e50 and e21>e50: s+=4; R.append("EMA21>EMA50")
    vp=t.get("vwap")
    if vp and price>vp: s+=7; R.append(f"فوق VWAP ${vp}")
    elif vp and price<vp*0.99: s-=5
    sup=t.get("support"); res=t.get("resistance")
    if sup and price<=sup*1.02: s+=8; R.append(f"قرب الدعم ${sup} 🛡️"); confirmations+=1
    if res and price>=res*0.98: s-=8
    vr=t.get("vol_ratio",1); rvol5=t.get("rvol_5",1)
    if vr>=3.0 and rvol5>=2.0: s+=16; R.append(f"حجم انفجاري {vr:.1f}x 🔥🔥🔥"); confirmations+=1
    elif vr>=2.0:               s+=11; R.append(f"حجم {vr:.1f}x 🔥🔥")
    elif vr>=1.5:               s+=7;  R.append(f"حجم {vr:.1f}x 🔥")
    elif vr<0.5:                s-=10
    brk=t.get("breakout",{})
    if brk.get("type")=="BULLISH_BREAKOUT":
        s+=15; R.append(f"🚀 Breakout! كسر ${brk.get('resistance')} بحجم {brk.get('vol_ratio')}x"); confirmations+=1
    div=t.get("divergence","NONE")
    if div=="BULLISH_DIVERGENCE":
        s+=12; R.append("📈 Bullish Divergence"); confirmations+=1
    pts=t.get("patterns",[])
    if pts: s+=min(len(pts)*5,12); R.append(f"نمط: {', '.join(pts)}"); confirmations+=1
    obv=t.get("obv_trend","")
    if obv=="UP": s+=6; R.append("OBV صاعد — smart money يشتري")
    elif obv=="DOWN": s-=5
    if w:
        w_rsi=w.get("rsi_weekly",50); w_trend=w.get("trend_weekly","")
        w_macd_h=w.get("macd_hist_weekly")
        if w_rsi<40 and w_trend=="UP":  s+=10; R.append(f"الأسبوعي: RSI={w_rsi} صاعد ✅"); confirmations+=1
        elif w_rsi<40:                  s+=5;  R.append(f"الأسبوعي: RSI={w_rsi}")
        if w_macd_h and w_macd_h>0:    s+=6;  R.append("MACD أسبوعي ✅")
        if w.get("above_sma20_weekly"): s+=4
    fg_val=fg.get("value",50) if fg else 50
    if fg_val<=25:   s+=10; R.append(f"خوف شديد {fg_val:.0f} 😱 — فرصة تاريخية"); confirmations+=1
    elif fg_val<=40: s+=5;  R.append(f"خوف {fg_val:.0f} 😟")
    elif fg_val>=80: s-=8
    elif fg_val>=65: s-=4
    if earnings:
        days=earnings.get("days_until",99)
        if days is not None:
            if 0<=days<=3:   s-=15; R.append(f"⚠️ نتائج خلال {days} أيام — خطر")
            elif 4<=days<=7: s-=8;  R.append(f"⚠️ نتائج خلال أسبوع")
    ts=t.get("trend_strength",50)
    if ts>=70: s+=8; R.append(f"قوة اتجاه {ts}%")
    elif ts<=30: s-=6
    signal="NONE"
    conf_ok=confirmations>=MIN_CONFIRMATIONS
    if s>=MIN_SCORE and conf_ok:
        brk_type=brk.get("type","")
        if brk_type=="BULLISH_BREAKOUT":       signal="BREAKOUT_BUY"
        elif div=="BULLISH_DIVERGENCE":         signal="DIVERGENCE_BUY"
        elif rsi_avg<35 and bbl and price<=bbl*1.02: signal="SWING_BUY"
        elif h_ and h_>0 and vr>=1.5:          signal="DAY_BUY"
        elif pts and rsi_avg<45:               signal="PATTERN_BUY"
        else:                                  signal="BUY"
    return min(s,100),signal,R,confirmations


async def run_scanner(app):
    global alert_count_this_hour,last_hour_reset,scanning_active,last_scan_stats
    log.info("🚀 Ultimate Scanner v4.0 started")

    while True:
        if not scanning_active:
            await asyncio.sleep(60); continue
        if time.time()-last_hour_reset>3600:
            alert_count_this_hour=0; last_hour_reset=time.time()

        ses=get_session()
        fg=await get_fear_greed()
        log.info(f"🔍 Scanning {len(WATCHLIST)} | {ses} | F&G:{fg.get('value',50):.0f}")
        start=time.time(); opps=0; sent_c=0; rej=0

        for i in range(0,len(WATCHLIST),5):
            if alert_count_this_hour>=MAX_ALERTS_PER_HOUR: break
            batch=WATCHLIST[i:i+5]

            for sym in batch:
                try:
                    # مرحلة 1: تحليل يومي سريع
                    data=await get_yahoo_data(sym)
                    t=analyze_daily(data)
                    if not t: continue

                    # فلتر أولي
                    r=t.get("rsi",50); vr=t.get("vol_ratio",1)
                    if r>62 and vr<1.1: continue

                    # مرحلة 2: تقييم أولي
                    sc_pre,sig_pre,_,conf_pre=super_score(t,{},fg,None,{},{})
                    if sc_pre<MIN_SCORE-10: continue

                    opps+=1
                    if time.time()-alerted_symbols.get(sym,0)<COOLDOWN_HOURS*3600: continue

                    # مرحلة 3: جلب بيانات إضافية للأسهم المؤهلة
                    weekly_data,sent_d,earnings_d,av_rsi_d,av_macd_d=await asyncio.gather(
                        get_yahoo_weekly(sym),
                        get_finnhub_sentiment(sym),
                        get_earnings_calendar(sym),
                        get_av_rsi(sym),
                        get_av_macd(sym),
                        return_exceptions=True
                    )
                    if isinstance(weekly_data,Exception): weekly_data={}
                    if isinstance(sent_d,Exception): sent_d={}
                    if isinstance(earnings_d,Exception): earnings_d={}
                    if isinstance(av_rsi_d,Exception): av_rsi_d=None
                    if isinstance(av_macd_d,Exception): av_macd_d={}

                    w=analyze_weekly(weekly_data) if weekly_data else {}

                    # مرحلة 4: تقييم كامل مع كل البيانات
                    sc,sig,reasons,conf=super_score(t,w,fg,av_rsi_d,av_macd_d,earnings_d)
                    if sc<MIN_SCORE or sig=="NONE": continue

                    tgt=calc_targets(t)

                    # مرحلة 5: Claude AI الحكم النهائي
                    ai,approved=await claude_final_verdict(
                        sym,t,w,sc,conf,reasons,tgt,sent_d,fg,earnings_d,av_rsi_d,av_macd_d)

                    if not approved:
                        log.info(f"🚫 AI rejected {sym}"); rej+=1; continue

                    msg=format_alert(sym,t,sc,sig,conf,reasons,tgt,ai,fg,earnings_d)
                    ok=False
                    for cid in ALERT_CHAT_IDS:
                        try:
                            await app.bot.send_message(cid,msg,parse_mode="Markdown"); ok=True
                        except Exception as e:
                            log.error(f"send {cid}: {e}")
                    if ok:
                        alerted_symbols[sym]=time.time()
                        alert_count_this_hour+=1; sent_c+=1
                        log.info(f"✅ ALERT: {sym} sc={sc} sig={sig} conf={conf}")

                except Exception as e:
                    log.debug(f"Error {sym}: {e}")

            await asyncio.sleep(2)

        elapsed=round(time.time()-start)
        last_scan_stats={"scanned":len(WATCHLIST),"opportunities":opps,
                         "sent":sent_c,"rejected":rej,"time":f"{elapsed}s"}
        log.info(f"✅ Scan done | opps={opps} sent={sent_c} rej={rej} time={elapsed}s")
        await asyncio.sleep(SCAN_INTERVAL_SEC)


# ══════════════════════════════════════════════
# TELEGRAM HANDLERS
# ══════════════════════════════════════════════

async def start(update,ctx):
    cid=update.effective_chat.id
    kb=[[InlineKeyboardButton("📡 تفعيل التنبيهات",callback_data="sub"),
         InlineKeyboardButton("🔕 إيقاف",callback_data="unsub")],
        [InlineKeyboardButton("🔍 فحص سريع",callback_data="scan"),
         InlineKeyboardButton("📊 حالة السوق",callback_data="status")],
        [InlineKeyboardButton("🏆 أفضل فرصة",callback_data="best"),
         InlineKeyboardButton("😱 Fear & Greed",callback_data="fg")],
        [InlineKeyboardButton("⚙️ الإعدادات",callback_data="settings")]]
    await update.message.reply_text(
        f"🤖 *بوت التداول الخارق v4.0*\n\n"
        f"🔍 {len(WATCHLIST)} سهم أمريكي\n"
        f"⏰ 24/7 جميع الجلسات\n"
        f"🧠 18 طبقة تحليل + Claude AI\n"
        f"📊 تحقق مزدوج: Yahoo + Alpha Vantage\n"
        f"🚀 Breakout Detection\n"
        f"📈 RSI Divergence\n"
        f"📅 Earnings Calendar\n"
        f"😱 Fear & Greed Index\n"
        f"🕯️ أنماط الشموع\n"
        f"📆 تحليل متعدد الأطر (يومي + أسبوعي)\n\n"
        f"🆔 Chat ID: `{cid}`\n\n"
        f"👇 *تفعيل التنبيهات* للبدء",
        parse_mode="Markdown",reply_markup=InlineKeyboardMarkup(kb))


async def btn(update,ctx):
    q=update.callback_query; await q.answer(); cid=q.message.chat_id

    if q.data=="sub":
        if cid not in ALERT_CHAT_IDS: ALERT_CHAT_IDS.append(cid)
        await q.edit_message_text(
            f"✅ *تم تفعيل التنبيهات!*\n\n"
            f"🔍 {len(WATCHLIST)} سهم تحت المراقبة\n"
            f"⏱️ فحص كل {SCAN_INTERVAL_SEC//60} دقائق\n"
            f"📊 الحد الأدنى: {MIN_SCORE}/100\n"
            f"✅ تأكيدات مطلوبة: {MIN_CONFIRMATIONS}+\n"
            f"🧠 18 طبقة تحليل + Claude AI\n\n"
            f"سيصلك تنبيه عند اكتشاف فرصة عالية الدقة 🎯",
            parse_mode="Markdown")
    elif q.data=="unsub":
        if cid in ALERT_CHAT_IDS: ALERT_CHAT_IDS.remove(cid)
        await q.edit_message_text("🔕 تم إيقاف التنبيهات.")
    elif q.data=="fg":
        fg=await get_fear_greed()
        val=fg.get("value",50); label=fg.get("label","محايد")
        advice="🟢 وقت ممتاز للشراء" if val<=25 else "🟡 فرصة جيدة" if val<=40 else "🔴 كن حذراً" if val>=75 else "⚪ محايد"
        await q.edit_message_text(
            f"😱 *Fear & Greed Index*\n\n"
            f"القيمة: *{val:.0f}/100*\n"
            f"الحالة: {label}\n"
            f"التوصية: {advice}\n\n"
            f"_يُحدَّث كل ساعة_",parse_mode="Markdown")
    elif q.data=="status":
        ses=get_session(); st=last_scan_stats; fg=fear_greed_cache
        await q.edit_message_text(
            f"📊 *حالة البوت v4.0*\n\n"
            f"⏰ {ses}\n🔍 الأسهم: {len(WATCHLIST)}\n"
            f"📡 {'🟢 نشط' if scanning_active else '🔴 متوقف'}\n"
            f"🔔 تنبيهات/ساعة: {alert_count_this_hour}/{MAX_ALERTS_PER_HOUR}\n"
            f"😱 F&G: {fg.get('value',50):.0f} {fg.get('label','')}\n\n"
            f"📈 *آخر فحص:*\n"
            f"  فُحص:{st.get('scanned',0)} | فرص:{st.get('opportunities',0)}\n"
            f"  أُرسل:{st.get('sent',0)} | رُفض AI:{st.get('rejected',0)}\n"
            f"  وقت:{st.get('time','—')}\n\n"
            f"🕐 {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC",
            parse_mode="Markdown")
    elif q.data=="settings":
        await q.edit_message_text(
            f"⚙️ *إعدادات v4.0*\n\n"
            f"📊 الحد الأدنى: {MIN_SCORE}/100\n"
            f"✅ تأكيدات مطلوبة: {MIN_CONFIRMATIONS}+\n"
            f"⏱️ المسح: كل {SCAN_INTERVAL_SEC//60} دقيقة\n"
            f"🔔 حد/ساعة: {MAX_ALERTS_PER_HOUR}\n"
            f"⏳ كولداون: {COOLDOWN_HOURS} ساعات\n\n"
            f"*المصادر:*\n"
            f"  📈 Yahoo Finance ✅\n"
            f"  📊 Alpha Vantage ✅\n"
            f"  📰 Finnhub Sentiment ✅\n"
            f"  😱 Fear & Greed CNN ✅\n"
            f"  📅 Earnings Calendar ✅\n\n"
            f"*التحليلات:*\n"
            f"  🔢 18 طبقة تحليل\n"
            f"  📆 يومي + أسبوعي\n"
            f"  🚀 Breakout Detection ✅\n"
            f"  📈 RSI Divergence ✅\n"
            f"  🕯️ أنماط الشموع ✅\n"
            f"  🧠 Claude AI ✅",
            parse_mode="Markdown")
    elif q.data=="scan":
        await q.edit_message_text("⏳ فحص سريع لأفضل 30 سهم...")
        fg=await get_fear_greed(); found=[]
        for sym in WATCHLIST[:30]:
            try:
                data=await get_yahoo_data(sym)
                t=analyze_daily(data)
                if not t: continue
                sc,sig,reasons,conf=super_score(t,{},fg,None,{},{})
                if sc>=MIN_SCORE-10 and sig!="NONE":
                    ic={"BREAKOUT_BUY":"🚀","SWING_BUY":"🔄","DAY_BUY":"⚡","DIVERGENCE_BUY":"📈","PATTERN_BUY":"🕯️"}.get(sig,"📈")
                    found.append((sc,f"{ic} *{sym}* {sc}/100 | ${t['price']} | conf:{conf} | Vol:{t.get('vol_ratio')}x"))
            except: pass
        found.sort(key=lambda x:-x[0])
        msg=("🔍 *أفضل الفرص:*\n\n"+"\n".join(x[1] for x in found[:8])+"\n\n_أرسل رمز السهم لتحليل كامل_") if found else "🔍 لا توجد فرص قوية حالياً."
        await q.edit_message_text(msg,parse_mode="Markdown")
    elif q.data=="best":
        await q.edit_message_text("🏆 أبحث عن أفضل فرصة...")
        fg=await get_fear_greed(); best=None; bs=0
        for sym in WATCHLIST[:60]:
            try:
                data=await get_yahoo_data(sym)
                t=analyze_daily(data)
                if not t: continue
                sc,sig,reasons,conf=super_score(t,{},fg,None,{},{})
                if sc>bs and sig!="NONE": bs=sc; best=(sym,sc,sig,t,reasons,conf)
            except: pass
        if best:
            sym,sc,sig,t,reasons,conf=best
            w_d=await get_yahoo_weekly(sym)
            w=analyze_weekly(w_d) if w_d else {}
            sent=await get_finnhub_sentiment(sym)
            earn=await get_earnings_calendar(sym)
            av_r=await get_av_rsi(sym)
            av_m=await get_av_macd(sym)
            sc,sig,reasons,conf=super_score(t,w,fg,av_r,av_m,earn)
            tgt=calc_targets(t)
            ai,_=await claude_final_verdict(sym,t,w,sc,conf,reasons,tgt,sent,fg,earn,av_r,av_m)
            await q.edit_message_text(format_alert(sym,t,sc,sig,conf,reasons,tgt,ai,fg,earn),parse_mode="Markdown")
        else:
            await q.edit_message_text("🏆 لا توجد فرص بارزة حالياً.")


async def analyze_cmd(update,ctx):
    sym=update.message.text.strip().upper()
    if not sym.isalpha() or len(sym)>6: return
    msg=await update.message.reply_text(f"🔍 تحليل شامل لـ *{sym}*...",parse_mode="Markdown")
    try:
        data=await get_yahoo_data(sym)
        if not data:
            await msg.edit_text(f"❌ لم أجد بيانات لـ `{sym}`"); return
        t=analyze_daily(data)
        if not t:
            await msg.edit_text(f"❌ بيانات غير كافية لـ `{sym}`"); return

        fg=await get_fear_greed()
        w_d,sent,earn,av_r,av_m=await asyncio.gather(
            get_yahoo_weekly(sym),get_finnhub_sentiment(sym),
            get_earnings_calendar(sym),get_av_rsi(sym),get_av_macd(sym),
            return_exceptions=True)
        if isinstance(w_d,Exception): w_d={}
        if isinstance(sent,Exception): sent={}
        if isinstance(earn,Exception): earn={}
        if isinstance(av_r,Exception): av_r=None
        if isinstance(av_m,Exception): av_m={}
        w=analyze_weekly(w_d) if w_d else {}
        sc,sig,reasons,conf=super_score(t,w,fg,av_r,av_m,earn)
        tgt=calc_targets(t)
        ai,_=await claude_final_verdict(sym,t,w,sc,conf,reasons,tgt,sent,fg,earn,av_r,av_m)
        if sc>=MIN_SCORE:
            await msg.edit_text(format_alert(sym,t,sc,sig,conf,reasons,tgt,ai,fg,earn),parse_mode="Markdown")
        else:
            ses=get_session()
            await msg.edit_text(
                f"📊 *تحليل {sym}* | {ses}\n"
                f"💵 `${t['price']}` | {t['change_pct']}%\n\n"
                f"RSI:{t.get('rsi')} | Stoch:{t.get('stoch_k')} | WR:{t.get('williams_r')}\n"
                f"CCI:{t.get('cci')} | MACD:{t.get('macd_hist')} | Vol:{t.get('vol_ratio')}x\n"
                f"Breakout:{t.get('breakout',{}).get('type')} | Div:{t.get('divergence')}\n"
                +(f"🕯️ {' | '.join(t.get('patterns',[]))}\n" if t.get('patterns') else "")
                +f"\n⚠️ النقاط: *{sc}/100* | تأكيدات: {conf}/{MIN_CONFIRMATIONS}\n"
                f"(الحد المطلوب: {MIN_SCORE} نقطة + {MIN_CONFIRMATIONS} تأكيدات)\n\n"
                f"🧠 {ai}",parse_mode="Markdown")
    except Exception as e:
        log.error(f"analyze {sym}: {e}")
        await msg.edit_text(f"❌ خطأ: `{e}`",parse_mode="Markdown")


async def post_init(app):
    asyncio.create_task(run_scanner(app))
    log.info("✅ Ultimate Scanner v4.0 ready")

def main():
    app=(Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build())
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("help",start))
    app.add_handler(CallbackQueryHandler(btn))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,analyze_cmd))
    log.info("🤖 Ultimate Trading Bot v4.0")
    app.run_polling(drop_pending_updates=True)

if __name__=="__main__":
    main()
