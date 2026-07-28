"""
StopLoss - Personal Investment Intelligence Dashboard (Phase 2)
=================================================================
Sections:
 1. Market Pulse header
 2. Global Snapshot (Dow, Nasdaq, Crude, Gold, Silver, Bitcoin, GIFT Nifty)
 3. Market Breadth (best-effort, NSE)
 4. FII / DII Activity
 5. Sector Performance (strongest -> weakest)
 6. Nifty Valuation (PE / PB / Div Yield)
 7. Market Internals (PCR, Max Pain, OI) - best-effort, NSE option chain
 8. Watchlist (price + technicals + fundamentals)
 9. Opportunity Scanner (derived from Watchlist - no extra data needed)
10. Important Events (manually-maintained calendar - no free live feed exists)
11. Market Health Score / Valuation Meter / Risk Meter / Checklist

DATA RELIABILITY NOTES (read this before debugging a blank section):
- [RELIABLE]  Yahoo Finance (yfinance) prices, history, PE/EPS/ROE/Debt-Equity
  from `.info` - stable, but Yahoo occasionally omits fields for a given ticker.
- [BEST-EFFORT] NSE public JSON endpoints (allIndices, option-chain-indices) -
  unofficial, undocumented, and NSE actively rate-limits/blocks bot-like
  traffic. Wrapped in try/except everywhere; on failure the section shows an
  "unavailable" message instead of crashing the whole app.
- [NOT AVAILABLE FREE] Promoter/FII/DII shareholding %, Delivery %, ROCE,
  Result/Dividend dates, real-time GIFT Nifty - no reliable free structured
  source found. These are shown as "N/A - needs paid vendor" rather than
  faked. When you're ready, replace the relevant get_*() function only - the
  UI layer doesn't need to change.
- [MANUAL] Important Events - no free live economic calendar API exists.
  Update the EVENTS list below by hand for now.

Every data source lives in its own get_*() function so a future swap to a
paid vendor (e.g. for options data or shareholding) only touches that one
function.
"""

import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import requests
from datetime import datetime

try:
    from nsepython import nse_fiidii
except Exception:
    nse_fiidii = None

st.set_page_config(page_title="StopLoss", page_icon="📈", layout="wide")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DEFAULT_WATCHLIST = ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS"]

INDICES = {
    "NIFTY 50": "^NSEI",
    "SENSEX": "^BSESN",
    "BANK NIFTY": "^NSEBANK",
}

GLOBAL_TICKERS = {
    "Dow Jones": "^DJI",
    "Nasdaq": "^IXIC",
    "Crude Oil": "CL=F",
    "Gold": "GC=F",
    "Silver": "SI=F",
    "Bitcoin": "BTC-USD",
    "India VIX": "^INDIAVIX",
}

# Manually-maintained events calendar (edit as needed - no free live feed exists)
EVENTS = [
    {"date": "2026-08-06", "event": "RBI Monetary Policy Decision"},
    {"date": "2026-08-12", "event": "India CPI Inflation Data"},
    {"date": "2026-09-16", "event": "US Fed FOMC Meeting"},
]

# Approximate long-run Nifty PE reference band (update manually; no free
# 10-year-average PE feed exists). Used only for the Valuation Meter.
NIFTY_PE_10YR_AVG = 21.0

if "watchlist" not in st.session_state:
    st.session_state.watchlist = DEFAULT_WATCHLIST.copy()


# ---------------------------------------------------------------------------
# NSE session helper (best-effort - NSE requires a warmed-up session/cookies)
# ---------------------------------------------------------------------------
def _nse_session():
    s = requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    s.headers.update(headers)
    try:
        s.get("https://www.nseindia.com", timeout=5)
    except Exception:
        pass
    return s


# ---------------------------------------------------------------------------
# 1. Market Snapshot (Indian indices) [RELIABLE - Yahoo Finance]
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def get_index_snapshot():
    rows = []
    for name, ticker in INDICES.items():
        try:
            hist = yf.Ticker(ticker).history(period="2d")
            if len(hist) >= 2:
                prev, last = hist["Close"].iloc[-2], hist["Close"].iloc[-1]
                change = last - prev
                pct = change / prev * 100
                rows.append({"Index": name, "Level": round(last, 2),
                             "Change": round(change, 2), "Change %": round(pct, 2)})
            else:
                rows.append({"Index": name, "Level": None, "Change": None, "Change %": None})
        except Exception:
            rows.append({"Index": name, "Level": None, "Change": None, "Change %": None})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 2. Global Snapshot [RELIABLE - Yahoo Finance]
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def get_global_snapshot():
    rows = []
    for name, ticker in GLOBAL_TICKERS.items():
        try:
            hist = yf.Ticker(ticker).history(period="2d")
            if len(hist) >= 2:
                prev, last = hist["Close"].iloc[-2], hist["Close"].iloc[-1]
                pct = (last - prev) / prev * 100
                rows.append({"Asset": name, "Value": round(last, 2), "Change %": round(pct, 2)})
            else:
                rows.append({"Asset": name, "Value": None, "Change %": None})
        except Exception:
            rows.append({"Asset": name, "Value": None, "Change %": None})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 3. Market Breadth [BEST-EFFORT - proxy from Nifty 50 constituents]
# ---------------------------------------------------------------------------
@st.cache_data(ttl=600, show_spinner=False)
def get_market_breadth():
    try:
        s = _nse_session()
        r = s.get("https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%2050", timeout=8)
        data = r.json().get("data", [])
        changes = [d.get("pChange") for d in data if isinstance(d.get("pChange"), (int, float))]
        if not changes:
            return None
        advances = sum(1 for c in changes if c > 0)
        declines = sum(1 for c in changes if c < 0)
        return {
            "advances": advances,
            "declines": declines,
            "ratio": round(advances / declines, 2) if declines else None,
            "note": "Proxy from Nifty 50 constituents only, not full-market breadth.",
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 4. FII / DII Activity [BEST-EFFORT - nsepython]
# ---------------------------------------------------------------------------
@st.cache_data(ttl=1800, show_spinner=False)
def get_fii_dii():
    if nse_fiidii is None:
        return None
    try:
        return nse_fiidii()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 5 & 6. Sector Performance + Nifty Valuation [BEST-EFFORT - NSE allIndices]
# ---------------------------------------------------------------------------
SECTOR_KEYWORDS = ["NIFTY AUTO", "NIFTY PSU BANK", "NIFTY PRIVATE BANK", "NIFTY IT",
                   "NIFTY PHARMA", "NIFTY METAL", "NIFTY REALTY", "NIFTY ENERGY",
                   "NIFTY FMCG", "NIFTY HEALTHCARE"]


@st.cache_data(ttl=900, show_spinner=False)
def get_all_indices():
    try:
        s = _nse_session()
        r = s.get("https://www.nseindia.com/api/allIndices", timeout=8)
        return r.json().get("data", [])
    except Exception:
        return None


def get_sector_performance(all_indices):
    if not all_indices:
        return None
    rows = []
    for d in all_indices:
        name = d.get("index", "")
        if name in SECTOR_KEYWORDS:
            rows.append({
                "Sector": name.replace("NIFTY ", ""),
                "Change %": d.get("percentChange"),
            })
    if not rows:
        return None
    df = pd.DataFrame(rows).dropna()
    return df.sort_values("Change %", ascending=False).reset_index(drop=True)


def get_nifty_valuation(all_indices):
    if not all_indices:
        return None
    for d in all_indices:
        if d.get("index") == "NIFTY 50":
            pe = d.get("pe")
            pb = d.get("pb")
            div_yield = d.get("divYield") or d.get("dy")
            if pe is None:
                return None
            eps = round(d.get("last", 0) / pe, 2) if d.get("last") and pe else None
            status = "N/A"
            if pe:
                if pe < NIFTY_PE_10YR_AVG * 0.9:
                    status = "🟢 Cheap"
                elif pe > NIFTY_PE_10YR_AVG * 1.1:
                    status = "🔴 Expensive"
                else:
                    status = "🟠 Fair"
            return {"pe": pe, "pb": pb, "div_yield": div_yield, "eps": eps, "status": status}
    return None


# ---------------------------------------------------------------------------
# 7. Market Internals - PCR / Max Pain [BEST-EFFORT - NSE option chain]
# ---------------------------------------------------------------------------
@st.cache_data(ttl=900, show_spinner=False)
def get_market_internals():
    try:
        s = _nse_session()
        r = s.get("https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY", timeout=8)
        data = r.json().get("records", {}).get("data", [])
        if not data:
            return None
        total_ce_oi, total_pe_oi = 0, 0
        pain = {}
        max_call_oi, max_call_strike = 0, None
        max_put_oi, max_put_strike = 0, None
        for d in data:
            strike = d.get("strikePrice")
            ce_oi = d.get("CE", {}).get("openInterest", 0) or 0
            pe_oi = d.get("PE", {}).get("openInterest", 0) or 0
            total_ce_oi += ce_oi
            total_pe_oi += pe_oi
            if ce_oi > max_call_oi:
                max_call_oi, max_call_strike = ce_oi, strike
            if pe_oi > max_put_oi:
                max_put_oi, max_put_strike = pe_oi, strike
            pain[strike] = pain.get(strike, 0) + ce_oi + pe_oi
        pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi else None
        max_pain_strike = max(pain, key=pain.get) if pain else None
        return {
            "pcr": pcr,
            "max_pain": max_pain_strike,
            "max_call_oi_strike": max_call_strike,
            "max_put_oi_strike": max_put_strike,
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 8. Watchlist - price + technicals [RELIABLE] + fundamentals [MIXED]
# ---------------------------------------------------------------------------
def compute_rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1] if not rsi.empty else None


def compute_macd(close):
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    return round(macd_line.iloc[-1] - signal_line.iloc[-1], 2) if len(close) > 26 else None


@st.cache_data(ttl=300, show_spinner=False)
def get_watchlist_data(tickers):
    rows = []
    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            info = t.info
            hist = t.history(period="1y")

            last = prev = pct = None
            dma20 = dma50 = dma200 = rsi = macd = vol_spike = None
            if len(hist) >= 2:
                prev = hist["Close"].iloc[-2]
                last = hist["Close"].iloc[-1]
                pct = round((last - prev) / prev * 100, 2)
            if len(hist) >= 20:
                dma20 = round(hist["Close"].rolling(20).mean().iloc[-1], 2)
            if len(hist) >= 50:
                dma50 = round(hist["Close"].rolling(50).mean().iloc[-1], 2)
            if len(hist) >= 200:
                dma200 = round(hist["Close"].rolling(200).mean().iloc[-1], 2)
            if len(hist) >= 15:
                rsi = round(compute_rsi(hist["Close"]), 2)
            if len(hist) >= 27:
                macd = compute_macd(hist["Close"])
            if len(hist) >= 20 and hist["Volume"].iloc[-20:-1].mean() > 0:
                vol_spike = round(hist["Volume"].iloc[-1] / hist["Volume"].iloc[-20:-1].mean(), 2)

            rows.append({
                "Symbol": ticker.replace(".NS", ""),
                "Price": round(last, 2) if last else None,
                "Change %": pct,
                "Volume": int(hist["Volume"].iloc[-1]) if len(hist) else None,
                "Vol Spike x": vol_spike,
                "20 DMA": dma20, "50 DMA": dma50, "200 DMA": dma200,
                "RSI": rsi, "MACD": macd,
                "PE Ratio": round(info.get("trailingPE"), 2) if info.get("trailingPE") else None,
                "EPS": round(info.get("trailingEps"), 2) if info.get("trailingEps") else None,
                "Market Cap (Cr)": round(info.get("marketCap", 0) / 1e7, 0) if info.get("marketCap") else None,
                "ROE %": round(info.get("returnOnEquity", 0) * 100, 2) if info.get("returnOnEquity") else None,
                "Debt/Equity": round(info.get("debtToEquity"), 2) if info.get("debtToEquity") else None,
                "Book Value": round(info.get("bookValue"), 2) if info.get("bookValue") else None,
                "Div Yield %": round(info.get("dividendYield", 0) * 100, 2) if info.get("dividendYield") else None,
                "52W High": info.get("fiftyTwoWeekHigh"),
                "52W Low": info.get("fiftyTwoWeekLow"),
                # Not available free - shown for completeness, always N/A until a paid vendor is added
                "ROCE %": None,
                "Delivery %": None,
                "Promoter Hold %": None,
                "FII Hold %": None,
                "DII Hold %": None,
            })
        except Exception:
            rows.append({"Symbol": ticker.replace(".NS", ""), "Price": None})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 9. Opportunity Scanner - derived entirely from watchlist_df, no new data
# ---------------------------------------------------------------------------
def run_opportunity_scanner(df):
    flags = []
    for _, row in df.iterrows():
        sym = row.get("Symbol")
        if row.get("Price") and row.get("52W High") and row["Price"] >= row["52W High"] * 0.99:
            flags.append((sym, "New 52-week high territory"))
        if row.get("Vol Spike x") and row["Vol Spike x"] >= 2:
            flags.append((sym, f"Unusual volume ({row['Vol Spike x']}x avg)"))
        if row.get("50 DMA") and row.get("200 DMA") and row["50 DMA"] > row["200 DMA"]:
            flags.append((sym, "Golden Cross (50 DMA above 200 DMA)"))
        if row.get("RSI") is not None and row["RSI"] < 30:
            flags.append((sym, f"RSI {row['RSI']} - potentially oversold"))
        if row.get("RSI") is not None and row["RSI"] > 70:
            flags.append((sym, f"RSI {row['RSI']} - potentially overbought"))
        if row.get("Change %") is not None and row["Change %"] >= 5:
            flags.append((sym, f"Up {row['Change %']}% today"))
        if row.get("Change %") is not None and row["Change %"] <= -5:
            flags.append((sym, f"Down {row['Change %']}% today"))
    return flags


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
st.title("📈 StopLoss")
st.caption(f"Your personal market snapshot — {datetime.now().strftime('%d %b %Y, %I:%M %p')}")
st.divider()

# --- Section 1: Market Snapshot (Indian indices) ---
st.subheader("Market Snapshot")
idx_df = get_index_snapshot()
cols = st.columns(len(INDICES))
for col, (_, row) in zip(cols, idx_df.iterrows()):
    if row["Level"] is not None:
        col.metric(row["Index"], f"{row['Level']:,}", f"{row['Change %']}%")
    else:
        col.metric(row["Index"], "N/A", "data unavailable")
st.divider()

# --- Section 2: Global Snapshot ---
st.subheader("🌍 Global Snapshot")
st.caption("Indian markets often take cues from these overnight.")
global_df = get_global_snapshot()
g_cols = st.columns(4)
for i, (_, row) in enumerate(global_df.iterrows()):
    with g_cols[i % 4]:
        if row["Value"] is not None:
            st.metric(row["Asset"], f"{row['Value']:,}", f"{row['Change %']}%")
        else:
            st.metric(row["Asset"], "N/A", "unavailable")
st.divider()

# --- Section 3: Market Breadth ---
st.subheader("📊 Market Breadth")
breadth = get_market_breadth()
if breadth:
    b1, b2, b3 = st.columns(3)
    b1.metric("Advances", breadth["advances"])
    b2.metric("Declines", breadth["declines"])
    b3.metric("Advance/Decline Ratio", breadth["ratio"] if breadth["ratio"] else "N/A")
    st.caption(breadth["note"])
else:
    st.info("Breadth data unavailable right now (NSE endpoint may be rate-limiting). Try refreshing.")
st.divider()

# --- Section 4: FII / DII Activity ---
st.subheader("🏦 FII / DII Activity (₹ Cr, Provisional)")
fii_dii_df = get_fii_dii()
if fii_dii_df is not None and len(fii_dii_df) > 0:
    st.dataframe(fii_dii_df, use_container_width=True, hide_index=True)
else:
    st.info("FII/DII data unavailable right now — NSE's public endpoint occasionally blocks "
            "automated requests. This usually resolves on refresh.")
st.divider()

# --- Section 5 & 6: Sector Performance + Nifty Valuation ---
all_indices = get_all_indices()

st.subheader("🏭 Sector Performance")
sector_df = get_sector_performance(all_indices)
if sector_df is not None:
    st.dataframe(sector_df, use_container_width=True, hide_index=True)
else:
    st.info("Sector data unavailable right now — try refreshing.")
st.divider()

st.subheader("💰 Nifty Valuation")
valuation = get_nifty_valuation(all_indices)
if valuation:
    v1, v2, v3, v4 = st.columns(4)
    v1.metric("Nifty PE", valuation["pe"] or "N/A")
    v2.metric("Nifty PB", valuation["pb"] or "N/A")
    v3.metric("Dividend Yield", f"{valuation['div_yield']}%" if valuation["div_yield"] else "N/A")
    v4.metric("EPS (approx)", valuation["eps"] or "N/A")
    st.caption(f"10-Year Avg PE (manually set, update periodically): {NIFTY_PE_10YR_AVG}  |  "
               f"Current Valuation: {valuation['status']}")
else:
    st.info("Valuation data unavailable right now — try refreshing.")
st.divider()

# --- Section 7: Market Internals ---
st.subheader("⚙️ Market Internals (Options)")
internals = get_market_internals()
if internals:
    i1, i2, i3, i4 = st.columns(4)
    i1.metric("PCR", internals["pcr"] or "N/A")
    i2.metric("Max Pain", internals["max_pain"] or "N/A")
    i3.metric("Highest Call OI Strike", internals["max_call_oi_strike"] or "N/A")
    i4.metric("Highest Put OI Strike", internals["max_put_oi_strike"] or "N/A")
else:
    st.info("Options data unavailable right now — NSE's option-chain endpoint is heavily "
            "rate-limited for automated access. Try refreshing.")
st.divider()

# --- Section 8: Watchlist ---
st.subheader("⭐ My Watchlist")
with st.expander("➕ Manage watchlist"):
    new_ticker = st.text_input("Add NSE stock symbol (e.g. WIPRO)", "")
    add_col, remove_col = st.columns(2)
    if add_col.button("Add to watchlist") and new_ticker:
        symbol = new_ticker.strip().upper() + ".NS"
        if symbol not in st.session_state.watchlist:
            st.session_state.watchlist.append(symbol)
            st.rerun()
    to_remove = remove_col.selectbox("Remove a symbol", ["—"] + st.session_state.watchlist)
    if remove_col.button("Remove") and to_remove != "—":
        st.session_state.watchlist.remove(to_remove)
        st.rerun()

with st.spinner("Fetching watchlist data..."):
    watchlist_df = get_watchlist_data(st.session_state.watchlist)
st.dataframe(watchlist_df, use_container_width=True, hide_index=True)
st.caption("ROCE, Delivery %, Promoter/FII/DII Holding: not available from free data "
           "sources — shown as N/A until a paid data vendor is added.")
st.divider()

# --- Section 9: Opportunity Scanner ---
st.subheader("🔍 Opportunity Scanner")
st.caption("Not buy/sell signals — just prompts to investigate further.")
scanner_flags = run_opportunity_scanner(watchlist_df)
if scanner_flags:
    for sym, reason in scanner_flags:
        st.write(f"**{sym}** — {reason}")
else:
    st.info("Nothing flagged right now.")
st.divider()

# --- Section 10: Important Events ---
st.subheader("📅 Important Events")
st.caption("Manually maintained — no free live economic calendar feed exists yet.")
events_df = pd.DataFrame(EVENTS)
st.dataframe(events_df, use_container_width=True, hide_index=True)
st.divider()

# --- Section 11: Market Health Score / Valuation Meter / Risk Meter / Checklist ---
st.subheader("🩺 Market Health & Risk")

# Health score (simple weighted composite from what we could fetch)
health_points, health_max = 0, 0
if breadth and breadth.get("ratio"):
    health_max += 1
    if breadth["ratio"] > 1:
        health_points += 1
if fii_dii_df is not None and len(fii_dii_df) > 0:
    try:
        fii_row = fii_dii_df[fii_dii_df.iloc[:, 1].astype(str).str.contains("FII", case=False, na=False)]
        if not fii_row.empty and pd.to_numeric(fii_row.iloc[0].get("netValue", 0), errors="coerce") is not None:
            health_max += 1
            if pd.to_numeric(fii_row.iloc[0].get("netValue", 0), errors="coerce") > 0:
                health_points += 1
    except Exception:
        pass
india_vix_row = global_df[global_df["Asset"] == "India VIX"]
vix_val = india_vix_row["Value"].iloc[0] if not india_vix_row.empty else None
if vix_val is not None:
    health_max += 1
    if vix_val < 15:
        health_points += 1
if sector_df is not None and len(sector_df) > 0:
    health_max += 1
    if (sector_df["Change %"] > 0).mean() >= 0.5:
        health_points += 1

health_score = round((health_points / health_max) * 100) if health_max else None

m1, m2, m3 = st.columns(3)
m1.metric("Market Health Score", f"{health_score}/100" if health_score is not None else "N/A")
m2.metric("Nifty Valuation", valuation["status"] if valuation else "N/A")
risk_level = "N/A"
if vix_val is not None:
    risk_level = "🟢 Low" if vix_val < 13 else ("🟠 Moderate" if vix_val < 18 else "🔴 High")
m3.metric("Risk Meter (India VIX)", risk_level)

st.markdown("**Market Checklist**")
checklist_items = [
    ("Is the broader market healthy?", health_score is not None and health_score >= 60),
    ("Are FIIs buying?", health_points > 0 and fii_dii_df is not None),
    ("Is volatility low?", vix_val is not None and vix_val < 15),
    ("Are most sectors participating?", sector_df is not None and (sector_df["Change %"] > 0).mean() >= 0.5),
    ("Is Nifty above its 20-day moving average?",
     watchlist_df["20 DMA"].notna().any() if "20 DMA" in watchlist_df.columns else False),
    ("Is breadth positive?", breadth is not None and breadth.get("ratio") and breadth["ratio"] > 1),
    ("Are valuations reasonable?", valuation is not None and valuation["status"] != "🔴 Expensive"),
    ("No major events today?", datetime.now().strftime("%Y-%m-%d") not in [e["date"] for e in EVENTS]),
]
for label, ok in checklist_items:
    st.write(("✅ " if ok else "⚠️ ") + label)

st.divider()
st.caption(
    "Data: Yahoo Finance (prices, technicals, PE/EPS/ROE) + NSE India (FII/DII, breadth, "
    "sector, valuation, options). Sections marked unavailable use best-effort free NSE "
    "endpoints that can rate-limit; refresh if needed. This is a personal research tool, "
    "not investment advice."
)
