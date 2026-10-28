"""
🎯 共振回踩分頁：讀 GitHub Actions 寫回 repo 的觀察名單與警示紀錄
點表格裡的股票 → 切回個股分析
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

from strategies.resonance_intraday import reference_prices

DATA = Path(__file__).parent / "data"
TAIPEI = timezone(timedelta(hours=8))
SOURCE_URL = "https://www.binance.com/zh-TC/square/post/297030038923969"


def _load(name: str) -> dict:
    f = DATA / name
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_list(name: str) -> list:
    f = DATA / name
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
    except Exception:
        return []


def _goto(code: str):
    """切到個股分析（page 是 radio 的 key，畫完之後不能直接改，交給下一輪處理）"""
    st.session_state.symbol = code
    st.session_state.pop("matched_name", None)
    st.session_state["_goto_page"] = "stock"
    st.rerun()


def _fmt_bar(t: int) -> str:
    return f"{t // 10000:02d}:{t // 100 % 100:02d}"


def render():
    wl = _load("resonance_watchlist.json")
    alerts_all = _load("resonance_alerts.json")
    today = datetime.now(TAIPEI).date().isoformat()
    asof = wl.get("asof", "")
    stocks = wl.get("stocks", [])
    today_alerts = alerts_all.get(asof, [])

    st.markdown("### 🎯 共振回踩作多雷達")
    st.caption("日線 EMA 9/21/50 多頭＋週 EMA9 向上＋放量突破後整理 → 盤中 15 分 K "
               "跌破支撐（EMA9／EMA21／AVWAP）後快速收回，突破反轉 K 高點進場｜每個交易日 09:00～10:30")

    if not wl:
        st.info("還沒有資料。GitHub Actions 每個交易日 08:20 執行後就會出現。")
        return
    if asof != today:
        st.warning(f"這是 {asof} 的名單（今天還沒更新，或今天休市）")

    n_ok = sum(s.get("zone_ok") for s in stocks)
    c1, c2, c3 = st.columns(3)
    c1.metric("觀察名單", f"{len(stocks)} 檔")
    c2.metric("🎯 支撐區 ≤3%", f"{n_ok} 檔")
    c3.metric("今日警示", f"{len(today_alerts)} 次")

    # ── 今日警示 ──
    for a in today_alerts:
        st.markdown(f"""
<div class="card" style="border-left:4px solid #f85149">
  <div class="card-title">🔔 {_fmt_bar(a['bar_t'])} 的 15 分 K｜{a['code']} {a['name']}（{a.get('time', '')} 觸發）</div>
  進場 <b>{a['price']:g}</b>（突破 {a['breakout']:g}）｜停損 {a['stop']:g}（-{a['risk_pct']}%）｜每張最多賠 {(a['price'] - a['stop']) * 1000:,.0f} 元<br>
  目標 3R <b>{a['target_3r']:g}</b>（賣三分之一）｜5R <b>{a['target_5r']:g}</b>（剩下 2/3 跌破日 EMA9 再出場）｜EMA9 <b>{a.get('ema_d9', 0):.2f}</b> 元
</div>""", unsafe_allow_html=True)

    # ── 觀察名單 ──
    st.markdown(f"**📋 {asof} 觀察名單**　點一列看個股 K 線")
    st.caption("警戒價＝跌破才開始注意｜進場區間＝突破價落在這裡才會發警示（高於 AVWAP／均線，且停損 ≤3%）｜"
               "移動停損＝原文出場：3R 先出 1/3，其餘跌破日 EMA9 出場｜盤中 AVWAP 會小幅移動，以上為參考值")
    if not stocks:
        st.write("今天沒有符合盤前條件的股票。")
    else:
        rows = []
        for s in sorted(stocks, key=lambda x: (not reference_prices(x)["possible"], x["zone_width_pct"])):
            r = reference_prices(s)
            rows.append({
                "": "🎯" if s.get("zone_ok") else "",
                "代號": s["code"], "名稱": s["name"], "昨收": round(s["last_close"], 2),
                "警戒價": r["warn"],
                "進場區間": f"{r['min_entry']:g}～{r['max_entry']:g}" if r["possible"] else "不會觸發",
                "移動停損 EMA9": r["trail_ema9"],
                "支撐區": f"{s['zone_lo']:.2f}～{s['zone_hi']:.2f}",
                "區寬%": s["zone_width_pct"],
                "距下緣%": round((s["last_close"] / s["zone_lo"] - 1) * 100, 1),
                "錨點日": s["anchor_date"],
            })
        df = pd.DataFrame(rows)
        ev = st.dataframe(df, hide_index=True, use_container_width=True,
                          on_select="rerun", selection_mode="single-row", key="rp_watch")
        if ev.selection.rows:
            _goto(df.iloc[ev.selection.rows[0]]["代號"])

    # ── 持倉追蹤（收盤後更新）──
    positions = {p["id"]: p for p in (_load_list("resonance_positions.json"))}
    holding = [p for p in positions.values() if p["status"] == "open"]
    if holding:
        st.markdown("**📒 持倉追蹤**　每天收盤後更新｜原文出場：3R 先出 1/3，其餘收盤跌破日 EMA9 出場")
        st.dataframe(pd.DataFrame([{
            "代號": p["code"], "名稱": p["name"], "進場日": p["entry_day"], "進場": p["entry"],
            "最新收盤": p.get("last_close", p["entry"]),
            "帳面R": round((p.get("last_close", p["entry"]) - p["entry"]) / p["r"], 2),
            "停損": p["stop"], "3R": p["target_3r"], "已出 1/3": "✅" if p["partial"] else "",
            "EMA9 移動停損": round(p["ema9"], 2) if p.get("ema9") else None,
        } for p in holding]), hide_index=True, use_container_width=True)

    # ── 歷史警示 ──
    def _result(day, a):
        p = positions.get(f"{day}_{a['code']}_{a.get('time', a['bar_t'])}")
        if not p:
            return "待追蹤"
        return f"{p['result_r']:+.2f}R" + ("（持倉中）" if p["status"] == "open" else "")
    hist = [{"日期": d, "時間": a.get("time", _fmt_bar(a["bar_t"])), "代號": a["code"], "名稱": a["name"],
             "進場": a["price"], "停損": a["stop"], "停損%": a["risk_pct"],
             "3R": a["target_3r"], "結果": _result(d, a)}
            for d, lst in sorted(alerts_all.items(), reverse=True) for a in lst]
    st.markdown("**🗂 歷史警示**")
    if hist:
        hdf = pd.DataFrame(hist)
        ev2 = st.dataframe(hdf, hide_index=True, use_container_width=True,
                           on_select="rerun", selection_mode="single-row", key="rp_hist")
        if ev2.selection.rows:
            _goto(hdf.iloc[ev2.selection.rows[0]]["代號"])
    else:
        st.caption("還沒有警示紀錄。回測 2026/5～10 月全市場只響 2 次，屬於很少出手的策略。")

    with st.expander("ℹ️ 策略說明與回測"):
        st.markdown(f"""
- **來源**：Martin Luk（2025 美國投資冠軍賽 $2 萬～100 萬股票組冠軍，+969.8%）的共振回調策略，[原文]({SOURCE_URL})
- **盤前**：日 EMA9＞21＞50、週 EMA9 向上、近 20 日均量 ≥1,000 張、整理前 20 日漲 ≥15%、近 5 日振幅 ≤10%、60 日內放量突破當錨點算 AVWAP
- **盤中**：支撐區（EMA9／EMA21／AVWAP）寬 ≤3% → 15 分 K 低點跌破下緣 → 2 根內強紅 K 收回下緣 → 2 根內突破反轉 K 高點；停損＝今日低點且 ≤3%
- **原文出場**：3～5R 先出 1/3，其餘跌破日 EMA9 出場
- **回測（2026/5/1～10/27）**：2 次警示。智易當天停損 -1R；永豐金照原文抱到 10/22 碰 3R 先出 1/3，剩餘仍持有（帳面約 +3R）。樣本太少，上線累積中
- 名單更新時間：{wl.get('generated', '-')}
""")
