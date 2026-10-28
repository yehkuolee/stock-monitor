#!/usr/bin/env python3
"""
共振回踩作多（移植自 XQ 策略雷達 XS 腳本）— 盤前篩選

對應 XS 腳本「二、每日初始化」＋「三、自動尋找 AVWAP 錨點」：
只用「已完成」的日 K／週 K（asof 當天以前），算出今天可以盯盤的觀察名單與支撐區。
XS 的 [Idx] 寫法 = 往前第 Idx 根完成 K，這裡用 bar(series, idx) 對應。

用法：
  python -m strategies.resonance_pullback                 # 今天、全市場
  python -m strategies.resonance_pullback --date 2026-10-20
  python -m strategies.resonance_pullback --codes 2330,3017 -v   # 指定股票、印出每關結果
"""
import argparse, json, sys, time, warnings
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sj_client

warnings.filterwarnings("ignore", message=".*inplace keyword.*")

TAIPEI = timezone(timedelta(hours=8))
OUT_FILE = Path(__file__).resolve().parent.parent / "data" / "resonance_watchlist.json"


# ── 參數（對應 XS Input，預設值照抄）────────────────────────────────
@dataclass
class Params:
    DSeed: int = 250            # 日 EMA 起算歷史根數
    WSeed: int = 100            # 週 EMA 起算歷史根數
    AnchorWindow: int = 60      # 錨點回看日數
    BreakDays: int = 20         # 錨點突破前幾日高點
    VolDays: int = 20           # 均量觀察日數
    AnchorVolMult: float = 1.5  # 錨點量須達前均量倍數
    AnchorRisePct: float = 3    # 錨點單日最低漲幅%
    RunupDays: int = 20         # 整理前漲幅觀察日數
    MinRunupPct: float = 15     # 整理前最低漲幅%
    BaseDays: int = 5           # 最近整理日數
    MaxBasePct: float = 10      # 整理區間最大振幅%
    MinAvgVol: float = 1000     # 最低日均量（張）
    MaxZonePct: float = 3       # EMA9/21/AVWAP 區寬上限%（盤中才檢查，這裡只標記）


P = Params()


def bar(s: pd.Series, idx: int) -> float:
    """XS 的 s[idx]：往前第 idx 根完成 K；資料不夠回傳 0（同 GetField Default:=0）"""
    return float(s.iloc[-idx]) if 0 < idx <= len(s) else 0.0


def ema_from_seed(closes: pd.Series, seed: int, period: int, stop_before_last: bool = False) -> float:
    """XS 起算法：以第 seed 根收盤為初值，一路遞推到 [1]（不是 SMA 起算）"""
    e = bar(closes, seed)
    last = 2 if stop_before_last else 1
    for i in range(seed - 1, last - 1, -1):
        e += 2.0 / (period + 1) * (bar(closes, i) - e)
    return e


# ── 日 K 資料 ─────────────────────────────────────────────────────────
def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """yfinance 休市日會塞成交量 0 的假 K 棒，要拿掉；成交量換算成張"""
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    df = df[df["Volume"] > 0].copy()
    df["Volume"] = df["Volume"] / 1000
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    return df


def fetch_daily(tickers: list[str], start: date, end: date, batch: int = 300) -> dict[str, pd.DataFrame]:
    out = {}
    for i in range(0, len(tickers), batch):
        chunk = tickers[i:i + batch]
        raw = yf.download(chunk, start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
                          auto_adjust=False, group_by="ticker", progress=False, threads=True)
        for t in chunk:
            try:
                df = _clean(raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw)
            except KeyError:
                continue
            if len(df):
                out[t] = df
        time.sleep(1)
    return out


# ── 各關檢查（回傳 None = 通過；字串 = 被刷掉的原因）──────────────────
def check_volume_base_runup(d: pd.DataFrame, p: Params = P) -> tuple[str | None, dict]:
    vol, hi, lo, cl = d["Volume"], d["High"], d["Low"], d["Close"]
    need = max(p.AnchorWindow + max(p.BreakDays, p.VolDays) + 1, p.RunupDays + p.BaseDays + 1)
    if len(d) < need:
        return "歷史資料不足", {}

    avg_vol = sum(bar(vol, i) for i in range(1, p.VolDays + 1)) / p.VolDays
    base_high = max(bar(hi, i) for i in range(1, p.BaseDays + 1))
    base_low = min(bar(lo, i) for i in range(1, p.BaseDays + 1))
    base_pct = (base_high / base_low - 1) * 100 if base_low > 0 else 999
    px = bar(cl, p.BaseDays + p.RunupDays + 1)
    prior_gain = (bar(cl, p.BaseDays + 1) / px - 1) * 100 if px > 0 else -999
    info = {"avg_vol": round(avg_vol), "base_pct": round(base_pct, 2), "prior_gain": round(prior_gain, 2)}

    if avg_vol < p.MinAvgVol:
        return f"均量 {avg_vol:.0f} 張 < {p.MinAvgVol:.0f}", info
    if base_pct > p.MaxBasePct:
        return f"整理振幅 {base_pct:.1f}% > {p.MaxBasePct}%", info
    if prior_gain < p.MinRunupPct:
        return f"整理前漲幅 {prior_gain:.1f}% < {p.MinRunupPct}%", info
    return None, info


def find_anchor(d: pd.DataFrame, p: Params = P) -> int:
    """從昨日往前找最近一次放量突破，回傳 AnchorAgo（找不到 -1）"""
    vol, hi, cl = d["Volume"], d["High"], d["Close"]
    for idx in range(1, p.AnchorWindow + 1):
        prior_high = max(bar(hi, idx + j) for j in range(1, p.BreakDays + 1))
        ev_avg = sum(bar(vol, idx + j) for j in range(1, p.VolDays + 1)) / p.VolDays
        prev_close = bar(cl, idx + 1)
        if prior_high <= 0 or ev_avg <= 0 or prev_close <= 0:
            continue
        if (bar(cl, idx) > prior_high
                and bar(cl, idx) >= prev_close * (1 + p.AnchorRisePct / 100)
                and bar(vol, idx) >= ev_avg * p.AnchorVolMult):
            return idx
    return -1


def check_stage1(d: pd.DataFrame, p: Params = P) -> tuple[str | None, dict]:
    """均量、整理、前期漲幅、錨點（只需近半年日 K）"""
    reason, info = check_volume_base_runup(d, p)
    if reason is None:
        ago = find_anchor(d, p)
        info["anchor_ago"] = ago
        if ago <= p.BaseDays:
            reason = "近 60 日無錨點" if ago < 0 else f"錨點在整理期內（{ago} 日前）"
        else:
            info["anchor_date"] = d.index[-ago].date().isoformat()
    return reason, info


def check_trend(d: pd.DataFrame, asof: date, p: Params = P) -> tuple[str | None, dict]:
    cl = d["Close"]
    if len(cl) < p.DSeed:
        return "日 K 不足 DSeed", {}
    ema_d9, ema_d21, ema_d50 = (ema_from_seed(cl, p.DSeed, n) for n in (9, 21, 50))

    # 週 K：只留已完成的週（本週還沒收完，不算）
    wk = cl.resample("W-FRI").last().dropna()
    wk = wk[wk.index.date < asof] if len(wk) and wk.index[-1].date() >= asof else wk
    if len(wk) < p.WSeed:
        return "週 K 不足 WSeed", {}
    ema_w9, ema_w21 = (ema_from_seed(wk, p.WSeed, n) for n in (9, 21))
    ema_w9_prev = ema_from_seed(wk, p.WSeed, 9, stop_before_last=True)

    info = {k: round(v, 4) for k, v in dict(ema_d9=ema_d9, ema_d21=ema_d21, ema_d50=ema_d50,
                                             ema_w9=ema_w9, ema_w21=ema_w21, ema_w9_prev=ema_w9_prev).items()}
    if not (ema_d9 > ema_d21 > ema_d50):
        return "日 EMA 非多頭排列", info
    if not ema_w9 > ema_w9_prev:
        return "週 EMA9 未上升", info
    return None, info


def anchor_vwap(api, code: str, anchor_day: date, last_day: date, expect_days: int) -> tuple[float, float, int]:
    """錨點當天到昨日的實際成交金額、成交張數（永豐 1 分 K 加總）"""
    df = sj_client.minute_kbars(api, code, anchor_day.isoformat(), last_day.isoformat())
    if df.empty:
        return 0, 0, 0
    df = df[(df["ts"].dt.date >= anchor_day) & (df["ts"].dt.date <= last_day)]
    return float(df["Amount"].sum()), float(df["Volume"].sum()), df["ts"].dt.date.nunique()


# ── 主流程 ────────────────────────────────────────────────────────────
def run(asof: date, codes: list[str] | None = None, verbose: bool = False, p: Params = P) -> list[dict]:
    t0 = time.time()
    api = sj_client.login()
    try:
        universe = sj_client.stock_universe(api)
        if codes:
            universe = {c: universe[c] for c in codes if c in universe}
        print(f"[{asof}] 股票池 {len(universe)} 檔")

        # 第 1 輪：近 6 個月日 K → 均量、整理、前期漲幅、錨點（便宜的先刷）
        short = fetch_daily([u["yf"] for u in universe.values()], asof - timedelta(days=190), asof)
        stage1 = {}
        for code, u in universe.items():
            d = short.get(u["yf"])
            if d is None:
                continue
            reason, info = check_stage1(d[d.index.date < asof], p)
            if verbose:
                print(f"  {code} {u['name']}: {reason or '第 1 輪通過'} {info}")
            if reason is None:
                stage1[code] = info
        print(f"第 1 輪通過 {len(stage1)} 檔（{time.time() - t0:.0f}s）")

        # 第 2 輪：3 年日 K → 日 EMA 多頭、週 EMA9 上升
        long = fetch_daily([universe[c]["yf"] for c in stage1], asof - timedelta(days=365 * 3), asof)
        results = []
        for code, info in stage1.items():
            u = universe[code]
            d = long.get(u["yf"])
            if d is None:
                continue
            d = d[d.index.date < asof]
            reason, tinfo = check_trend(d, asof, p)
            info.update(tinfo)
            if reason is None:
                # 第 3 輪：AVWAP（永豐分 K 的真實成交金額）
                anchor_day = date.fromisoformat(info["anchor_date"])
                amt, vol, ndays = anchor_vwap(api, code, anchor_day, d.index[-1].date(), info["anchor_ago"])
                if vol <= 0 or amt <= 0:
                    reason = "抓不到錨點以來成交金額"
                else:
                    if ndays != info["anchor_ago"]:
                        print(f"  ⚠️ {code} 分 K 天數 {ndays} ≠ 日 K 天數 {info['anchor_ago']}")
                    avwap = amt / (vol * 1000)
                    zlo = min(info["ema_d9"], info["ema_d21"], avwap)
                    zhi = max(info["ema_d9"], info["ema_d21"], avwap)
                    info.update(past_amount=amt, past_volume=vol, avwap0=round(avwap, 4),
                                zone_lo=round(zlo, 4), zone_hi=round(zhi, 4),
                                zone_width_pct=round((zhi / zlo - 1) * 100, 2),
                                last_close=float(d["Close"].iloc[-1]))
                    info["zone_ok"] = info["zone_width_pct"] <= p.MaxZonePct
            if verbose:
                print(f"  {code} {u['name']}: {reason or '✅ 入選'}")
            if reason is None:
                results.append({"code": code, "name": u["name"], "market": u["market"], **info})
        print(f"入選 {len(results)} 檔（{time.time() - t0:.0f}s）")
        return results
    finally:
        api.logout()


def save(asof: date, res: list[dict]):
    OUT_FILE.write_text(json.dumps({"asof": asof.isoformat(), "params": asdict(P),
                                    "generated": datetime.now(TAIPEI).isoformat(timespec="seconds"),
                                    "stocks": res}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已存 {OUT_FILE}")


def main():
    ap = argparse.ArgumentParser(description="共振回踩作多：盤前篩選")
    ap.add_argument("--date", help="評估日 YYYY-MM-DD（預設今天，只用這天以前的完成 K）")
    ap.add_argument("--codes", help="只看指定股票，逗號分隔")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--no-save", action="store_true")
    a = ap.parse_args()

    asof = date.fromisoformat(a.date) if a.date else datetime.now(TAIPEI).date()
    res = run(asof, a.codes.split(",") if a.codes else None, a.verbose)
    for r in res:
        flag = "✅" if r["zone_ok"] else "區寬過大"
        print(f"{r['code']} {r['name']:<6} 收 {r['last_close']:>8.2f}  支撐區 {r['zone_lo']:.2f}~{r['zone_hi']:.2f}"
              f"（寬 {r['zone_width_pct']}% {flag}）錨點 {r['anchor_date']}  AVWAP {r['avwap0']:.2f}")
    if not a.no_save:
        save(asof, res)


if __name__ == "__main__":
    main()
