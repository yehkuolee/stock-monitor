import streamlit as st
import yfinance as yf
import pandas as pd
import ta
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import urllib3
from datetime import datetime
from streamlit_autorefresh import st_autorefresh

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

st.set_page_config(
    page_title="台股技術分析監測器",
    layout="wide",
    page_icon="📈",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
/* 全頁深色背景 */
html, body, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
    background-color: #0d1117 !important;
    color: #e6edf3 !important;
}
[data-testid="stHeader"] { background: #0d1117 !important; }
[data-testid="stSidebar"] { background: #161b22 !important; }

/* 壓縮頁面 padding */
.block-container { padding: 0.5rem 1rem 0.5rem 1rem !important; max-width: 100% !important; }
[data-testid="stVerticalBlock"] { gap: 0.3rem !important; }

/* 搜尋列固定在頁面頂部 */
.search-bar {
    position: sticky;
    top: 0;
    z-index: 999;
    background: #0d1117;
    padding: 6px 0 6px 0;
    border-bottom: 1px solid #30363d;
    margin-bottom: 6px;
}

/* 自訂元件卡片 */
.card {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 8px 12px;
    margin-bottom: 4px;
}
.card-title {
    color: #8b949e;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    margin-bottom: 6px;
}

/* 行情數字 */
.price-big { font-size: 28px; font-weight: 700; color: #f85149; }
.chg-up { color: #3fb950; font-weight: 600; }
.chg-dn { color: #f85149; font-weight: 600; }
.chg-flat { color: #8b949e; font-weight: 600; }

/* 標籤徽章 */
.badge-red    { color:#f85149; font-weight:700; }
.badge-green  { color:#3fb950; font-weight:700; }
.badge-yellow { color:#d29922; font-weight:700; }
.badge-gray   { color:#8b949e; font-weight:700; }

/* 法人表格 */
.inst-table { width:100%; border-collapse:collapse; font-size:12px; }
.inst-table th { color:#8b949e; font-weight:600; padding:3px 6px; border-bottom:1px solid #30363d; text-align:right; }
.inst-table th:first-child { text-align:left; }
.inst-table td { padding:3px 6px; text-align:right; border-bottom:1px solid #21262d; }
.inst-table td:first-child { text-align:left; color:#8b949e; }
.pos { color:#3fb950; } .neg { color:#f85149; }

/* 關鍵價位表 */
.level-table { width:100%; border-collapse:collapse; font-size:13px; }
.level-table td { padding:5px 8px; border-bottom:1px solid #21262d; }
.level-table td:last-child { text-align:right; font-weight:700; }
.level-res { color:#f85149; } .level-sup { color:#3fb950; } .level-pb { color:#d29922; }

/* 技術指標列表 */
.tech-row { display:flex; justify-content:space-between; padding:4px 0; border-bottom:1px solid #21262d; font-size:13px; }
.tech-label { color:#8b949e; }

/* 操作建議 */
.suggest-box { background:#0d1117; border-left:3px solid #3fb950; padding:6px 10px; margin:3px 0; font-size:12px; border-radius:0 4px 4px 0; }
.suggest-box.warn { border-left-color:#f85149; }
.suggest-box.neutral { border-left-color:#d29922; }

/* 機率儀表 */
.prob-box { text-align:center; padding:8px; background:#0d1117; border-radius:6px; }
.prob-num { font-size:22px; font-weight:700; }
.prob-label { font-size:11px; color:#8b949e; margin-top:2px; }

/* 燈號 */
.signal-dot { width:60px; height:60px; border-radius:50%; margin:0 auto 6px; }
.signal-red { background:radial-gradient(circle at 35% 35%, #ff6b6b, #c0392b); box-shadow:0 0 15px #f85149; }
.signal-green { background:radial-gradient(circle at 35% 35%, #6bff8e, #27ae60); box-shadow:0 0 15px #3fb950; }
.signal-yellow { background:radial-gradient(circle at 35% 35%, #ffe66b, #d4ac0d); box-shadow:0 0 15px #d29922; }
.signal-label { text-align:center; font-size:13px; font-weight:700; }
.signal-sub { text-align:center; font-size:11px; color:#8b949e; margin-top:2px; }

/* Streamlit metric 壓縮 */
[data-testid="metric-container"] {
    background: #161b22 !important;
    border: 1px solid #30363d !important;
    border-radius: 8px !important;
    padding: 8px 12px !important;
}
[data-testid="stMetricValue"] { font-size: 18px !important; }
[data-testid="stMetricLabel"] { font-size: 11px !important; }

/* 隱藏 streamlit 預設間距 */
hr { margin: 6px 0 !important; border-color: #30363d !important; }
h3 { font-size: 13px !important; margin: 4px 0 !important; color: #8b949e !important; font-weight:600; letter-spacing:0.05em; }
</style>
""", unsafe_allow_html=True)


# ── 資料抓取 ──────────────────────────────────────────────────────────────────

def fetch_realtime(symbol: str) -> dict:
    """TWSE 即時報價（不快取，每次都抓最新）"""
    for ex in ["tse", "otc"]:
        try:
            url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={ex}_{symbol}.tw&json=1&delay=0"
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"},
                             timeout=5, verify=False)
            d = r.json().get("msgArray", [{}])[0]
            if d.get("z") and d["z"] != "-":
                def safe_float(v):
                    try: return float(v)
                    except: return None
                return {
                    "name":    d.get("n", symbol),
                    "price":   safe_float(d.get("z")),
                    "open":    safe_float(d.get("o")),
                    "high":    safe_float(d.get("h")),
                    "low":     safe_float(d.get("l")),
                    "volume":  safe_float(d.get("v")),
                    "yday":    safe_float(d.get("y")),
                    "limit_up":  safe_float(d.get("u")),
                    "limit_dn":  safe_float(d.get("w")),
                    "bid":     safe_float((d.get("b","").split("_")[0])),
                    "ask":     safe_float((d.get("a","").split("_")[0])),
                    "time":    d.get("t", ""),
                    "date":    d.get("d", ""),
                    "exchange": ex,
                }
        except Exception:
            continue
    return {}


@st.cache_data(ttl=60)
def fetch_intraday(symbol: str) -> pd.DataFrame:
    """盤中 5 分 K，快取 60 秒"""
    df = yf.download(symbol + ".TW", period="1d", interval="5m",
                     auto_adjust=True, progress=False)
    if df.empty:
        df = yf.download(symbol + ".TWO", period="1d", interval="5m",
                         auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    # 轉台北時間
    if not df.empty and df.index.tz is not None:
        df.index = df.index.tz_convert("Asia/Taipei")
    return df


def is_market_open() -> bool:
    now = datetime.now()
    # 週一到週五 09:00~13:35
    if now.weekday() >= 5:
        return False
    t = now.hour * 60 + now.minute
    return 9 * 60 <= t <= 13 * 60 + 35


@st.cache_data(ttl=900)
def fetch_daily(symbol: str) -> pd.DataFrame:
    for suffix in [".TW", ".TWO"]:
        df = yf.download(symbol + suffix, period="6mo", interval="1d",
                         auto_adjust=True, progress=False)
        if not df.empty:
            break
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


@st.cache_data(ttl=900)
def fetch_60min(symbol: str) -> pd.DataFrame:
    df = yf.download(symbol + ".TW", period="5d", interval="60m",
                     auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


@st.cache_data(ttl=900)
def fetch_weekly(symbol: str) -> pd.DataFrame:
    df = yf.download(symbol + ".TW", period="1y", interval="1wk",
                     auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


@st.cache_data(ttl=1800)
def fetch_institutional(symbol: str) -> list:
    url = "https://www.twse.com.tw/rwd/zh/fund/T86?response=json&selectType=ALL"
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        data = resp.json()
        if data.get("stat") != "OK":
            return []
        cols = ["代號", "名稱", "外資買", "外資賣", "外資淨",
                "投信買", "投信賣", "投信淨", "自營買", "自營賣", "自營淨", "合計"]
        results = []
        for row in data.get("data", []):
            if len(row) >= 12 and row[0] == symbol:
                r = dict(zip(cols, row))
                for k in ["外資淨", "投信淨", "自營淨", "合計"]:
                    try:
                        r[k] = int(r[k].replace(",", ""))
                    except Exception:
                        r[k] = 0
                r["日期"] = data.get("date", "")[:4] + "/" + data.get("date", "")[4:6] + "/" + data.get("date", "")[6:]
                results.append(r)
        return results
    except Exception:
        return []


# ── 指標計算 ──────────────────────────────────────────────────────────────────

def calc_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or len(df) < 10:
        return df
    df = df.copy()
    close = df["Close"].squeeze()
    high  = df["High"].squeeze()
    low   = df["Low"].squeeze()

    bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
    df["BB_upper"] = bb.bollinger_hband()
    df["BB_mid"]   = bb.bollinger_mavg()
    df["BB_lower"] = bb.bollinger_lband()

    stoch = ta.momentum.StochasticOscillator(high, low, close, window=9, smooth_window=3)
    df["K"] = stoch.stoch()
    df["D"] = stoch.stoch_signal()

    macd_obj = ta.trend.MACD(close, window_slow=26, window_fast=12, window_sign=9)
    df["DIF"]  = macd_obj.macd()
    df["MACD"] = macd_obj.macd_signal()
    df["OSC"]  = macd_obj.macd_diff()

    df["MA5"]  = close.rolling(5).mean()
    df["MA20"] = close.rolling(20).mean()
    df["MA60"] = close.rolling(60).mean()
    return df


# ── 訊號判讀 ──────────────────────────────────────────────────────────────────

def trend_label(df):
    last, prev5 = df.iloc[-1], df.iloc[min(-6, -len(df))]
    if float(last["Close"]) > float(prev5["Close"]) and float(last["MA5"]) > float(last["MA20"]):
        return "多頭趨勢", "green"
    elif float(last["Close"]) < float(prev5["Close"]) and float(last["MA5"]) < float(last["MA20"]):
        return "空頭趨勢", "red"
    return "盤整格局", "yellow"


def bb_label(close, upper, lower):
    pct = (close - lower) / (upper - lower + 1e-9) * 100
    if pct >= 80: return "貼近上軌（過熱）", "red"
    elif pct >= 50: return "中軌以上", "yellow"
    elif pct >= 20: return "中軌以下", "yellow"
    return "貼近下軌（超賣）", "green"


def ma_label(df):
    last = df.iloc[-1]
    ma5, ma20, ma60 = float(last["MA5"]), float(last["MA20"]), float(last["MA60"])
    if ma5 > ma20 > ma60: return "多頭排列 (5>20>60)", "green"
    elif ma5 < ma20 < ma60: return "空頭排列 (5<20<60)", "red"
    return "均線糾結", "yellow"


def kd_label(k, d):
    if k > 80 and d > 80: return f"高檔鈍化 K:{k:.1f}", "red"
    elif k < 20 and d < 20: return f"低檔鈍化 K:{k:.1f}", "green"
    elif k > d: return f"黃金交叉 K:{k:.1f}", "green"
    return f"死亡交叉 K:{k:.1f}", "red"


def macd_label(dif, sig, osc):
    if dif > 0 and osc > 0: return f"多頭延續 OSC:{osc:.2f}", "green"
    elif dif < 0 and osc < 0: return f"空頭延續 OSC:{osc:.2f}", "red"
    elif osc > 0: return f"多頭收斂 OSC:{osc:.2f}", "yellow"
    return f"空頭收斂 OSC:{osc:.2f}", "yellow"


def vol_label(df):
    avg = float(df["Volume"].tail(10).mean())
    last_vol = float(df["Volume"].iloc[-1])
    ratio = last_vol / avg if avg else 1
    if ratio > 1.5: return f"爆量 ({ratio:.1f}x)", "green"
    elif ratio < 0.7: return f"量縮 ({ratio:.1f}x)", "yellow"
    return f"量平 ({ratio:.1f}x)", "gray"


def bb_open_label(df):
    if len(df) < 5:
        return "資料不足", "gray"
    recent_width = float(df["BB_upper"].iloc[-1]) - float(df["BB_lower"].iloc[-1])
    prev_width   = float(df["BB_upper"].iloc[-5]) - float(df["BB_lower"].iloc[-5])
    if recent_width > prev_width * 1.05: return "開口擴大（趨勢加速）", "green"
    elif recent_width < prev_width * 0.95: return "開口收斂（盤整中）", "yellow"
    return "開口持平", "gray"


def key_levels(df):
    recent = df.tail(20)
    return (
        float(recent["High"].max()),
        float(recent["Low"].min()),
        round(float(df["Close"].iloc[-1]) * 0.96),
        round(float(df["Close"].iloc[-1]) * 0.99),
    )


def overall_signal(df):
    last = df.iloc[-1]
    k = float(last["K"])
    osc = float(last["OSC"])
    dif = float(last["DIF"])
    close = float(last["Close"])
    upper = float(last["BB_upper"])
    lower = float(last["BB_lower"])
    ma5, ma20 = float(last["MA5"]), float(last["MA20"])
    bb_pct = (close - lower) / (upper - lower + 1e-9) * 100

    score = sum([k < 60, osc > 0, ma5 > ma20, bb_pct < 70, dif > 0])
    if score >= 4: return "積極多頭", "green", "signal-green"
    elif score == 3: return "偏多觀察", "yellow", "signal-yellow"
    elif score == 2: return "中性整理", "yellow", "signal-yellow"
    else: return "出貨警示", "red", "signal-red"


def ai_suggest(df, score):
    resistance, support, pb_lo, pb_hi = key_levels(df)
    if score >= 3:
        main = f"✅ 技術偏多，回檔 {pb_lo:,.0f}～{pb_hi:,.0f} 可留意買點"
        cls = ""
    elif score <= 1:
        main = f"⚠️ 技術偏弱，觀望為宜，跌破 {support:,.0f} 停損"
        cls = "warn"
    else:
        main = "🔄 中性格局，等待方向確認再介入"
        cls = "neutral"
    stop = f"🔴 壓力 {resistance:,.0f}｜停損參考跌破 {support:,.0f}"
    up   = min(30 + score * 10, 65)
    dn   = max(65 - score * 10, 25)
    flat = 100 - up - dn
    return main, cls, stop, up, dn, flat


def ai_score_val(df):
    last = df.iloc[-1]
    k = float(last["K"]); osc = float(last["OSC"]); dif = float(last["DIF"])
    close = float(last["Close"])
    upper = float(last["BB_upper"]); lower = float(last["BB_lower"])
    bb_pct = (close - lower) / (upper - lower + 1e-9) * 100
    return sum([k < 60, osc > 0, float(last["MA5"]) > float(last["MA20"]), bb_pct < 70, dif > 0])


def badge_html(text, color):
    cls = {"green": "badge-green", "red": "badge-red",
           "yellow": "badge-yellow", "gray": "badge-gray"}.get(color, "badge-gray")
    return f'<span class="{cls}">{text}</span>'


# ── 圖表 ──────────────────────────────────────────────────────────────────────

def main_chart(df):
    fig = make_subplots(
        rows=4, cols=1, shared_xaxes=True,
        vertical_spacing=0.02,
        row_heights=[0.52, 0.13, 0.18, 0.17],
    )

    fig.add_trace(go.Candlestick(
        x=df.index, open=df["Open"], high=df["High"],
        low=df["Low"], close=df["Close"],
        increasing_line_color="#3fb950", decreasing_line_color="#f85149",
        name="K線", increasing_fillcolor="#3fb950", decreasing_fillcolor="#f85149"
    ), row=1, col=1)

    for col, name, color, dash in [
        ("BB_upper","上軌","#d29922","dot"),
        ("BB_mid","中軌","#555e6a","solid"),
        ("BB_lower","下軌","#d29922","dot"),
    ]:
        fig.add_trace(go.Scatter(x=df.index, y=df[col], name=name, showlegend=False,
            line=dict(color=color, width=1, dash=dash)), row=1, col=1)

    for col, name, color in [("MA5","MA5","#58a6ff"),("MA20","MA20","#bc8cff"),("MA60","MA60","#ffa657")]:
        fig.add_trace(go.Scatter(x=df.index, y=df[col], name=name,
            line=dict(color=color, width=1.2)), row=1, col=1)

    vol_colors = ["#3fb950" if float(c) >= float(o) else "#f85149"
                  for c, o in zip(df["Close"], df["Open"])]
    fig.add_trace(go.Bar(x=df.index, y=df["Volume"], marker_color=vol_colors,
        name="量", showlegend=False), row=2, col=1)

    fig.add_trace(go.Scatter(x=df.index, y=df["K"], name="K",
        line=dict(color="#f0883e", width=1.5)), row=3, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["D"], name="D",
        line=dict(color="#58a6ff", width=1.5)), row=3, col=1)
    fig.add_hline(y=80, line_dash="dot", line_color="#f85149", opacity=0.4, row=3, col=1)
    fig.add_hline(y=20, line_dash="dot", line_color="#3fb950", opacity=0.4, row=3, col=1)

    fig.add_trace(go.Scatter(x=df.index, y=df["DIF"], name="DIF",
        line=dict(color="#f0883e", width=1.5)), row=4, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["MACD"], name="Signal",
        line=dict(color="#58a6ff", width=1.5)), row=4, col=1)
    osc_colors = ["#3fb950" if float(v) >= 0 else "#f85149" for v in df["OSC"]]
    fig.add_trace(go.Bar(x=df.index, y=df["OSC"], marker_color=osc_colors,
        name="OSC", showlegend=False), row=4, col=1)

    # 標注最高點
    recent_high = float(df["High"].tail(20).max())
    high_idx = df["High"].tail(20).idxmax()
    fig.add_annotation(x=high_idx, y=recent_high, text=f"近期高點 {recent_high:,.0f}",
        showarrow=True, arrowhead=2, arrowcolor="#d29922", font=dict(color="#d29922", size=11),
        bgcolor="#0d1117", bordercolor="#d29922", row=1, col=1)

    fig.update_layout(
        paper_bgcolor="#0d1117", plot_bgcolor="#161b22",
        font=dict(color="#e6edf3", size=11),
        height=500,
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="right", x=1,
                    font=dict(size=10), bgcolor="rgba(0,0,0,0)"),
        margin=dict(l=50, r=10, t=10, b=10),
        hovermode="x unified",
    )
    fig.update_xaxes(gridcolor="#21262d", showgrid=True)
    fig.update_yaxes(gridcolor="#21262d", showgrid=True)

    # 子圖標題
    for i, label in enumerate(["K線 (日) + 布林通道 (20,2) + 均線", "成交量", "KD (9,3,3)", "MACD (12,26,9)"], 1):
        fig.add_annotation(
            x=0, y=1, xref=f"x{'' if i==1 else i} domain", yref=f"y{'' if i==1 else i} domain",
            text=f"<b>{label}</b>", showarrow=False,
            font=dict(size=10, color="#8b949e"), xanchor="left", yanchor="top"
        )

    return fig


def mini_candle(df, title, height=180):
    if df.empty:
        return go.Figure()
    fig = go.Figure(go.Candlestick(
        x=df.index, open=df["Open"], high=df["High"],
        low=df["Low"], close=df["Close"],
        increasing_line_color="#3fb950", decreasing_line_color="#f85149",
        showlegend=False
    ))
    if "MA5" in df.columns:
        fig.add_trace(go.Scatter(x=df.index, y=df["MA5"], line=dict(color="#58a6ff", width=1), showlegend=False))
    if "MA20" in df.columns:
        fig.add_trace(go.Scatter(x=df.index, y=df["MA20"], line=dict(color="#bc8cff", width=1), showlegend=False))
    fig.update_layout(
        title=dict(text=f"<b>{title}</b>", font=dict(size=11, color="#8b949e"), x=0.02, y=0.95),
        paper_bgcolor="#161b22", plot_bgcolor="#0d1117",
        font_color="#e6edf3", height=height,
        margin=dict(l=5, r=5, t=24, b=5),
        xaxis=dict(rangeslider_visible=False, showgrid=False, showticklabels=False),
        yaxis=dict(showgrid=False, showticklabels=True, tickfont=dict(size=9))
    )
    return fig


# ── UI 主體 ───────────────────────────────────────────────────────────────────

# Session state 管理股票代號
if "symbol" not in st.session_state:
    st.session_state.symbol = "3661"

# 盤中自動刷新（每 60 秒）
market_open = is_market_open()
if market_open:
    st_autorefresh(interval=60_000, key="rt_refresh")

# ── 側欄：快捷按鈕 ───────────────────────────────────────────────────────────
shortcuts = {
    "台積電 2330": "2330", "鴻海 2317": "2317",
    "緯穎 6669": "6669", "世芯 3661": "3661",
    "聯發科 2454": "2454", "元大台灣50 0050": "0050",
}
with st.sidebar:
    st.markdown("## 📈 台股監測器")
    st.markdown("---")
    st.markdown("**快速切換**")
    for label, code in shortcuts.items():
        if st.button(label, use_container_width=True, key=f"sc_{code}"):
            st.session_state.symbol = code
            st.rerun()
    st.markdown("---")
    st.markdown(
        f'<div style="color:#8b949e;font-size:11px">'
        f'資料來源：Yahoo Finance / TWSE<br>'
        f'延遲約 15 分鐘<br>'
        f'{"📡 盤中自動刷新 60s" if market_open else "💤 盤後模式"}<br>'
        f'{datetime.now().strftime("%Y/%m/%d %H:%M")}</div>',
        unsafe_allow_html=True
    )

# ── 主畫面搜尋列（桌機 + 手機都能用）────────────────────────────────────────
with st.form("main_search", border=False):
    mc1, mc2 = st.columns([5, 1])
    with mc1:
        input_symbol = st.text_input(
            "", value=st.session_state.symbol,
            label_visibility="collapsed",
            placeholder="輸入股票代號，例：2330、6669、0050"
        )
    with mc2:
        main_submitted = st.form_submit_button("🔍 分析", use_container_width=True)
if main_submitted and input_symbol.strip():
    st.session_state.symbol = input_symbol.strip()
    st.rerun()

symbol = st.session_state.symbol

# 載入資料
with st.spinner(f"載入 {symbol}..."):
    df = fetch_daily(symbol)

if df.empty:
    st.error(f"找不到代號 {symbol}")
    st.stop()

df = calc_indicators(df)

with st.spinner(""):
    rt        = fetch_realtime(symbol)
    df_intra  = fetch_intraday(symbol)
    df_60m    = calc_indicators(fetch_60min(symbol))
    df_wk     = calc_indicators(fetch_weekly(symbol))
    inst      = fetch_institutional(symbol)

last  = df.iloc[-1]
prev  = df.iloc[-2]

# 優先用即時報價，盤後用日線收盤
if rt and rt.get("price"):
    rt_price = rt["price"]
    rt_yday  = rt.get("yday") or float(prev["Close"])
    close    = rt_price
    chg      = rt_price - rt_yday
    chg_p    = chg / rt_yday * 100
    vol      = int(rt.get("volume") or last["Volume"])
    rt_open  = rt.get("open", "—")
    rt_high  = rt.get("high", "—")
    rt_low   = rt.get("low", "—")
    rt_bid   = rt.get("bid", "—")
    rt_ask   = rt.get("ask", "—")
    rt_limit_up = rt.get("limit_up", "—")
    rt_limit_dn = rt.get("limit_dn", "—")
    rt_time  = rt.get("time", "")
    rt_name  = rt.get("name", symbol)
    is_rt    = True
else:
    close = float(last["Close"])
    chg   = close - float(prev["Close"])
    chg_p = chg / float(prev["Close"]) * 100
    vol   = int(last["Volume"])
    rt_open = rt_high = rt_low = rt_bid = rt_ask = "—"
    rt_limit_up = rt_limit_dn = "—"
    rt_time = ""; rt_name = symbol; is_rt = False

upper = float(last["BB_upper"])
lower = float(last["BB_lower"])
mid   = float(last["BB_mid"])
k_val = float(last["K"])
d_val = float(last["D"])
dif   = float(last["DIF"])
sig   = float(last["MACD"])
osc   = float(last["OSC"])
ma5, ma20, ma60 = float(last["MA5"]), float(last["MA20"]), float(last["MA60"])
resistance, support, pb_lo, pb_hi = key_levels(df)
score = ai_score_val(df)
sig_text, sig_color, sig_cls = overall_signal(df)
main_sug, sug_cls, stop_sug, up_p, dn_p, flat_p = ai_suggest(df, score)

chg_color  = "#f85149" if chg >= 0 else "#3fb950"
chg_arrow  = "▲" if chg > 0 else ("▼" if chg < 0 else "")
rt_badge   = f'<span style="background:#1f6feb;color:#fff;font-size:10px;padding:1px 6px;border-radius:3px;margin-left:6px">{"即時" if market_open else "收盤"}</span>'

# ── 行情 Header ──────────────────────────────────────────────────────────────
fmt = lambda v: f"{v:,.2f}" if isinstance(v, float) else str(v)
st.markdown(f"""
<div class="card" style="padding:10px 16px;">
  <div style="display:flex; align-items:center; gap:20px; flex-wrap:wrap;">
    <div>
      <span style="font-size:18px;font-weight:700">{rt_name}（{symbol}）</span>
      {rt_badge}
    </div>
    <div>
      <span class="price-big" style="color:{chg_color}">{close:,.2f}</span>
      <span style="color:{chg_color};font-size:15px;margin-left:8px;font-weight:600">
        {chg_arrow} {abs(chg):.2f}（{chg_p:+.2f}%）
      </span>
    </div>
    <div style="display:flex;gap:16px;flex-wrap:wrap;font-size:13px">
      <span style="color:#8b949e">開 <b style="color:#e6edf3">{fmt(rt_open)}</b></span>
      <span style="color:#8b949e">高 <b style="color:#3fb950">{fmt(rt_high)}</b></span>
      <span style="color:#8b949e">低 <b style="color:#f85149">{fmt(rt_low)}</b></span>
      <span style="color:#8b949e">量 <b style="color:#e6edf3">{vol:,} 張</b></span>
      <span style="color:#8b949e">買 <b style="color:#3fb950">{fmt(rt_bid)}</b></span>
      <span style="color:#8b949e">賣 <b style="color:#f85149">{fmt(rt_ask)}</b></span>
      <span style="color:#8b949e">漲停 <b style="color:#f85149">{fmt(rt_limit_up)}</b></span>
      <span style="color:#8b949e">跌停 <b style="color:#3fb950">{fmt(rt_limit_dn)}</b></span>
    </div>
    <div style="margin-left:auto;font-size:11px;color:#8b949e;text-align:right">
      {"📡 盤中自動 60 秒刷新" if market_open else "💤 盤後模式"}<br>
      報價時間 {rt_time or datetime.now().strftime("%H:%M:%S")}
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

# ── 主要區域：左側圖表 + 右側面板 ────────────────────────────────────────────
main_left, main_right = st.columns([62, 38], gap="small")

with main_left:
    st.plotly_chart(main_chart(df), use_container_width=True, config={"displayModeBar": False})

with main_right:
    # 技術分析總覽
    t_txt, t_cls = trend_label(df)
    b_txt, b_cls = bb_label(close, upper, lower)
    m_txt, m_cls = ma_label(df)
    k_txt, k_cls = kd_label(k_val, d_val)
    macd_txt, macd_cls = macd_label(dif, sig, osc)
    v_txt, v_cls = vol_label(df)
    bb_o_txt, bb_o_cls = bb_open_label(df)

    st.markdown(f"""
<div class="card">
  <div class="card-title">🔍 技術分析總覽</div>
  <div class="tech-row"><span class="tech-label">趨勢方向</span>{badge_html(t_txt, t_cls)}</div>
  <div class="tech-row"><span class="tech-label">價格位置</span>{badge_html(b_txt, b_cls)}</div>
  <div class="tech-row"><span class="tech-label">均線排列</span>{badge_html(m_txt, m_cls)}</div>
  <div class="tech-row"><span class="tech-label">KD 訊號</span>{badge_html(k_txt, k_cls)}</div>
  <div class="tech-row"><span class="tech-label">MACD 訊號</span>{badge_html(macd_txt, macd_cls)}</div>
  <div class="tech-row"><span class="tech-label">量能狀況</span>{badge_html(v_txt, v_cls)}</div>
  <div class="tech-row" style="border:none"><span class="tech-label">布林通道</span>{badge_html(bb_o_txt, bb_o_cls)}</div>
</div>
""", unsafe_allow_html=True)

    # 三大法人
    if inst:
        rows_html = ""
        for r in inst[:5]:
            date_str = r.get("日期", "")[-5:]
            fg = r["外資淨"]; ig = r["投信淨"]; sg = r["自營淨"]; tg = r["合計"]
            def fmt(v): return f'<span class="{"pos" if v>0 else "neg"}">{v:+,}</span>'
            rows_html += f"<tr><td>{date_str}</td><td>{fmt(fg)}</td><td>{fmt(ig)}</td><td>{fmt(sg)}</td><td>{fmt(tg)}</td></tr>"
        st.markdown(f"""
<div class="card">
  <div class="card-title">🏦 三大法人（張）</div>
  <table class="inst-table">
    <tr><th>日期</th><th>外資</th><th>投信</th><th>自營</th><th>合計</th></tr>
    {rows_html}
  </table>
</div>
""", unsafe_allow_html=True)
    else:
        st.markdown('<div class="card"><div class="card-title">🏦 三大法人</div><span style="color:#8b949e;font-size:12px">盤中／假日暫無資料</span></div>', unsafe_allow_html=True)

    # 主力燈號 + 短線勝率
    winrate = min(40 + score * 8, 78)
    winrate_color = "#3fb950" if winrate > 65 else ("#d29922" if winrate > 50 else "#f85149")
    st.markdown(f"""
<div class="card">
  <div style="display:flex;gap:12px;align-items:center">
    <div style="flex:1;text-align:center">
      <div class="card-title" style="text-align:center">主力燈號</div>
      <div class="signal-dot {sig_cls}" style="width:50px;height:50px;border-radius:50%;margin:4px auto"></div>
      <div class="signal-label" style="color:{'#3fb950' if sig_color=='green' else '#f85149' if sig_color=='red' else '#d29922'}">{sig_text}</div>
    </div>
    <div style="flex:1;text-align:center">
      <div class="card-title" style="text-align:center">短線勝率</div>
      <div style="font-size:30px;font-weight:700;color:{winrate_color}">{winrate}%</div>
      <div style="font-size:11px;color:#8b949e">規則引擎評估</div>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

# ── 第二列：多週期（分頁）+ 關鍵價位 ────────────────────────────────────────
r2c_chart, r2c4 = st.columns([66, 22], gap="small")

with r2c_chart:
    # 盤中多一個 5分K 分頁
    if market_open and not df_intra.empty:
        tab_5m, tab_60m, tab_day, tab_week = st.tabs(["📊 5分K（盤中）", "⏱ 60分K", "📅 日K", "📆 週K"])
        with tab_5m:
            st.plotly_chart(mini_candle(df_intra, "", 210), use_container_width=True, config={"displayModeBar": False})
            if rt and rt.get("open") and rt.get("price"):
                intra_chg = (rt["price"] - rt["open"]) / rt["open"] * 100
                color = "#3fb950" if intra_chg >= 0 else "#f85149"
                st.markdown(f'<div style="text-align:center;font-size:12px;color:{color}">今日相對開盤 {intra_chg:+.2f}%</div>', unsafe_allow_html=True)
        with tab_60m:
            if not df_60m.empty:
                st.plotly_chart(mini_candle(df_60m.tail(40), "", 210), use_container_width=True, config={"displayModeBar": False})
        with tab_day:
            st.plotly_chart(mini_candle(df.tail(60), "", 210), use_container_width=True, config={"displayModeBar": False})
            st.markdown(f'<div style="text-align:center;font-size:12px;color:#8b949e">{t_txt}</div>', unsafe_allow_html=True)
        with tab_week:
            if not df_wk.empty:
                st.plotly_chart(mini_candle(df_wk.tail(52), "", 210), use_container_width=True, config={"displayModeBar": False})
    else:
        tab_60m, tab_day, tab_week = st.tabs(["⏱ 60分K（短線）", "📅 日K（中線）", "📆 週K（長線）"])
        with tab_60m:
            if not df_60m.empty:
                st.plotly_chart(mini_candle(df_60m.tail(40), "", 210), use_container_width=True, config={"displayModeBar": False})
            if not df_60m.empty and "MA5" in df_60m.columns:
                lm = df_60m.iloc[-1]
                s60 = "高檔震盪" if float(lm["Close"]) > float(lm.get("MA20", lm["Close"])) else "弱勢整理"
                st.markdown(f'<div style="text-align:center;font-size:12px;color:#8b949e">{s60}</div>', unsafe_allow_html=True)
        with tab_day:
            st.plotly_chart(mini_candle(df.tail(60), "", 210), use_container_width=True, config={"displayModeBar": False})
            st.markdown(f'<div style="text-align:center;font-size:12px;color:#8b949e">{t_txt}</div>', unsafe_allow_html=True)
        with tab_week:
            if not df_wk.empty:
                st.plotly_chart(mini_candle(df_wk.tail(52), "", 210), use_container_width=True, config={"displayModeBar": False})
            lw_txt = "主升段後期" if score >= 3 else "波段整理中"
            st.markdown(f'<div style="text-align:center;font-size:12px;color:#8b949e">{lw_txt}</div>', unsafe_allow_html=True)

with r2c4:
    # 關鍵價位
    st.markdown(f"""
<div class="card">
  <div class="card-title">📌 關鍵價位</div>
  <table class="level-table">
    <tr><td class="level-res">🔴 壓力區</td><td class="level-res">{resistance:,.0f}</td></tr>
    <tr><td class="level-pb">🟡 回檔參考</td><td class="level-pb">{pb_lo:,.0f} ～ {pb_hi:,.0f}</td></tr>
    <tr><td class="level-sup">🟢 支撐區</td><td class="level-sup">{support:,.0f}</td></tr>
    <tr><td style="color:#8b949e">跌破防守</td><td style="color:#f85149">跌破 {support:,.0f} 轉弱</td></tr>
    <tr><td style="color:#8b949e">強勢關鍵</td><td style="color:#3fb950">突破 {resistance:,.0f} 才強</td></tr>
  </table>
</div>
""", unsafe_allow_html=True)

# ── 第三列：技術指標數值 + 操作建議 + 明日預測 ───────────────────────────────
r3c1, r3c2, r3c3 = st.columns([28, 34, 26], gap="small")

with r3c1:
    st.markdown(f"""
<div class="card">
  <div class="card-title">📊 技術指標數值</div>
  <table class="inst-table">
    <tr><th style="text-align:left">指標</th><th>數值</th><th>方向</th></tr>
    <tr><td>KD (K/D)</td><td style="text-align:right">{k_val:.1f} / {d_val:.1f}</td><td style="text-align:right">{badge_html(k_txt.split()[0], k_cls)}</td></tr>
    <tr><td>MACD DIF</td><td style="text-align:right">{dif:.2f}</td><td style="text-align:right">{badge_html('多' if dif>0 else '空', 'green' if dif>0 else 'red')}</td></tr>
    <tr><td>MACD OSC</td><td style="text-align:right">{osc:.2f}</td><td style="text-align:right">{badge_html('↑' if osc>0 else '↓', 'green' if osc>0 else 'red')}</td></tr>
    <tr><td>均線排列</td><td style="text-align:right">{ma5:.0f}/{ma20:.0f}</td><td style="text-align:right">{badge_html('多頭' if ma5>ma20 else '空頭', 'green' if ma5>ma20 else 'red')}</td></tr>
    <tr><td>布林上軌</td><td style="text-align:right">{upper:,.0f}</td><td style="text-align:right">—</td></tr>
    <tr><td>布林中軌</td><td style="text-align:right">{mid:,.0f}</td><td style="text-align:right">—</td></tr>
    <tr><td>布林下軌</td><td style="text-align:right">{lower:,.0f}</td><td style="text-align:right">—</td></tr>
  </table>
</div>
""", unsafe_allow_html=True)

with r3c2:
    st.markdown(f"""
<div class="card">
  <div class="card-title">💡 操作建議</div>
  <div class="suggest-box {sug_cls}">{main_sug}</div>
  <div class="suggest-box neutral">{stop_sug}</div>
  <div class="suggest-box neutral">📊 回檔買點：{pb_lo:,.0f} ～ {pb_hi:,.0f}，停損跌破 {support:,.0f}</div>
  <div style="font-size:11px;color:#8b949e;margin-top:6px">
  ⚠️ 以上為規則引擎分析，不構成投資建議，操作前請自行判斷風險。
  </div>
</div>
""", unsafe_allow_html=True)

with r3c3:
    up_bar_w  = int(up_p * 1.5)
    dn_bar_w  = int(dn_p * 1.5)
    flat_bar_w = int(flat_p * 1.5)
    st.markdown(f"""
<div class="card">
  <div class="card-title">🔮 明日漲跌機率預測（AI 模型）</div>
  <div style="display:flex;justify-content:space-around;margin:8px 0">
    <div class="prob-box">
      <div class="prob-num" style="color:#3fb950">{up_p}%</div>
      <div class="prob-label">上漲機率</div>
    </div>
    <div class="prob-box">
      <div class="prob-num" style="color:#f85149">{dn_p}%</div>
      <div class="prob-label">下跌機率</div>
    </div>
    <div class="prob-box">
      <div class="prob-num" style="color:#d29922">{flat_p}%</div>
      <div class="prob-label">震盪機率</div>
    </div>
  </div>
  <div style="margin-top:6px">
    <div style="font-size:11px;color:#8b949e;margin-bottom:2px">上漲</div>
    <div style="background:#21262d;border-radius:3px;height:6px"><div style="background:#3fb950;width:{up_bar_w}px;height:6px;border-radius:3px"></div></div>
    <div style="font-size:11px;color:#8b949e;margin-bottom:2px;margin-top:4px">下跌</div>
    <div style="background:#21262d;border-radius:3px;height:6px"><div style="background:#f85149;width:{dn_bar_w}px;height:6px;border-radius:3px"></div></div>
    <div style="font-size:11px;color:#8b949e;margin-bottom:2px;margin-top:4px">震盪</div>
    <div style="background:#21262d;border-radius:3px;height:6px"><div style="background:#d29922;width:{flat_bar_w}px;height:6px;border-radius:3px"></div></div>
  </div>
  <div style="font-size:10px;color:#555e6a;margin-top:8px">
    預測結論：{'上漲機率較高，可留意做多機會' if up_p > dn_p else '下跌機率較高，建議謹慎操作'}
  </div>
</div>
""", unsafe_allow_html=True)
