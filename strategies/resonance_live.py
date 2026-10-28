#!/usr/bin/env python3
"""
共振回踩作多 — 盤中盯盤（GitHub Actions 每天 08:20 啟動，跑到 10:30）

流程：盤前篩選 → Discord 推觀察名單 → 永豐逐筆訂閱 → 組 15 分 K → 盤中引擎 → 觸發就推 Discord
版本：照原文（掃低＝跌破支撐下緣、收復＝收回下緣），見 resonance_intraday.IntradayParams

用法：
  python -m strategies.resonance_live                    # 正式
  python -m strategies.resonance_live --test --no-discord   # 盤中測試：不等 09:00、時間窗放寬到 13:30、只印不推
"""
import argparse, json, os, queue, subprocess, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import shioaji as sj

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sj_client
from strategies import resonance_pullback as pre
from strategies.resonance_intraday import Engine, IntradayParams

TAIPEI = timezone(timedelta(hours=8))
ALERT_FILE = Path(__file__).resolve().parent.parent / "data" / "resonance_alerts.json"
NO_TICK_EXIT = 91000          # 這個時間還沒收到任何成交 → 視為休市


def now_tp() -> datetime:
    return datetime.now(TAIPEI).replace(tzinfo=None)


def hhmmss(t: datetime) -> int:
    return t.hour * 10000 + t.minute * 100 + t.second


class Discord:
    def __init__(self, enabled: bool):
        self.url = sj_client.env("DISCORD_RESONANCE_WEBHOOK") if enabled else None
        if enabled and not self.url:
            print("⚠️ 沒有 DISCORD_RESONANCE_WEBHOOK，只印在終端機")

    def send(self, text: str):
        print(text, "\n")
        if not self.url:
            return
        for chunk in [text[i:i + 1900] for i in range(0, len(text), 1900)]:
            try:
                requests.post(self.url, json={"content": chunk}, timeout=10).raise_for_status()
            except Exception as e:
                print(f"Discord 失敗：{e}")


def watchlist_text(day, setups: list[dict], bar_min: int) -> str:
    if not setups:
        return f"📋 **共振回踩｜{day:%m/%d} 觀察名單**\n今天沒有符合盤前條件的股票，不盯盤。"
    rows = []
    for s in sorted(setups, key=lambda x: x["zone_width_pct"]):
        dist = (s["last_close"] / s["zone_lo"] - 1) * 100
        mark = "🎯" if s["zone_ok"] else "　"
        rows.append(f"{mark} **{s['code']} {s['name']}** 昨收 {s['last_close']:g}｜支撐 {s['zone_lo']:.2f}～{s['zone_hi']:.2f}"
                    f"（寬 {s['zone_width_pct']}%）｜距下緣 {dist:+.1f}%")
    n_ok = sum(s["zone_ok"] for s in setups)
    return (f"📋 **共振回踩｜{day:%m/%d} 觀察名單**（{len(setups)} 檔，🎯 支撐區 ≤3% 共 {n_ok} 檔）\n"
            f"09:00～10:30 盯 {bar_min} 分 K：跌破支撐下緣 → 強紅 K 收回 → 突破反轉高點\n" + "\n".join(rows))


def alert_text(a: dict, bar_min: int) -> str:
    t = f"{a['bar_t'] // 10000:02d}:{a['bar_t'] // 100 % 100:02d}"
    return (f"🔔 **共振回踩作多｜{a['code']} {a['name']}**（{t} 的 {bar_min} 分 K）\n"
            f"進場 **{a['price']:g}**（突破反轉高點 {a['breakout']:g}）\n"
            f"停損 {a['stop']:g}（-{a['risk_pct']}%，今日低點）\n"
            f"目標 3R {a['target_3r']:g}｜5R {a['target_5r']:g}\n"
            f"AVWAP {a['avwap']:g}｜原文出場：3～5R 先出 1/3，其餘跌破日 EMA9 出場")


def load_alerts(day) -> list[dict]:
    data = json.loads(ALERT_FILE.read_text(encoding="utf-8")) if ALERT_FILE.exists() else {}
    return data.get(day.isoformat(), [])


def save_alerts(day, alerts: list[dict]):
    data = json.loads(ALERT_FILE.read_text(encoding="utf-8")) if ALERT_FILE.exists() else {}
    data[day.isoformat()] = alerts
    ALERT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def publish(msg: str):
    """在 GitHub Actions 上才做：把名單／警示 commit 回 repo，網頁就會更新"""
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    root = Path(__file__).resolve().parent.parent
    cmds = [["git", "add", "data/resonance_watchlist.json", "data/resonance_alerts.json"],
            ["git", "commit", "-m", msg], ["git", "pull", "--rebase", "-q"], ["git", "push", "-q"]]
    for c in cmds:
        r = subprocess.run(c, cwd=root, capture_output=True, text=True)
        if r.returncode and c[1] != "commit":
            print(f"publish 失敗 {' '.join(c)}：{r.stderr.strip()}")
            return


def subscribe_all(api, codes, q: queue.Queue):
    api.set_on_tick_stk_v1_callback(lambda exchange, tick: q.put(tick))
    for c in codes:
        api.subscribe(api.Contracts.Stocks[c], quote_type=sj.QuoteType.Tick, version=sj.QuoteVersion.v1)


def main():
    ap = argparse.ArgumentParser(description="共振回踩作多：盤中盯盤")
    ap.add_argument("--bar", type=int, default=15, choices=(5, 15))
    ap.add_argument("--test", action="store_true", help="盤中測試：時間窗放寬到 13:30")
    ap.add_argument("--no-discord", action="store_true")
    ap.add_argument("--codes", help="只盯指定股票（測試用）")
    ap.add_argument("--minutes", type=float, help="測試用：只跑幾分鐘")
    a = ap.parse_args()

    ip = IntradayParams(SweepFrom="lo", RecoverTo="lo", EndTime=133000 if a.test else 103000)
    dc = Discord(not a.no_discord)
    day = now_tp().date()

    # ── 盤前篩選 ──
    setups = pre.run(day)
    pre.save(day, setups)
    alerts = load_alerts(day)                 # 同一天重跑時保留已發過的警示
    save_alerts(day, alerts)
    publish(f"data: 共振回踩 {day} 觀察名單")
    if a.codes:
        want = a.codes.split(",")
        setups = [s for s in setups if s["code"] in want]
    dc.send(watchlist_text(day, setups, a.bar))
    if not setups:
        return

    engines = {s["code"]: Engine(s, s["last_close"], ip) for s in setups}
    bars = {}                 # code → [bar 開始時間, 累計張數, 累計金額]
    ticks_seen = 0
    q: queue.Queue = queue.Queue()
    session_down = {"flag": False}

    api = sj_client.login()
    api.set_session_down_callback(lambda: session_down.update(flag=True))
    subscribe_all(api, engines, q)
    print(f"已訂閱 {len(engines)} 檔，盯到 {ip.EndTime}")
    t_start = time.time()

    try:
        while True:
            now = now_tp()
            if hhmmss(now) >= ip.EndTime + 100 or (a.minutes and time.time() - t_start > a.minutes * 60):
                break
            if not ticks_seen and hhmmss(now) >= NO_TICK_EXIT and not a.test:
                dc.send(f"😴 {day:%m/%d} 到 09:10 都沒有成交，判斷休市，收工。")
                return
            if session_down["flag"]:
                dc.send("⚠️ 永豐連線中斷，重新連線中…")
                session_down["flag"] = False
                try:
                    api.logout()
                except Exception:
                    pass
                api = sj_client.login()
                api.set_session_down_callback(lambda: session_down.update(flag=True))
                subscribe_all(api, engines, q)
            try:
                tick = q.get(timeout=1)
            except queue.Empty:
                continue
            if tick.simtrade or tick.suspend or tick.code not in engines:
                continue                      # 試撮、暫停交易不算
            ticks_seen += 1
            ts = tick.datetime
            start = ts.replace(minute=ts.minute - ts.minute % a.bar, second=0, microsecond=0)
            bar_t = hhmmss(start)
            b = bars.get(tick.code)
            if b is None or b[0] != bar_t:
                b = bars[tick.code] = [bar_t, 0.0, 0.0]
            b[1] += float(tick.volume)
            b[2] += float(tick.amount)
            alert = engines[tick.code].on_tick(bar_t, float(tick.close), b[1], b[2])
            if alert:
                alert["time"] = ts.strftime("%H:%M:%S")
                alerts.append(alert)
                save_alerts(day, alerts)
                dc.send(alert_text(alert, a.bar))
                publish(f"data: 共振回踩 {day} 警示 {alert['code']}")
    finally:
        try:
            api.logout()
        except Exception:
            pass

    if a.test:
        for code, e in engines.items():
            for b in e.bars + ([e.cur] if e.cur else []):
                print(f"  {code} {b.t:06d} O{b.o:g} H{b.h:g} L{b.l:g} C{b.c:g} V{b.v:g} A{b.a:.0f} 支撐 {b.zlo:.2f}~{b.zhi:.2f}")
    st = {k: sum(e.stats[k] for e in engines.values()) for k in ("sweep", "reversal", "breakout_rejected", "alert")}
    save_alerts(day, alerts)
    dc.send(f"🏁 **共振回踩｜{day:%m/%d} 收工**　盯 {len(engines)} 檔、收到 {ticks_seen:,} 筆成交\n"
            f"掃低 {st['sweep']} 次 → 收復 {st['reversal']} 次 → 突破被濾掉 {st['breakout_rejected']} 次 → **警示 {st['alert']} 次**")


if __name__ == "__main__":
    main()
