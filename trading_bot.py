"""
🤖 Super Trading Bot v3.0 - Yahoo Finance Edition
بيانات مجانية 100% من Yahoo Finance
"""

import os, logging, asyncio, aiohttp, time, json
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN")
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY",  "YOUR_ANTHROPIC_KEY")
FINNHUB_API_KEY    = os.getenv("FINNHUB_API_KEY",    "YOUR_FINNHUB_KEY")

ALERT_CHAT_IDS: list[int] = []
MIN_SCORE           = 72
SCAN_INTERVAL_SEC   = 300
MAX_ALERTS_PER_HOUR = 12
COOLDOWN_HOURS      = 4

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
last_scan_stats       = {"scanned":0,"opportunities":0,"sent":0,"time":""}


def get_session():
    m = datetime.now(timezone.utc).hour * 60 + datetime.now(timezone.utc).minute
    if 780<=m<810:    return "🌅 Pre-Market"
    elif 810<=m<1200: return "📈 Regular Market"
    elif 1200<=m<1440:return "🌙 After-Hours"
    else:             return "🌃 Overnight"


# ══════════════════════════════════════════════
# YAHOO FINANCE — بيانات مجانية
# ══════════════════════════════════════════════

async def get_yahoo_data(symbol: str) -> dict:
    """جلب بيانات الشموع من Yahoo Finance"""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {
        "interval": "1d",
        "range": "3mo",
        "includePrePost": "false",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, params=params, headers=headers,
                             timeout=aiohttp.ClientTimeout(total=12)) as r:
                data = await r.json()
        result = data["chart"]["result"][0]
        quotes = result["indicators"]["quote"][0]
        timestamps = result["timestamp"]
        closes  = [x for x in quotes.get("close",[])  if x is not None]
        opens   = [x for x in quotes.get("open",[])   if x is not None]
        highs   = [x for x in quotes.get("high",[])   if x is not None]
        lows    = [x for x in quotes.get("low",[])    if x is not None]
        volumes = [x for x in quotes.get("volume",[]) if x is not None]
        if len(closes) < 20:
            return {}
        meta = result.get("meta", {})
        return {
            "c": closes, "o": opens, "h": highs,
            "l": lows,   "v": volumes,
            "currency": meta.get("currency","USD"),
            "name": meta.get("shortName", symbol),
        }
    except Exception as e:
        log.debug(f"Yahoo {symbol}: {e}")
        return {}


async def get_finnhub_sentiment(symbol: str) -> dict:
    """مشاعر الأخبار من Finnhub (مجاني)"""
    try:
        url = f"https://finnhub.io/api/v1/news-sentiment?symbol={symbol}&token={FINNHUB_API_KEY}"
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=6)) as r:
                return await r.json()
    except:
        return {}


# ══════════════════════════════════════════════
# TECHNICAL INDICATORS
# ══════════════════════════════════════════════

def rsi(p, n=14):
    if len(p)<n+1: return 50.0
    g=[max(p[i]-p[i-1],0) for i in range(1,len(p))]
    l=[max(p[i-1]-p[i],0) for i in range(1,len(p))]
    ag=sum(g[-n:])/n; al=sum(l[-n:])/n
    return round(100-(100/(1+ag/al)),2) if al else 100.0

def ema(p, n):
    if len(p)<n: return p
    k=2/(n+1); e=[sum(p[:n])/n]
    for x in p[n:]: e.append(x*k+e[-1]*(1-k))
    return e

def macd(p):
    if len(p)<26: return None,None,None
    e12=ema(p,12); e26=ema(p,26); n=min(len(e12),len(e26))
    ml=[e12[-n+i]-e26[-n+i] for i in range(n)]; sig=ema(ml,9)
    return (round(ml[-1],4),round(sig[-1],4),round(ml[-1]-sig[-1],4)) if sig else (None,None,None)

def bb(p, n=20):
    if len(p)<n: return None,None,None
    r=p[-n:]; m=sum(r)/n; std=(sum((x-m)**2 for x in r)/n)**0.5
    return round(m+2*std,2),round(m,2),round(m-2*std,2)

def atr_calc(h,l,c,n=14):
    if len(c)<n+1: return 0.0
    trs=[max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1])) for i in range(1,len(c))]
    return round(sum(trs[-n:])/n,4)

def stoch(h,l,c,n=14):
    if len(c)<n: return 50.0,50.0
    rh=max(h[-n:]); rl=min(l[-n:])
    if rh==rl: return 50.0,50.0
    k=((c[-1]-rl)/(rh-rl))*100
    rh2=max(h[-n-1:-1]) if len(h)>n else rh
    rl2=min(l[-n-1:-1]) if len(l)>n else rl
    k2=((c[-2]-rl2)/(rh2-rl2))*100 if rh2!=rl2 and len(c)>n else k
    return round(k,2),round((k+k2)/2,2)

def vwap_calc(h,l,c,v):
    if not v or sum(v)==0: return c[-1]
    tp=[(h[i]+l[i]+c[i])/3 for i in range(len(c))]
    return round(sum(tp[i]*v[i] for i in range(len(c)))/sum(v),2)

def williams_r(h,l,c,n=14):
    if len(c)<n: return -50.0
    rh=max(h[-n:]); rl=min(l[-n:])
    return round(((rh-c[-1])/(rh-rl))*-100,2) if rh!=rl else -50.0

def cci_calc(h,l,c,n=20):
    if len(c)<n: return 0.0
    tp=[(h[i]+l[i]+c[i])/3 for i in range(len(c))]
    tp_n=tp[-n:]; mean=sum(tp_n)/n
    mad=sum(abs(x-mean) for x in tp_n)/n
    return round((tp[-1]-mean)/(0.015*mad),2) if mad else 0.0

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


def analyze(data: dict) -> dict:
    if not data: return {}
    c=data.get("c",[]); o=data.get("o",[])
    h=data.get("h",[]); l=data.get("l",[]); v=data.get("v",[])
    if len(c)<25: return {}

    MC,MS,MH = macd(c)
    BU,BM,BL = bb(c)
    SK,SD    = stoch(h,l,c)
    AT       = atr_calc(h,l,c)
    VP       = vwap_calc(h,l,c,v)
    WR       = williams_r(h,l,c)
    CC       = cci_calc(h,l,c)
    PTS      = detect_patterns(o,h,l,c)

    sma20 = round(sum(c[-20:])/20,2) if len(c)>=20 else None
    sma50 = round(sum(c[-50:])/50,2) if len(c)>=50 else None
    e9    = round(ema(c,9)[-1],2)    if len(c)>=9  else None
    e21   = round(ema(c,21)[-1],2)   if len(c)>=21 else None
    e50   = round(ema(c,50)[-1],2)   if len(c)>=50 else None

    avg_v = sum(v[-20:])/20 if len(v)>=20 else 1
    vr    = round(v[-1]/avg_v,2) if avg_v else 1.0
    chg   = round((c[-1]-c[-2])/c[-2]*100,2) if len(c)>1 else 0
    sup   = round(min(l[-20:]),2)
    res_  = round(max(h[-20:]),2)
    ts    = sum(1 for i in range(1,min(10,len(c))) if c[-i]>c[-i-1])

    return dict(
        price=round(c[-1],2), prev=round(c[-2],2) if len(c)>1 else round(c[-1],2),
        change_pct=chg, high=round(h[-1],2), low=round(l[-1],2), open=round(o[-1],2),
        rsi=rsi(c), macd=MC, macd_signal=MS, macd_hist=MH,
        bb_upper=BU, bb_mid=BM, bb_lower=BL,
        atr_val=AT, stoch_k=SK, stoch_d=SD, vwap=VP,
        williams_r=WR, cci=CC, support=sup, resistance=res_,
        sma20=sma20, sma50=sma50, ema9=e9, ema21=e21, ema50=e50,
        vol_ratio=vr, volume=int(v[-1]),
        patterns=PTS, trend_strength=round(ts/min(9,len(c)-1)*100),
        name=data.get("name","")
    )


def score(t: dict):
    if not t: return 0,"NONE",[]
    s=0; R=[]; price=t.get("price",0)
    r=t.get("rsi",50)

    if r<25:   s+=22; R.append(f"RSI={r} 🔥 ذروة بيع قصوى")
    elif r<32: s+=16; R.append(f"RSI={r} ذروة بيع قوية")
    elif r<40: s+=9;  R.append(f"RSI={r} منطقة شراء")
    elif r>75: s-=15
    elif r>65: s-=8

    h_=t.get("macd_hist"); mc=t.get("macd")
    if h_ is not None:
        if h_>0 and mc and mc>t.get("macd_signal",0): s+=15; R.append("MACD ✅ تقاطع صاعد")
        elif h_>0: s+=8; R.append("MACD إيجابي")
        elif h_<0: s-=8

    k=t.get("stoch_k",50); d=t.get("stoch_d",50)
    if k<15 and d<15:   s+=16; R.append(f"Stoch={k:.0f} 🔥 ذروة بيع قصوى")
    elif k<25 and d<25: s+=11; R.append(f"Stoch={k:.0f} ذروة بيع")
    elif k>d and k<40:  s+=7;  R.append("Stoch تقاطع صاعد")
    elif k>80:          s-=10

    bbl=t.get("bb_lower"); bbu=t.get("bb_upper")
    if bbl and price<=bbl:        s+=16; R.append("🎯 تحت الباند السفلي")
    elif bbl and price<=bbl*1.01: s+=11; R.append("عند الباند السفلي")
    elif bbu and price>=bbu*0.99: s-=12

    wr=t.get("williams_r",-50)
    if wr<-80:   s+=10; R.append(f"Williams%R={wr} ذروة بيع")
    elif wr<-60: s+=5
    elif wr>-20: s-=8

    cc=t.get("cci",0)
    if cc<-150:   s+=10; R.append(f"CCI={cc:.0f} ذروة بيع")
    elif cc<-100: s+=6
    elif cc>150:  s-=8

    s20=t.get("sma20"); s50=t.get("sma50")
    e9=t.get("ema9"); e21=t.get("ema21"); e50=t.get("ema50")
    if s20 and s50 and s20>s50: s+=6; R.append("SMA20>SMA50")
    if e9 and e21 and e9>e21:   s+=5; R.append("EMA9>EMA21 زخم")
    if e21 and e50 and e21>e50: s+=4; R.append("EMA21>EMA50")

    vp=t.get("vwap")
    if vp and price>vp: s+=8; R.append(f"فوق VWAP ${vp}")
    elif vp and price<vp*0.99: s-=4

    sup=t.get("support"); res=t.get("resistance")
    if sup and price<=sup*1.02: s+=8; R.append(f"قرب الدعم ${sup} 🛡️")
    if res and price>=res*0.98: s-=6

    vr=t.get("vol_ratio",1)
    if vr>=3.0:   s+=14; R.append(f"حجم {vr:.1f}x 🔥🔥🔥")
    elif vr>=2.0: s+=10; R.append(f"حجم {vr:.1f}x 🔥🔥")
    elif vr>=1.5: s+=6;  R.append(f"حجم {vr:.1f}x 🔥")
    elif vr<0.5:  s-=8

    pts=t.get("patterns",[])
    if pts: s+=min(len(pts)*5,10); R.append(f"نمط: {', '.join(pts)}")

    ts=t.get("trend_strength",50)
    if ts>=70: s+=8; R.append(f"قوة اتجاه {ts}%")
    elif ts<=30: s-=5

    sig="NONE"
    if s>=MIN_SCORE:
        if r<35 and bbl and price<=bbl*1.02: sig="SWING_BUY"
        elif h_ and h_>0 and vr>=1.5: sig="DAY_BUY"
        elif pts and r<45: sig="PATTERN_BUY"
        else: sig="BUY"

    return min(s,100), sig, R


def calc_tgt(t: dict) -> dict:
    p=t.get("price",0); at=t.get("atr_val",p*0.015)
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


async def claude_ai(sym,t,sc,reasons,tgt,sent):
    ses=get_session()
    ss=sent.get("companyNewsScore",0.5) if sent else 0.5
    sl_="إيجابي 😊" if ss>0.6 else "سلبي 😟" if ss<0.4 else "محايد 😐"
    prompt=(
        f"محلل وول ستريت خبير. حلّل هذه الفرصة:\n"
        f"{sym} ({t.get('name','')}) | نقاط {sc}/100 | {ses}\n"
        f"السعر:${t.get('price')} التغيير:{t.get('change_pct')}%\n"
        f"RSI:{t.get('rsi')} MACD_H:{t.get('macd_hist')} Stoch:{t.get('stoch_k')} "
        f"WR:{t.get('williams_r')} CCI:{t.get('cci')} Vol:{t.get('vol_ratio')}x\n"
        f"BB_L:${t.get('bb_lower')} VWAP:${t.get('vwap')} "
        f"دعم:${t.get('support')} مقاومة:${t.get('resistance')}\n"
        f"EMA9/21/50:${t.get('ema9')}/${t.get('ema21')}/${t.get('ema50')}\n"
        f"أنماط:{','.join(t.get('patterns',[]))} | مشاعر:{sl_}\n"
        f"SL:${tgt['sl']} TP1:${tgt['tp1']} TP2:${tgt['tp2']} TP3:${tgt['tp3']} R:R=1:{tgt['rr']}\n"
        f"الأسباب:{' | '.join(reasons[:4])}\n\n"
        f"أجب بهذا التنسيق فقط:\n"
        f"VERDICT: [✅ قوية / ⚠️ متوسطة / ❌ ضعيفة]\n"
        f"ANALYSIS: [جملتان عن المشهد التقني]\n"
        f"RISK: [جملة عن أهم مخاطرة]\n"
        f"ACTION: [ادخل الآن / انتظر تراجع / تجنب]"
    )
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post("https://api.anthropic.com/v1/messages",
                headers={"x-api-key":ANTHROPIC_API_KEY,"anthropic-version":"2023-06-01","content-type":"application/json"},
                json={"model":"claude-haiku-4-5-20251001","max_tokens":300,
                      "messages":[{"role":"user","content":prompt}]},
                timeout=aiohttp.ClientTimeout(total=25)) as r:
                data=await r.json()
        if "content" not in data: return "⚠️ AI غير متاح",True
        txt=data["content"][0]["text"]
        approved="تجنب" not in txt and "❌" not in txt.split("VERDICT:")[-1][:20]
        return txt,approved
    except Exception as e:
        return f"⚠️ {e}",True


def fmt_alert(sym,t,sc,sig,reasons,tgt,ai):
    ses=get_session()
    em="🚀" if sc>=85 else "📊"
    sl={"SWING_BUY":"🔄 Swing","DAY_BUY":"⚡ Day Trade","PATTERN_BUY":"🕯️ Pattern","BUY":"📈 Buy"}.get(sig,"📈")
    ep=tgt['entry']; pct=lambda x:f"+{round((x-ep)/ep*100,1)}%"
    name=f" ({t.get('name','')})" if t.get('name') else ""
    return (
        f"{em} *{sym}{name} — {sl}*\n{'━'*22}\n"
        f"🏆 النقاط: *{sc}/100* | {ses}\n"
        f"💵 `${t['price']}` | {'+' if t['change_pct']>=0 else ''}{t['change_pct']}% | Vol:{t.get('vol_ratio')}x\n\n"
        f"🎯 *الأهداف:*\n"
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
        +(f"  🕯️ {' | '.join(t.get('patterns',[]))}\n" if t.get('patterns') else "")
        +f"\n🧠 *تحليل AI:*\n{ai}\n\n"
        f"💡 *الأسباب:*\n"+"\n".join(f"  • {r}" for r in reasons[:5])
    )


async def scan_one(sym):
    try:
        data = await get_yahoo_data(sym)
        t = analyze(data)
        if not t: return None
        r=t.get("rsi",50); vr=t.get("vol_ratio",1)
        if r>58 and vr<1.2: return None
        sc,sig,reasons=score(t)
        if sc<MIN_SCORE or sig=="NONE": return None
        return sc,sig,t,reasons
    except Exception as e:
        log.debug(f"scan {sym}: {e}")
        return None


async def run_scanner(app):
    global alert_count_this_hour,last_hour_reset,scanning_active,last_scan_stats
    log.info("🚀 Super Scanner v3.0 (Yahoo Finance) started")

    while True:
        if not scanning_active:
            await asyncio.sleep(60); continue
        if time.time()-last_hour_reset>3600:
            alert_count_this_hour=0; last_hour_reset=time.time()

        ses=get_session()
        log.info(f"🔍 Scanning {len(WATCHLIST)} | {ses}")
        start=time.time(); opps=0; sent_c=0

        for i in range(0,len(WATCHLIST),6):
            if alert_count_this_hour>=MAX_ALERTS_PER_HOUR: break
            batch=WATCHLIST[i:i+6]
            results=await asyncio.gather(*[scan_one(s) for s in batch],return_exceptions=True)
            for sym,res in zip(batch,results):
                if not res or isinstance(res,Exception): continue
                sc,sig,t,reasons=res; opps+=1
                if time.time()-alerted_symbols.get(sym,0)<COOLDOWN_HOURS*3600: continue
                sent_d=await get_finnhub_sentiment(sym)
                tgt=calc_tgt(t)
                ai,approved=await claude_ai(sym,t,sc,reasons,tgt,sent_d)
                if not approved:
                    log.info(f"🚫 rejected {sym}"); continue
                msg=fmt_alert(sym,t,sc,sig,reasons,tgt,ai)
                ok=False
                for cid in ALERT_CHAT_IDS:
                    try:
                        await app.bot.send_message(cid,msg,parse_mode="Markdown"); ok=True
                    except Exception as e:
                        log.error(f"send {cid}: {e}")
                if ok:
                    alerted_symbols[sym]=time.time()
                    alert_count_this_hour+=1; sent_c+=1
                    log.info(f"✅ {sym} sc={sc} sig={sig}")
            await asyncio.sleep(1.2)

        elapsed=round(time.time()-start)
        last_scan_stats={"scanned":len(WATCHLIST),"opportunities":opps,"sent":sent_c,"time":f"{elapsed}s"}
        log.info(f"✅ Done | opps={opps} sent={sent_c} time={elapsed}s")
        await asyncio.sleep(SCAN_INTERVAL_SEC)


async def start(update,ctx):
    cid=update.effective_chat.id
    kb=[[InlineKeyboardButton("📡 تفعيل التنبيهات",callback_data="sub"),
         InlineKeyboardButton("🔕 إيقاف",callback_data="unsub")],
        [InlineKeyboardButton("🔍 فحص سريع",callback_data="scan"),
         InlineKeyboardButton("📊 حالة السوق",callback_data="status")],
        [InlineKeyboardButton("🏆 أفضل فرصة الآن",callback_data="best"),
         InlineKeyboardButton("⚙️ الإعدادات",callback_data="settings")]]
    await update.message.reply_text(
        f"🤖 *بوت التداول الخارق v3.0*\n\n"
        f"🔍 يراقب *{len(WATCHLIST)} سهم* أمريكي\n"
        f"⏰ يعمل *24/7* في كل الجلسات\n"
        f"📊 *12 مؤشر + أنماط شموع + Claude AI*\n"
        f"📰 تحليل مشاعر الأخبار\n"
        f"🎯 *4 أهداف ربح + SL + R:R*\n\n"
        f"🆔 Chat ID: `{cid}`\n\n"
        f"👇 اضغط *تفعيل التنبيهات* للبدء",
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
            f"🧠 12 مؤشر + Claude AI\n\n"
            f"سيصلك تنبيه فور اكتشاف فرصة 🚀",parse_mode="Markdown")
    elif q.data=="unsub":
        if cid in ALERT_CHAT_IDS: ALERT_CHAT_IDS.remove(cid)
        await q.edit_message_text("🔕 تم إيقاف التنبيهات.")
    elif q.data=="status":
        ses=get_session(); st=last_scan_stats
        await q.edit_message_text(
            f"📊 *حالة البوت v3.0*\n\n"
            f"⏰ الجلسة: {ses}\n"
            f"🔍 الأسهم: {len(WATCHLIST)}\n"
            f"📡 {'🟢 نشط' if scanning_active else '🔴 متوقف'}\n"
            f"🔔 تنبيهات/ساعة: {alert_count_this_hour}/{MAX_ALERTS_PER_HOUR}\n\n"
            f"📈 *آخر فحص:*\n"
            f"  فُحص:{st.get('scanned',0)} | فرص:{st.get('opportunities',0)} | أُرسل:{st.get('sent',0)} | وقت:{st.get('time','—')}\n\n"
            f"🕐 {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC",
            parse_mode="Markdown")
    elif q.data=="settings":
        await q.edit_message_text(
            f"⚙️ *الإعدادات*\n\n"
            f"📊 الحد الأدنى: {MIN_SCORE}/100\n"
            f"⏱️ المسح: كل {SCAN_INTERVAL_SEC//60} دقيقة\n"
            f"🔔 حد/ساعة: {MAX_ALERTS_PER_HOUR}\n"
            f"⏳ كولداون: {COOLDOWN_HOURS} ساعات\n"
            f"📋 الأسهم: {len(WATCHLIST)}\n"
            f"🧠 المؤشرات: 12\n"
            f"🕯️ أنماط الشموع: ✅\n"
            f"📰 تحليل المشاعر: ✅\n"
            f"📡 مصدر البيانات: Yahoo Finance ✅",
            parse_mode="Markdown")
    elif q.data=="scan":
        await q.edit_message_text("⏳ فحص 40 سهم...")
        found=[]
        for sym in WATCHLIST[:40]:
            r=await scan_one(sym)
            if r:
                sc,sig,t,_=r
                ic={"SWING_BUY":"🔄","DAY_BUY":"⚡","PATTERN_BUY":"🕯️"}.get(sig,"📈")
                found.append((sc,f"{ic} *{sym}* {sc}/100 | ${t['price']} | Vol:{t.get('vol_ratio')}x"))
        found.sort(key=lambda x:-x[0])
        msg=("🔍 *أفضل الفرص الآن:*\n\n"+"\n".join(x[1] for x in found[:8])+"\n\n_أرسل رمز السهم لتحليل كامل_") if found else "🔍 لا توجد فرص قوية حالياً."
        await q.edit_message_text(msg,parse_mode="Markdown")
    elif q.data=="best":
        await q.edit_message_text("🏆 أبحث عن أفضل فرصة...")
        best=None; bs=0
        for sym in WATCHLIST[:80]:
            r=await scan_one(sym)
            if r and r[0]>bs: bs=r[0]; best=(sym,)+r
        if best:
            sym,sc,sig,t,reasons=best; tgt=calc_tgt(t)
            sd=await get_finnhub_sentiment(sym)
            ai,_=await claude_ai(sym,t,sc,reasons,tgt,sd)
            await q.edit_message_text(fmt_alert(sym,t,sc,sig,reasons,tgt,ai),parse_mode="Markdown")
        else:
            await q.edit_message_text("🏆 لا توجد فرص بارزة حالياً.")


async def analyze_cmd(update,ctx):
    sym=update.message.text.strip().upper()
    if not sym.isalpha() or len(sym)>6: return
    msg=await update.message.reply_text(f"🔍 تحليل *{sym}*...",parse_mode="Markdown")
    try:
        data=await get_yahoo_data(sym)
        if not data:
            await msg.edit_text(f"❌ لم أجد بيانات لـ `{sym}`\nتأكد من الرمز مثل: AAPL NVDA TSLA"); return
        t=analyze(data)
        if not t:
            await msg.edit_text(f"❌ بيانات غير كافية لـ `{sym}`"); return
        sc,sig,reasons=score(t); tgt=calc_tgt(t)
        sent=await get_finnhub_sentiment(sym)
        ai,_=await claude_ai(sym,t,sc,reasons,tgt,sent)
        if sc>=MIN_SCORE:
            await msg.edit_text(fmt_alert(sym,t,sc,sig,reasons,tgt,ai),parse_mode="Markdown")
        else:
            ses=get_session()
            await msg.edit_text(
                f"📊 *تحليل {sym}*\n"
                f"💵 `${t['price']}` | {t['change_pct']}% | {ses}\n\n"
                f"RSI:{t.get('rsi')} | Stoch:{t.get('stoch_k')} | WR:{t.get('williams_r')}\n"
                f"CCI:{t.get('cci')} | MACD:{t.get('macd_hist')} | Vol:{t.get('vol_ratio')}x\n"
                f"دعم:${t.get('support')} | مقاومة:${t.get('resistance')}\n"
                +(f"🕯️ {' | '.join(t.get('patterns',[]))}\n" if t.get('patterns') else "")
                +f"\n⚠️ النقاط: *{sc}/100* (الحد: {MIN_SCORE})\n\n🧠 {ai}",
                parse_mode="Markdown")
    except Exception as e:
        log.error(f"analyze {sym}: {e}")
        await msg.edit_text(f"❌ خطأ: `{e}`",parse_mode="Markdown")


async def post_init(app):
    asyncio.create_task(run_scanner(app))
    log.info("✅ Super Scanner v3.0 ready")

def main():
    app=(Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build())
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("help",start))
    app.add_handler(CallbackQueryHandler(btn))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,analyze_cmd))
    log.info("🤖 Super Trading Bot v3.0 — Yahoo Finance Edition")
    app.run_polling(drop_pending_updates=True)

if __name__=="__main__":
    main()
