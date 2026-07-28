"""
StopLoss - Personal Investment Intelligence Dashboard (Phase 1)
Modules in this version: Market Snapshot, FII/DII Flow, Watchlist with fundamentals.
Built with free data sources (Yahoo Finance via yfinance, NSE India via nsepython).
"""

import streamlit as st
import yfinance as yf
import pandas as pd
from datetime import datetime

try:
    from nsepython import nse_fiidii
except Exception:
    nse_fiidii = None

st.set_page_config(page_title="StopLoss", page_icon="📈", layout="wide")

# ---------- Config ----------
DEFAULT_WATCHLIST = ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS"]
INDICES = {
    "NIFTY 50": "^NSEI",
    "SENSEX": "^BSESN",
    "BANK NIFTY": "^NSEBANK",
}

if "watchlist" not in st.session_state:
    st.session_state.watchlist = DEFAULT_WATCHLIST.copy()


# ---------- Data fetchers (cached so we don't hammer the free APIs) ----------
@st.cache_data(ttl=300, show_spinner=False)
def get_index_snapshot():
    rows = []
    for name, ticker in INDICES.items():
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="2d")
            if len(hist) >= 2:
                prev_close = hist["Close"].iloc[-2]
                last = hist["Close"].iloc[-1]
                change = last - prev_close
                pct = (change / prev_close) * 100
                rows.append({"Index": name, "Level": round(last, 2),
                             "Change": round(change, 2), "Change %": round(pct, 2)})
            else:
                rows.append({"Index": name, "Level": None, "Change": None, "Change %": None})
        except Exception:
            rows.append({"Index": name, "Level": None, "Change": None, "Change %": None})
    return pd.DataFrame(rows)


@st.cache_data(ttl=1800, show_spinner=False)
def get_fii_dii():
    """Daily FII/DII provisional buy/sell data from NSE (free, official)."""
    if nse_fiidii is None:
        return None
    try:
        df = nse_fiidii()
        return df
    except Exception:
        return None


@st.cache_data(ttl=300, show_spinner=False)
def get_watchlist_data(tickers):
    rows = []
    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            info = t.info
            hist = t.history(period="2d")
            if len(hist) >= 2:
                prev_close = hist["Close"].iloc[-2]
                last = hist["Close"].iloc[-1]
                pct = ((last - prev_close) / prev_close) * 100
            else:
                last, pct = info.get("currentPrice"), None

            rows.append({
                "Symbol": ticker.replace(".NS", ""),
                "Price": round(last, 2) if last else None,
                "Change %": round(pct, 2) if pct is not None else None,
                "PE Ratio": round(info.get("trailingPE"), 2) if info.get("trailingPE") else None,
                "EPS": round(info.get("trailingEps"), 2) if info.get("trailingEps") else None,
                "Market Cap (Cr)": round(info.get("marketCap", 0) / 1e7, 0) if info.get("marketCap") else None,
                "52W High": info.get("fiftyTwoWeekHigh"),
                "52W Low": info.get("fiftyTwoWeekLow"),
            })
        except Exception as e:
            rows.append({"Symbol": ticker.replace(".NS", ""), "Price": None, "Change %": None,
                         "PE Ratio": None, "EPS": None, "Market Cap (Cr)": None,
                         "52W High": None, "52W Low": None})
    return pd.DataFrame(rows)


# ---------- UI ----------
st.title("📈 StopLoss")
st.caption(f"Your personal market snapshot — {datetime.now().strftime('%d %b %Y, %I:%M %p')}")

st.divider()

# --- Market Snapshot ---
st.subheader("Market Snapshot")
with st.spinner("Fetching indices..."):
    idx_df = get_index_snapshot()

cols = st.columns(len(INDICES))
for col, (_, row) in zip(cols, idx_df.iterrows()):
    if row["Level"] is not None:
        col.metric(row["Index"], f"{row['Level']:,}", f"{row['Change %']}%")
    else:
        col.metric(row["Index"], "N/A", "data unavailable")

st.divider()

# --- FII / DII Flow ---
st.subheader("FII / DII Activity (₹ Cr, Provisional)")
fii_dii_df = get_fii_dii()
if fii_dii_df is not None and len(fii_dii_df) > 0:
    st.dataframe(fii_dii_df, use_container_width=True, hide_index=True)
else:
    st.info("FII/DII data unavailable right now — NSE's public endpoint occasionally blocks "
            "automated requests. This usually resolves on refresh; if it persists often, "
            "we can swap in a different free source.")

st.divider()

# --- Watchlist ---
st.subheader("My Watchlist")

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

st.caption(
    "Data: Yahoo Finance (prices, PE, EPS) + NSE India (FII/DII). Prices may be delayed. "
    "This is a personal research tool, not investment advice."
)
