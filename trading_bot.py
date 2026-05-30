"""
🤖 AI Trading Bot v3.0 - SUPER EDITION
أقوى بوت تداول مدعوم بالذكاء الاصطناعي
"""

import os, logging, asyncio, aiohttp, time
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
MIN_SCORE           = 75
SCAN_INTERVAL_SEC   = 240
MAX_ALERTS_PER_HOUR = 12
COOLDOWN_HOURS      = 4

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
    "ADBE","INTU","PANW","FTNT","CYBR","S","TENB","VRNS","QLYS","RPID",
    "TSM","ASML","WOLF","ON","SWKS","MPWR","ENTG","TER","COHU","FORM",
    "RIVN","LCID","NIO","LI","XPEV","CHPT","BLNK","EVGO","PTRA","ZEV",
    "SQ","PYPL","AFRM","RELY","FLYW","COUR","DUOL","CPNG","MELI","SE",
]

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

alert_count_this_hour = 0
last_hour_reset       = time.time()
alerted_symbols: dict[str, float] = {}
scanning_active       = True
last_scan_stats       = {"scanned":0,"opportunities":0,"sent":0,"time":""}
dynamic_watchlist: list[str] = []


def get_session():
    m = datetime.now(timezone.utc).hour * 60 + datetime.now(timezone.utc).minute
    if 780<=m<810:    return "🌅 Pre-Market","pre"
    elif 810<=m<1200: return "📈 Regular","regular"
    elif 1200<=m<1440:return "🌙 After-Hours","after"
    else:             return "🌃 Overnight","night"


async def fetch_watchlist():
    try:
        url = f"https://finnhub.io/api/v1/stock/symbol?exchange=US&token={FINNHUB_API_KEY}"
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                data = await r.json()
        syms=set()
        for item in data:
            sym=item.get("symbol",""); typ=item.get("type","")
            if typ in ("Common Stock","EQS") and "." not in sym and len(sym)<=5:
                syms.add(sym)
        priority=[s for s in FALLBACK_WATCHLIST if s in syms]
        rest=[s for s in syms if s not in set(FALLBACK_WATCHLIST)]
        result=priority+rest[:300-len(priority)]
        log.info(f"📋 Dynamic watchlist: {len(result)} symbols")
        return result[:300]
    except Exception as e:
        log.error(f"Watchlist error: {e}")
        return FALLBACK_WATCHLIST


async def get_candles(sym, res="D", count=65):
    to=int(time.time()); fr=to-(count*86400*2)
    url=f"https://finnhub.io/api/v1/stock/candle?symbol={sym}&resolution={res}&from={fr}&to={to}&token={FINNHUB_API_KEY}"
    async with aiohttp.ClientSession() as s:
        async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
            return await r.json()

async def get_sentiment(sym):
    try:
        url=f"https://finnhub.io/api/v1/news-sentiment?symbol={sym}&token={FINNHUB_API_KEY}"
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=6)) as r:
                return await r.json()
    except: return {}

async def get_fins(sym):
    try:
        url=f"https://finnhub.io/api/v1/stock/metric?symbol={sym}&metric=all&token={FINNHUB_API_KEY}"
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=6)) as r:
                return await r.json()
    except: return {}


def rsi(p,n=14):
    if len(p)<n+1: return 50.0
    g=[max(p[i]-p[i-1],0) for i in range(1,len(p))]
    l=[max(p[i-1]-p[i],0) for i in range(1,len(p))]
    ag=sum(g[-n:])/n; al=sum(l[-n:])/n
    return round(100-(100/(1+ag/al)),2) if al else 100.0

def ema(p,n):
    if len(p)<n: return p
    k=2/(n+1); e=[sum(p[:n])/n]
    for x in p[n:]: e.append(x*k+e[-1]*(1-k))
    return e

def macd(p):
    if len(p)<26: return None,None,None
    e12=ema(p,12); e26=ema(p,26); n=min(len(e12),len(e26))
    ml=[e12[-n+i]-e26[-n+i] for i in range(n)]; sig=ema(ml,9)
    return (round(ml[-1],4),round(sig[-1],4),round(ml[-1]-sig[-1],4)) if sig else (None,None,None)

def bb(p,n=20):
    if len(p)<n: return None,None,None
    r=p[-n:]; m=sum(r)/n; std=(sum((x-m)**2 for x in r)/n)**0.5
    return round(m+2*std,2),round(m,2),round(m-2*std,2)

def atr(h,l,c,n=14):
    if len(c)<n+1: return 0.0
    trs=[max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1])) for i in range(1,len(c))]
    return round(sum(trs[-n:])/n,4)

def stoch(h,l,c,n=14):
    if len(c)<n: return 50.0,50.0
    rh=max(h[-n:]); rl=min(l[-n:])
    if rh==rl: return 50.0,50.0
    k=((c[-1]-rl)/(rh-rl))*100
    rh2=max(h[-n-1:-1]) if len(h)>n else rh; rl2=min(l[-n-1:-1]) if len(l)>n else rl
    k2=((c[-2]-rl2)/(rh2-rl2))*100 if rh2!=rl2 and len(c)>n else k
    return round(k,2),round((k+k2)/2,2)

def vwap(h,l,c,v):
    if not v or sum(v)==0: return c[-1]
    tp=[(h[i]+l[i]+c[i])/3 for i in range(len(c))]
    return round(sum(tp[i]*v[i] for i in range(len(c)))/sum(v),2)

def williams_r(h,l,c,n=14):
    if len(c)<n: return -50.0
    rh=max(h[-n:]); rl=min(l[-n:])
    return round(((rh-c[-1])/(rh-rl))*-100,2) if rh!=rl else -50.0

def cci(h,l,c,n=20):
    if len(c)<n: return 0.0
    tp=[(h[i]+l[i]+c[i])/3 for i in range(len(c))]
    tp_n=tp[-n:]; mean=sum(tp_n)/n
    mad=sum(abs(x-mean) for x in tp_n)/n
    return round((tp[-1]-mean)/(0.015*mad),2) if mad else 0.0

def obv(c,v):
    o=0
    for i in range(1,len(c)):
        if c[i]>c[i-1]: o+=v[i]
        elif c[i]<c[i-1]: o-=v[i]
    return o

def patterns(o,h,l,c):
    if len(c)<3: return []
    pts=[]
    p,ph,pl,po=c[-1],h[-1],l[-1],o[-1]
    p2,ph2,pl2,po2=c[-2],h[-2],l[-2],o[-2]
    body=abs(p-po); rng=ph-pl; body2=abs(p2-po2); rng2=ph2-pl2
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


def analyze(candles):
    if not candles or candles.get("s")!="ok": return {}
    c=candles.get("c",[]); o_=candles.get("o",[])
    h=candles.get("h",[]); l=candles.get("l",[]); v=candles.get("v",[])
    if len(c)<30: return {}
    MC,MS,MH=macd(c); BU,BM,BL=bb(c); SK,SD=stoch(h,l,c)
    avg_v=sum(v[-20:])/20 if len(v)>=20 else 1
    vr=round(v[-1]/avg_v,2) if avg_v else 1.0
    sup=round(min(l[-20:]),2); res_=round(max(h[-20:]),2)
    ts=sum(1 for i in range(1,min(10,len(c))) if c[-i]>c[-i-1])
    return dict(
        price=c[-1], prev=c[-2] if len(c)>1 else c[-1],
        change_pct=round((c[-1]-c[-2])/c[-2]*100,2) if len(c)>1 else 0,
        high=h[-1], low=l[-1], open=o_[-1],
        rsi=rsi(c), macd=MC, macd_signal=MS, macd_hist=MH,
        bb_upper=BU, bb_mid=BM, bb_lower=BL,
        atr_val=atr(h,l,c), stoch_k=SK, stoch_d=SD,
        vwap=vwap(h,l,c,v), williams_r=williams_r(h,l,c),
        cci=cci(h,l,c), obv=obv(c,v),
        support=sup, resistance=res_,
        sma20=round(sum(c[-20:])/20,2) if len(c)>=20 else None,
        sma50=round(sum(c[-50:])/50,2) if len(c)>=50 else None,
        ema9=round(ema(c,9)[-1],2) if len(c)>=9 else None,
        ema21=round(ema(c,21)[-1],2) if len(c)>=21 else None,
        ema50=round(ema(c,50)[-1],2) if len(c)>=50 else None,
        vol_ratio=vr, volume=v[-1],
        patterns=patterns(o_,h,l,c),
        trend_strength=round(ts/min(9,len(c)-1)*100)
    )


def score(t):
    if not t: return 0,"NONE",[]
    s=0; R=[]; price=t.get("price",0)
    r=t.get("rsi",50)
    if r<25:   s+=22; R.append(f"RSI={r} 🔥 ذروة بيع قصوى")
    elif r<32: s+=16; R.append(f"RSI={r} ذروة بيع قوية")
    elif r<40: s+=9;  R.append(f"RSI={r} منطقة شراء")
    elif r>75: s-=15
    elif r>65: s-=8
    h=t.get("macd_hist"); mc=t.get("macd")
    if h is not None:
        if h>0 and mc and mc>t.get("macd_signal",0): s+=15; R.append("MACD ✅ تقاطع صاعد")
        elif h>0: s+=8; R.append("MACD إيجابي")
        elif h<0: s-=8
    k=t.get("stoch_k",50); d=t.get("stoch_d",50)
    if k<15 and d<15:   s+=16; R.append(f"Stoch={k:.0f} 🔥 ذروة بيع قصوى")
    elif k<25 and d<25: s+=11; R.append(f"Stoch={k:.0f} ذروة بيع")
    elif k>d and k<40:  s+=7;  R.append("Stoch تقاطع صاعد")
    elif k>80:          s-=10
    bbl=t.get("bb_lower"); bbu=t.get("bb_upper")
    if bbl and price<=bbl:         s+=16; R.append("🎯 تحت الباند السفلي")
    elif bbl and price<=bbl*1.01:  s+=11; R.append("عند الباند السفلي")
    elif bbu and price>=bbu*0.99:  s-=12
    wr=t.get("williams_r",-50)
    if wr<-80:   s+=10; R.append(f"Williams%R={wr} ذروة بيع")
    elif wr<-60: s+=5
    elif wr>-20: s-=8
    cc=t.get("cci",0)
    if cc<-150:    s+=10; R.append(f"CCI={cc:.0f} ذروة بيع")
    elif cc<-100:  s+=6
    elif cc>150:   s-=8
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
        elif h and h>0 and vr>=1.5: sig="DAY_BUY"
        elif pts and r<45: sig="PATTERN_BUY"
        else: sig="BUY"
    return min(s,100),sig,R


def calc_tgt(t):
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


async def claude_ai(sym,t,sc,reasons,tgt,sent,fins):
    ses,_=get_session()
    ss=sent.get("companyNewsScore",0.5) if sent else 0.5
    sl="إيجابي 😊" if ss>0.6 else "سلبي 😟" if ss<0.4 else "محايد 😐"
    mt=fins.get("metric",{}) if fins else {}
    pe=mt.get("peNormalizedAnnual","N/A"); beta=mt.get("beta","N/A")
    w52h=mt.get("52WeekHigh","N/A"); w52l=mt.get("52WeekLow","N/A")
    prompt=(f"محلل وول ستريت خبير. حلّل هذه الفرصة باختصار شديد.\n"
            f"{sym} | نقاط {sc}/100 | {ses} | ${t.get('price')} | {t.get('change_pct')}%\n"
            f"RSI:{t.get('rsi')} MACD_H:{t.get('macd_hist')} Stoch:{t.get('stoch_k')} "
            f"WR:{t.get('williams_r')} CCI:{t.get('cci')} Vol:{t.get('vol_ratio')}x\n"
            f"BB_L:${t.get('bb_lower')} VWAP:${t.get('vwap')} دعم:${t.get('support')} مقاومة:${t.get('resistance')}\n"
            f"EMA9/21/50:${t.get('ema9')}/${t.get('ema21')}/${t.get('ema50')}\n"
            f"أنماط:{','.join(t.get('patterns',[]))} | مشاعر:{sl} | P/E:{pe} Beta:{beta}\n"
            f"52W H:${w52h} L:${w52l}\n"
            f"SL:${tgt['sl']} TP1:${tgt['tp1']} TP2:${tgt['tp2']} TP3:${tgt['tp3']} R:R=1:{tgt['rr']}\n"
            f"الأسباب:{' | '.join(reasons[:4])}\n\n"
            f"أجب بالتنسيق التالي فقط:\n"
            f"VERDICT: [✅ قوية / ⚠️ متوسطة / ❌ ضعيفة]\n"
            f"ANALYSIS: [جملتان: المشهد التقني وسبب الدخول]\n"
            f"RISK: [جملة: أهم مخاطرة]\n"
            f"ACTION: [ادخل الآن / انتظر تراجع / تجنب]")
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


def fmt(sym,t,sc,sig,reasons,tgt,ai):
    ses,_=get_session()
    em="🚀" if sc>=85 else "📊"
    sl={"SWING_BUY":"🔄 Swing","DAY_BUY":"⚡ Day Trade","PATTERN_BUY":"🕯️ Pattern","BUY":"📈 Buy"}.get(sig,"📈")
    ep=tgt['entry']; pct=lambda x:f"+{round((x-ep)/ep*100,1)}%"
    return (f"{em} *{symbol_display(sym)} — {sl}*\n{'━'*22}\n"
            f"🏆 *{sc}/100* | {ses}\n"
            f"💵 `${t['price']}` | {'+' if t['change_pct']>=0 else ''}{t['change_pct']}% | Vol:{t.get('vol_ratio')}x\n\n"
            f"🎯 *الأهداف:*\n"
            f"  📍 دخول: `${ep}`\n"
            f"  🥉 TP1: `${tgt['tp1']}` ({pct(tgt['tp1'])})\n"
            f"  🥈 TP2: `${tgt['tp2']}` ({pct(tgt['tp2'])})\n"
            f"  🥇 TP3: `${tgt['tp3']}` ({pct(tgt['tp3'])})\n"
            f"  💎 TP4: `${tgt['tp4']}` ({pct(tgt['tp4'])})\n"
            f"  🛑 SL: `${tgt['sl']}` (-{tgt['risk_pct']}%)\n"
            f"  📐 R:R = 1:{tgt['rr']}\n\n"
            f"📊 RSI:{t.get('rsi')} | Stoch:{t.get('stoch_k')} | WR:{t.get('williams_r')}\n"
            f"MACD:{t.get('macd_hist')} | CCI:{t.get('cci')} | VWAP:${t.get('vwap')}\n"
            f"دعم:${t.get('support')} | مقاومة:${t.get('resistance')}\n"
            + (f"🕯️ {' | '.join(t.get('patterns',[]))}\n" if t.get('patterns') else "")
            + f"\n🧠 *AI:*\n{ai}\n\n"
            f"💡 *الأسباب:*\n"+"\n".join(f"  • {r}" for r in reasons[:5]))

def symbol_display(sym): return sym


async def scan_one(sym):
    try:
        candles=await get_candles(sym)
        t=analyze(candles)
        if not t: return None
        r=t.get("rsi",50); vr=t.get("vol_ratio",1)
        if r>58 and vr<1.2: return None
        sc,sig,reasons=score(t)
        if sc<MIN_SCORE or sig=="NONE": return None
        return sc,sig,t,reasons
    except Exception as e:
        log.debug(f"scan {sym}: {e}"); return None


async def run_scanner(app):
    global alert_count_this_hour,last_hour_reset,scanning_active,dynamic_watchlist,last_scan_stats
    log.info("🚀 Super Scanner v3.0 started")
    dynamic_watchlist=await fetch_watchlist()
    scan_count=0

    while True:
        if not scanning_active:
            await asyncio.sleep(60); continue
        if time.time()-last_hour_reset>3600:
            alert_count_this_hour=0; last_hour_reset=time.time()
        if scan_count%90==0 and scan_count>0:
            dynamic_watchlist=await fetch_watchlist()

        ses,_=get_session(); wl=dynamic_watchlist or FALLBACK_WATCHLIST
        log.info(f"🔍 Scanning {len(wl)} | {ses}")
        start=time.time(); opps=0; sent_c=0

        for i in range(0,len(wl),8):
            if alert_count_this_hour>=MAX_ALERTS_PER_HOUR: break
            batch=wl[i:i+8]
            results=await asyncio.gather(*[scan_one(s) for s in batch],return_exceptions=True)
            for sym,res in zip(batch,results):
                if not res or isinstance(res,Exception): continue
                sc,sig,t,reasons=res; opps+=1
                if time.time()-alerted_symbols.get(sym,0)<COOLDOWN_HOURS*3600: continue
                sent_d,fins_d=await asyncio.gather(get_sentiment(sym),get_fins(sym),return_exceptions=True)
                if isinstance(sent_d,Exception): sent_d={}
                if isinstance(fins_d,Exception): fins_d={}
                tgt=calc_tgt(t)
                ai,approved=await claude_ai(sym,t,sc,reasons,tgt,sent_d,fins_d)
                if not approved:
                    log.info(f"🚫 rejected {sym}"); continue
                msg=fmt(sym,t,sc,sig,reasons,tgt,ai)
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
            await asyncio.sleep(1.5)

        elapsed=round(time.time()-start)
        last_scan_stats={"scanned":len(wl),"opportunities":opps,"sent":sent_c,"time":f"{elapsed}s"}
        log.info(f"✅ Done | opps={opps} sent={sent_c} time={elapsed}s")
        scan_count+=1
        await asyncio.sleep(SCAN_INTERVAL_SEC)


async def start(update,ctx):
    cid=update.effective_chat.id; wl=dynamic_watchlist or FALLBACK_WATCHLIST
    kb=[[InlineKeyboardButton("📡 تفعيل التنبيهات",callback_data="sub"),
         InlineKeyboardButton("🔕 إيقاف",callback_data="unsub")],
        [InlineKeyboardButton("🔍 فحص سريع",callback_data="scan"),
         InlineKeyboardButton("📊 حالة السوق",callback_data="status")],
        [InlineKeyboardButton("🏆 أفضل فرصة",callback_data="best"),
         InlineKeyboardButton("⚙️ الإعدادات",callback_data="settings")]]
    await update.message.reply_text(
        f"🤖 *بوت التداول الخارق v3.0*\n\n"
        f"🔍 يراقب *{len(wl)}+ سهم* ديناميكياً\n"
        f"⏰ يعمل *24/7* في كل الجلسات\n"
        f"🧠 *12 مؤشر + أنماط شموع + Claude AI*\n"
        f"📰 تحليل الأخبار والبيانات المالية\n"
        f"🎯 *4 أهداف ربح + SL + R:R*\n\n"
        f"🆔 Chat ID: `{cid}`\n\n"
        f"👇 اضغط *تفعيل التنبيهات* للبدء",
        parse_mode="Markdown",reply_markup=InlineKeyboardMarkup(kb))


async def btn(update,ctx):
    q=update.callback_query; await q.answer(); cid=q.message.chat_id; wl=dynamic_watchlist or FALLBACK_WATCHLIST

    if q.data=="sub":
        if cid not in ALERT_CHAT_IDS: ALERT_CHAT_IDS.append(cid)
        await q.edit_message_text(
            f"✅ *تم تفعيل التنبيهات!*\n\n"
            f"🔍 {len(wl)} سهم تحت المراقبة\n"
            f"⏱️ فحص كل {SCAN_INTERVAL_SEC//60} دقائق\n"
            f"📊 الحد الأدنى: {MIN_SCORE}/100\n"
            f"🧠 12 مؤشر + Claude AI + أنماط شموع\n\n"
            f"سيصلك تنبيه فور اكتشاف فرصة 🚀",parse_mode="Markdown")
    elif q.data=="unsub":
        if cid in ALERT_CHAT_IDS: ALERT_CHAT_IDS.remove(cid)
        await q.edit_message_text("🔕 تم إيقاف التنبيهات.")
    elif q.data=="status":
        ses,_=get_session(); st=last_scan_stats
        await q.edit_message_text(
            f"📊 *حالة البوت v3.0*\n\n"
            f"⏰ الجلسة: {ses}\n🔍 الأسهم: {len(wl)}\n"
            f"📡 {'🟢 نشط' if scanning_active else '🔴 متوقف'}\n"
            f"🔔 تنبيهات/ساعة: {alert_count_this_hour}/{MAX_ALERTS_PER_HOUR}\n\n"
            f"📈 *آخر فحص:*\n  فُحص:{st.get('scanned',0)} | فرص:{st.get('opportunities',0)} | أُرسل:{st.get('sent',0)} | وقت:{st.get('time','—')}\n\n"
            f"🕐 {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC",parse_mode="Markdown")
    elif q.data=="settings":
        await q.edit_message_text(
            f"⚙️ *الإعدادات*\n\n"
            f"📊 الحد الأدنى: {MIN_SCORE}/100\n⏱️ المسح: كل {SCAN_INTERVAL_SEC//60} دقيقة\n"
            f"🔔 حد/ساعة: {MAX_ALERTS_PER_HOUR} | ⏳ كولداون: {COOLDOWN_HOURS}س\n"
            f"📋 الأسهم: {len(wl)} | 🧠 المؤشرات: 12\n"
            f"🕯️ أنماط الشموع: ✅ | 📰 تحليل المشاعر: ✅\n💰 البيانات المالية: ✅",
            parse_mode="Markdown")
    elif q.data=="scan":
        await q.edit_message_text("⏳ فحص 40 سهم...")
        found=[]
        for sym in wl[:40]:
            r=await scan_one(sym)
            if r:
                sc,sig,t,_=r
                sl={"SWING_BUY":"🔄","DAY_BUY":"⚡","PATTERN_BUY":"🕯️"}.get(sig,"📈")
                found.append((sc,f"{sl} *{sym}* {sc}/100 | ${t['price']} | Vol:{t.get('vol_ratio')}x"))
        found.sort(key=lambda x:-x[0])
        msg=("🔍 *أفضل الفرص الآن:*\n\n"+"\n".join(x[1] for x in found[:8])+"\n\n_أرسل رمز السهم لتحليل كامل_") if found else "🔍 لا توجد فرص قوية حالياً."
        await q.edit_message_text(msg,parse_mode="Markdown")
    elif q.data=="best":
        await q.edit_message_text("🏆 أبحث عن أفضل فرصة...")
        best=None; bs=0
        for sym in wl[:80]:
            r=await scan_one(sym)
            if r and r[0]>bs: bs=r[0]; best=(sym,)+r
        if best:
            sym,sc,sig,t,reasons=best; tgt=calc_tgt(t)
            sd,fins=await asyncio.gather(get_sentiment(sym),get_fins(sym),return_exceptions=True)
            if isinstance(sd,Exception): sd={}
            if isinstance(fins,Exception): fins={}
            ai,_=await claude_ai(sym,t,sc,reasons,tgt,sd,fins)
            await q.edit_message_text(fmt(sym,t,sc,sig,reasons,tgt,ai),parse_mode="Markdown")
        else:
            await q.edit_message_text("🏆 لا توجد فرص بارزة حالياً.")


async def analyze_cmd(update,ctx):
    sym=update.message.text.strip().upper()
    if not sym.isalpha() or len(sym)>6: return
    msg=await update.message.reply_text(f"🔍 تحليل عميق لـ *{sym}*...",parse_mode="Markdown")
    try:
        candles,sent_d,fins_d=await asyncio.gather(
            get_candles(sym),get_sentiment(sym),get_fins(sym),return_exceptions=True)
        if isinstance(candles,Exception) or not candles:
            await msg.edit_text(f"❌ لا بيانات لـ `{sym}`"); return
        t=analyze(candles)
        if not t:
            await msg.edit_text(f"❌ بيانات غير كافية لـ `{sym}`"); return
        if isinstance(sent_d,Exception): sent_d={}
        if isinstance(fins_d,Exception): fins_d={}
        sc,sig,reasons=score(t); tgt=calc_tgt(t)
        ai,_=await claude_ai(sym,t,sc,reasons,tgt,sent_d,fins_d)
        if sc>=MIN_SCORE:
            await msg.edit_text(fmt(sym,t,sc,sig,reasons,tgt,ai),parse_mode="Markdown")
        else:
            ses,_=get_session()
            await msg.edit_text(
                f"📊 *تحليل {sym}*\n"
                f"💵 `${t['price']}` | {t['change_pct']}% | {ses}\n\n"
                f"RSI:{t.get('rsi')} Stoch:{t.get('stoch_k')} WR:{t.get('williams_r')}\n"
                f"CCI:{t.get('cci')} MACD:{t.get('macd_hist')} Vol:{t.get('vol_ratio')}x\n"
                f"دعم:${t.get('support')} مقاومة:${t.get('resistance')}\n"
                +(f"🕯️ {' | '.join(t.get('patterns',[]))}\n" if t.get('patterns') else "")
                +f"\n⚠️ النقاط: *{sc}/100* (الحد: {MIN_SCORE})\n\n🧠 {ai}",
                parse_mode="Markdown")
    except Exception as e:
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
    log.info("🤖 Super Trading Bot v3.0 — Ultimate Edition")
    app.run_polling(drop_pending_updates=True)

if __name__=="__main__":
    main()
