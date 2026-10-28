#!/usr/bin/env python3
"""
共振回踩作多 — 收盤後出場追蹤（GitHub Actions 每天 13:45 跑一次，約 3 分鐘）

警示觸發後當成一筆部位，照原文出場：
  1. 碰到停損（今日低點）→ 全部出場，-1R
  2. 碰到 3R → 先出 1/3（+1R）
  3. 出過 1/3 之後，收盤跌破日 EMA9 → 剩下 2/3 出場
用永豐 1 分 K 逐分鐘檢查（同一分鐘碰到停損和 3R，保守算停損先到）。

用法：
  python -m strategies.resonance_tracker              # 正式
  python -m strategies.resonance_tracker --no-discord
"""
import argparse, json, sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sj_client
from strategies.resonance_live import Discord, publish

TAIPEI = timezone(timedelta(hours=8))
DATA = Path(__file__).resolve().parent.parent / "data"
ALERT_FILE = DATA / "resonance_alerts.json"
POS_FILE = DATA / "resonance_positions.json"
WATCH_FILE = DATA / "resonance_watchlist.json"


def load(f: Path, default):
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else default


def sync_new_alerts(positions: list[dict]) -> list[dict]:
    """把還沒追蹤的警示變成部位；EMA9 起始值用當天觀察名單的日 EMA9（算到前一日）"""
    known = {p["id"] for p in positions}
    alerts = load(ALERT_FILE, {})
    watch = load(WATCH_FILE, {})
    ema9_by_code = {s["code"]: s["ema_d9"] for s in watch.get("stocks", [])} if watch else {}
    new = []
    for day, lst in alerts.items():
        for a in lst:
            pid = f"{day}_{a['code']}_{a.get('time', a['bar_t'])}"
            if pid in known:
                continue
            p = {"id": pid, "code": a["code"], "name": a["name"], "entry_day": day,
                 "entry_time": a.get("time", ""), "entry": a["price"], "stop": a["stop"],
                 "target_3r": a["target_3r"], "r": round(a["price"] - a["stop"], 4),
                 "ema9": a.get("ema_d9") or (ema9_by_code.get(a["code"]) if watch.get("asof") == day else None),
                 "status": "open", "partial": False, "result_r": 0.0, "checked": None, "events": []}
            positions.append(p)
            new.append(p)
    return new


def path(r) -> tuple:
    return (r.Open, r.Low, r.High, r.Close) if r.Close >= r.Open else (r.Open, r.High, r.Low, r.Close)


def track(api, p: dict, today: date) -> list[str]:
    """更新一筆部位到今天為止，回傳要推播的訊息"""
    msgs = []
    start = date.fromisoformat(p["checked"]) + timedelta(days=1) if p["checked"] else date.fromisoformat(p["entry_day"])
    if start > today:
        return msgs
    kb = sj_client.minute_kbars(api, p["code"], start.isoformat(), today.isoformat())
    if kb.empty:
        p["checked"] = today.isoformat()
        return msgs
    label = f"{p['code']} {p['name']}"
    for d, day_df in kb.groupby(kb["ts"].dt.date):
        if d.isoformat() == p["entry_day"] and p["entry_time"]:
            # 進場當天只看觸發之後（1 分 K 的 ts 是結束時間）
            t = datetime.strptime(f"{d} {p['entry_time']}", "%Y-%m-%d %H:%M:%S")
            day_df = day_df[day_df["ts"] > t]
        for r in day_df.itertuples():
            for px in path(r):
                if px <= p["stop"]:
                    part = 2 / 3 if p["partial"] else 1
                    p["result_r"] = round(p["result_r"] + part * (p["stop"] - p["entry"]) / p["r"], 2)
                    p["status"] = "closed"
                    p["events"].append({"date": d.isoformat(), "time": f"{r.ts:%H:%M}", "type": "停損", "price": p["stop"]})
                    msgs.append(f"🛑 **{label}** {d:%m/%d} {r.ts:%H:%M} 碰到停損 {p['stop']:g}，"
                                f"{'剩餘 2/3' if p['partial'] else '全部'}出場｜這筆合計 **{p['result_r']:+.2f}R**")
                    break
                if not p["partial"] and px >= p["target_3r"]:
                    p["partial"] = True
                    p["result_r"] = 1.0                        # 1/3 部位 × 3R
                    p["events"].append({"date": d.isoformat(), "time": f"{r.ts:%H:%M}", "type": "3R 出 1/3", "price": p["target_3r"]})
                    msgs.append(f"🎯 **{label}** {d:%m/%d} {r.ts:%H:%M} 碰到 3R {p['target_3r']:g}，"
                                f"先出 1/3（+1.00R），其餘改用日 EMA9 移動停損")
            if p["status"] == "closed":
                break
        if p["status"] == "closed":
            p["checked"] = d.isoformat()
            break

        close = float(day_df["Close"].iloc[-1]) if len(day_df) else float(kb[kb["ts"].dt.date == d]["Close"].iloc[-1])
        if p["ema9"] is not None:
            p["ema9"] = round(p["ema9"] + 2 / 10 * (close - p["ema9"]), 4)
        p["last_close"] = close
        p["checked"] = d.isoformat()
        if p["partial"] and p["ema9"] is not None and close < p["ema9"]:
            p["result_r"] = round(p["result_r"] + 2 / 3 * (close - p["entry"]) / p["r"], 2)
            p["status"] = "closed"
            p["events"].append({"date": d.isoformat(), "time": "13:30", "type": "跌破 EMA9", "price": close})
            msgs.append(f"📉 **{label}** {d:%m/%d} 收盤 {close:g} 跌破日 EMA9 {p['ema9']:.2f}，剩餘 2/3 出場｜"
                        f"這筆合計 **{p['result_r']:+.2f}R**")
            break
    return msgs


def main():
    ap = argparse.ArgumentParser(description="共振回踩作多：收盤後出場追蹤")
    ap.add_argument("--no-discord", action="store_true")
    a = ap.parse_args()
    dc = Discord(not a.no_discord)
    today = datetime.now(TAIPEI).date()

    positions = load(POS_FILE, [])
    new = sync_new_alerts(positions)
    opened = [p for p in positions if p["status"] == "open"]
    if not opened:
        print("沒有持倉中的部位")
        POS_FILE.write_text(json.dumps(positions, ensure_ascii=False, indent=2), encoding="utf-8")
        return

    api = sj_client.login()
    msgs = []
    try:
        for p in opened:
            msgs += track(api, p, today)
    finally:
        api.logout()

    still = [p for p in positions if p["status"] == "open"]
    lines = []
    for p in still:
        now_r = (p.get("last_close", p["entry"]) - p["entry"]) / p["r"]
        ema = f"｜EMA9 {p['ema9']:.2f}" if p["ema9"] else ""
        stage = "已出 1/3" if p["partial"] else f"3R {p['target_3r']:g}"
        lines.append(f"・{p['code']} {p['name']} 進 {p['entry']:g}（{p['entry_day'][5:]}）收 {p.get('last_close', p['entry']):g}"
                     f"（{now_r:+.2f}R）｜停損 {p['stop']:g}｜{stage}{ema}")
    text = f"📒 **共振回踩｜{today:%m/%d} 收盤持倉追蹤**"
    if new:
        text += f"（新增 {len(new)} 筆）"
    if msgs:
        text += "\n" + "\n".join(msgs)
    text += ("\n持倉中：\n" + "\n".join(lines)) if lines else "\n目前沒有持倉。"
    dc.send(text)

    POS_FILE.write_text(json.dumps(positions, ensure_ascii=False, indent=2), encoding="utf-8")
    publish(f"data: 共振回踩 {today} 持倉追蹤")


if __name__ == "__main__":
    main()
