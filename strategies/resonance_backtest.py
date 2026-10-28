#!/usr/bin/env python3
"""
共振回踩作多 — 歷史回放

每個交易日：用當天以前的日 K 做盤前篩選 → 用永豐 1 分 K 模擬逐筆，餵給盤中引擎。
1 分 K 內部的走法假設：紅 K 開→低→高→收、黑 K 開→高→低→收。
突破價取「反轉高點 + 1 檔」（開盤跳空則取開盤價），比較接近 XQ 逐筆洗價第一筆突破的成交價。

用法：
  python -m strategies.resonance_backtest --start 2026-08-01 --end 2026-10-27
"""
import argparse, json, pickle, sys, time, warnings
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sj_client
from strategies import resonance_pullback as pre
from strategies.resonance_intraday import Engine, IntradayParams

warnings.filterwarnings("ignore", message=".*inplace keyword.*")

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache"
OUT_FILE = ROOT / "data" / "resonance_backtest.json"


def tick_size(px: float) -> float:
    for limit, tick in ((10, 0.01), (50, 0.05), (100, 0.1), (500, 0.5), (1000, 1)):
        if px < limit:
            return tick
    return 5


def load_daily(universe: dict, start: date, end: date) -> dict:
    f = CACHE / f"daily_{start}_{end}.pkl"
    if f.exists():
        return pickle.loads(f.read_bytes())
    data = pre.fetch_daily([u["yf"] for u in universe.values()], start, end)
    f.write_bytes(pickle.dumps(data))
    return data


def load_kbars(api, code: str, start: date, end: date) -> pd.DataFrame:
    f = CACHE / f"kbars_{code}_{start}_{end}.pkl"
    if f.exists():
        return pd.read_pickle(f)
    df = sj_client.minute_kbars(api, code, start.isoformat(), end.isoformat())
    df.to_pickle(f)
    return df


def ticks_of_day(day_df: pd.DataFrame, bar_min: int = 5):
    """1 分 K → 逐筆序列：(5 分 K 開始時間 HHMMSS, 價格, 這根 5 分 K 累計張數, 累計金額, 是否為該分鐘開盤價)"""
    start5 = (day_df["ts"] - pd.Timedelta(minutes=1)).dt.floor(f"{bar_min}min")
    cum_v = cum_a = 0.0
    prev5 = None
    for (_, r), s5 in zip(day_df.iterrows(), start5):
        if s5 != prev5:
            cum_v = cum_a = 0.0
            prev5 = s5
        cum_v += r["Volume"]
        cum_a += r["Amount"]
        t = s5.hour * 10000 + s5.minute * 100
        path = (r["Open"], r["Low"], r["High"], r["Close"]) if r["Close"] >= r["Open"] \
            else (r["Open"], r["High"], r["Low"], r["Close"])
        for i, px in enumerate(path):
            yield t, float(px), cum_v, cum_a, i == 0


def simulate_day(setup: dict, prev_close: float, day_df: pd.DataFrame, ip: IntradayParams, bar_min: int = 5):
    eng = Engine(setup, prev_close, ip)
    ticks = list(ticks_of_day(day_df, bar_min))
    trades = []
    for k, (t, px, v, a, is_open) in enumerate(ticks):
        if eng.phase == 2 and px > eng.reversal_high and not is_open:
            px = min(px, eng.reversal_high + tick_size(eng.reversal_high))
        alert = eng.on_tick(t, px, v, a)
        if alert:
            alert.update(outcome(alert, ticks[k + 1:]))
            trades.append(alert)
    return trades, eng.stats


def outcome(alert: dict, rest: list) -> dict:
    """停損優先（保守）；當天沒碰到就以收盤價結算"""
    entry, stop = alert["price"], alert["stop"]
    r = entry - stop
    best = entry
    for t, px, *_ in rest:
        best = max(best, px)
        if px <= stop:
            return {"exit": "停損", "exit_t": t, "r": -1.0, "mfe_r": round((best - entry) / r, 2)}
        if px >= alert["target_5r"]:
            return {"exit": "5R", "exit_t": t, "r": 5.0, "mfe_r": round((best - entry) / r, 2)}
    last = rest[-1][1] if rest else entry
    hit3 = best >= alert["target_3r"]
    return {"exit": "收盤" + ("（曾達 3R）" if hit3 else ""), "exit_t": 133000,
            "r": round((last - entry) / r, 2), "mfe_r": round((best - entry) / r, 2)}


def main():
    ap = argparse.ArgumentParser(description="共振回踩作多：歷史回放")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--bar", type=int, default=5, choices=(5, 15), help="K 棒分鐘數")
    ap.add_argument("--sweep-from", default="hi", choices=("hi", "lo"))
    ap.add_argument("--recover-to", default="hi", choices=("hi", "lo"))
    ap.add_argument("--out", default=str(OUT_FILE))
    a = ap.parse_args()
    start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
    CACHE.mkdir(parents=True, exist_ok=True)
    p, ip = pre.P, IntradayParams(SweepFrom=a.sweep_from, RecoverTo=a.recover_to)
    print(f"K 棒 {a.bar} 分｜掃低基準 {a.sweep_from}｜收復基準 {a.recover_to}")
    t0 = time.time()

    api = sj_client.login()
    try:
        universe = sj_client.stock_universe(api)
        daily = load_daily(universe, start - timedelta(days=365 * 3 + 30), end)
        print(f"日 K {len(daily)} 檔（{time.time() - t0:.0f}s）")
        days = [d.date() for d in daily["2330.TW"].index if start <= d.date() <= end]

        # 盤前篩選：每個交易日各跑一次
        setups = []
        for T in days:
            n1 = 0
            for code, u in universe.items():
                d = daily.get(u["yf"])
                if d is None:
                    continue
                d = d[d.index.date < T]
                reason, info = pre.check_stage1(d.tail(150), p)
                if reason:
                    continue
                n1 += 1
                reason, tinfo = pre.check_trend(d, T, p)
                if reason is None:
                    setups.append({"day": T, "code": code, "name": u["name"], **info, **tinfo})
            print(f"  {T} 第 1 輪 {n1} 檔 → 入選 {sum(s['day'] == T for s in setups)} 檔")
        print(f"盤前篩選合計 {len(setups)} 檔次（{time.time() - t0:.0f}s）")

        # 盤中回放
        trades, funnel = [], {"setup": len(setups), "zone_ok": 0, "sweep": 0, "reversal": 0,
                              "breakout_rejected": 0, "alert": 0, "no_data": 0}
        by_code, diag = {}, []
        for s in setups:
            by_code.setdefault(s["code"], []).append(s)
        for i, (code, ss) in enumerate(by_code.items(), 1):
            k_start = min(date.fromisoformat(s["anchor_date"]) for s in ss)
            k_end = max(s["day"] for s in ss)
            kb = load_kbars(api, code, k_start, k_end)
            if kb.empty:
                funnel["no_data"] += len(ss)
                continue
            kb_day = kb["ts"].dt.date
            for s in ss:
                T, anchor = s["day"], date.fromisoformat(s["anchor_date"])
                past = kb[(kb_day >= anchor) & (kb_day < T)]
                today = kb[kb_day == T]
                if past.empty or today.empty:
                    funnel["no_data"] += 1
                    continue
                s["past_amount"], s["past_volume"] = float(past["Amount"].sum()), float(past["Volume"].sum())
                avwap = s["past_amount"] / (s["past_volume"] * 1000)
                s["zone_lo"] = min(s["ema_d9"], s["ema_d21"], avwap)
                s["zone_hi"] = max(s["ema_d9"], s["ema_d21"], avwap)
                prev_close = float(past["Close"].iloc[-1])
                if (s["zone_hi"] / s["zone_lo"] - 1) * 100 <= ip.MaxZonePct:
                    funnel["zone_ok"] += 1
                    win = today[(today["ts"].dt.time > pd.Timestamp("09:00").time())
                                & (today["ts"].dt.time <= pd.Timestamp("10:30").time())]
                    diag.append({"day": T.isoformat(), "code": code, "name": s["name"],
                                 "width": round((s["zone_hi"] / s["zone_lo"] - 1) * 100, 2),
                                 "prev_vs_hi": round((prev_close / s["zone_hi"] - 1) * 100, 2),
                                 "low_vs_lo": round((win["Low"].min() / s["zone_lo"] - 1) * 100, 2),
                                 "low_vs_hi": round((win["Low"].min() / s["zone_hi"] - 1) * 100, 2)})
                tr, st = simulate_day(s, prev_close, today, ip, a.bar)
                for k in ("sweep", "reversal", "breakout_rejected", "alert"):
                    funnel[k] += st[k]
                for t in tr:
                    t["day"] = T.isoformat()
                    trades.append(t)
            if i % 20 == 0:
                print(f"  分 K 回放 {i}/{len(by_code)} 檔（{time.time() - t0:.0f}s）")
        print(f"用量：{api.usage()}")
    finally:
        api.logout()

    print("\n===== 漏斗 =====")
    for k, label in (("setup", "盤前入選（檔次）"), ("zone_ok", "開盤支撐區 ≤3%"), ("sweep", "掃低"),
                     ("reversal", "收復（強紅 K）"), ("breakout_rejected", "突破但被濾掉"), ("alert", "發出警示")):
        print(f"  {label:<14} {funnel[k]}")
    print("\n===== 警示 =====")
    for t in trades:
        print(f"{t['day']} {t['bar_t']:06d} {t['code']} {t['name']:<6} 進 {t['price']:.2f} 停損 {t['stop']:.2f}"
              f"（{t['risk_pct']}%）→ {t['exit']} {t['r']:+.2f}R  最大 {t['mfe_r']:+.2f}R")
    if trades:
        rs = [t["r"] for t in trades]
        print(f"\n{len(rs)} 筆｜勝率 {sum(r > 0 for r in rs) / len(rs):.0%}｜平均 {sum(rs) / len(rs):+.2f}R｜合計 {sum(rs):+.2f}R")
    Path(a.out).write_text(json.dumps({"start": a.start, "end": a.end, "funnel": funnel, "trades": trades, "zone_ok_days": diag},
                                   ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"已存 {a.out}")


if __name__ == "__main__":
    main()
