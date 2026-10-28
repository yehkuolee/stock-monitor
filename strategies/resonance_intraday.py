"""
共振回踩作多 — 盤中引擎（對應 XS「四、盤中 AVWAP」「五、掃低與反轉」「六、突破警示」）

一檔股票一個 Engine，每來一筆成交就呼叫 on_tick()。回放和盤中盯盤共用這份邏輯。
K 棒時間用「開始時間」標示（09:00 那根 = 09:00~09:05），對應 XS 的 Time。
"""
from dataclasses import dataclass


@dataclass
class IntradayParams:
    MaxZonePct: float = 3          # EMA9/21/AVWAP 區寬上限%
    RecoverBars: int = 2           # 跌穿後最多幾根 K 收復
    MinReboundPct: float = 0.5     # 低點至反轉收盤最低反彈%
    MinClosePosPct: float = 65     # 反轉收盤在 K 棒區間最低位置%
    BreakoutBars: int = 2          # 反轉後最多幾根 K 突破
    MaxRiskPct: float = 3          # 價格至參考停損最大距離%
    StopBasis: int = 2             # 參考停損：1 反轉波段低點、2 今日低點
    StartTime: int = 90000         # 開始時間 HHMMSS
    EndTime: int = 103000          # 結束時間 HHMMSS（不含）
    MaxAlerts: int = 0             # 單股每日警示上限，0 不限
    SweepFrom: str = "hi"          # 掃低前一根收盤要在：hi 支撐上緣之上（XS 原版）、lo 下緣之上（原文：回測支撐區後跌破）
    RecoverTo: str = "hi"          # 收復 K 要收回：hi 上緣（XS 原版）、lo 下緣（原文：跌破後快速拉回）


@dataclass
class Bar:
    t: int            # 開始時間 HHMMSS
    o: float
    h: float
    l: float
    c: float
    v: float = 0      # 張
    a: float = 0      # 元
    zlo: float = 0    # 這根 K 收完時的支撐區
    zhi: float = 0


class Engine:
    def __init__(self, setup: dict, prev_close: float, p: IntradayParams = IntradayParams()):
        """setup = 盤前篩選輸出的單檔資料（resonance_watchlist.json 的一筆）"""
        self.p = p
        self.s = setup
        self.prev_close = prev_close          # 昨日最後一根 K 收盤（XS 的 Close[2]，當今天第一根是 [1] 時）
        self.bars: list[Bar] = []             # 今日已完成 K
        self.cur: Bar | None = None
        self.closed_amt = self.closed_vol = 0.0
        self.today_low = float("inf")
        self.phase = 0
        self.sweep_bar = self.reversal_bar = 0
        self.sweep_low = self.frozen_hi = self.reversal_high = 0.0
        self.alerts_today = 0
        self.last_alert_bar = -10
        self.live_avwap = 0.0
        self.stats = {"sweep": 0, "reversal": 0, "breakout_rejected": 0, "alert": 0}

    def on_tick(self, bar_t: int, price: float, bar_vol: float, bar_amt: float) -> dict | None:
        """bar_t = 目前 5 分 K 開始時間；bar_vol/bar_amt = 這根 K 到目前為止的累計張數、金額"""
        p, s = self.p, self.s
        new_bar = self.cur is None or bar_t != self.cur.t
        if new_bar:
            if self.cur is not None:
                self.bars.append(self.cur)
            self.cur = Bar(bar_t, price, price, price, price)
        cur = self.cur
        cur.h, cur.l, cur.c = max(cur.h, price), min(cur.l, price), price
        cur.v, cur.a = bar_vol, bar_amt

        in_window = p.StartTime <= bar_t < p.EndTime
        quota_ok = p.MaxAlerts == 0 or self.alerts_today < p.MaxAlerts
        self.today_low = min(self.today_low, cur.l)
        n = len(self.bars)                    # 目前這根 K 是今天第 n 根（0 起算）

        # 每根新 K 只處理一次：累加上一根完成 K，判斷掃低與反轉
        if new_bar and n >= 1:
            b1 = self.bars[-1]
            self.closed_amt += b1.a
            self.closed_vol += b1.v
            if in_window and quota_ok:
                if n >= 2:
                    ref_lo, ref_hi, close2 = self.bars[-2].zlo, self.bars[-2].zhi, self.bars[-2].c
                else:
                    ref_lo, ref_hi, close2 = s["zone_lo"], s["zone_hi"], self.prev_close
                ref_width = (ref_hi / ref_lo - 1) * 100 if ref_lo > 0 else 999

                # Phase 0 → 1：原本在支撐上方，這根低點跌穿支撐下緣
                if (self.phase == 0 and n - 1 > self.last_alert_bar and ref_lo > 0
                        and ref_width <= p.MaxZonePct
                        and close2 >= (ref_hi if p.SweepFrom == "hi" else ref_lo) and b1.l < ref_lo):
                    self.phase, self.sweep_bar, self.sweep_low = 1, n - 1, b1.l
                    self.frozen_hi = ref_hi if p.RecoverTo == "hi" else ref_lo
                    self.stats["sweep"] += 1

                # Phase 1 → 2：掃低當根或其後 RecoverBars 根內，強勢紅 K 收復
                if self.phase == 1:
                    self.sweep_low = min(self.sweep_low, b1.l)
                    if n - 1 - self.sweep_bar > p.RecoverBars:
                        self.phase = 0
                    else:
                        rng = b1.h - b1.l
                        close_pos = (b1.c - b1.l) / rng * 100 if rng > 0 else 0
                        bounce = (b1.c / self.sweep_low - 1) * 100 if self.sweep_low > 0 else 0
                        if (b1.c > b1.o and b1.c >= self.frozen_hi
                                and close_pos >= p.MinClosePosPct and bounce >= p.MinReboundPct):
                            self.phase, self.reversal_bar, self.reversal_high = 2, n - 1, b1.h
                            self.stats["reversal"] += 1

        # 即時 AVWAP = 錨點至昨日 + 今日完成 K + 目前這根
        tot_vol = s["past_volume"] + self.closed_vol + cur.v
        self.live_avwap = (s["past_amount"] + self.closed_amt + cur.a) / (tot_vol * 1000) if tot_vol > 0 else 0
        if self.live_avwap > 0:
            cur.zlo = min(s["ema_d9"], s["ema_d21"], self.live_avwap)
            cur.zhi = max(s["ema_d9"], s["ema_d21"], self.live_avwap)

        if not in_window or not quota_ok or self.live_avwap <= 0:
            self.phase = 0

        # Phase 2：逐筆檢查突破
        if self.phase != 2:
            return None
        if cur.l < self.sweep_low or n - self.reversal_bar > p.BreakoutBars:
            self.phase = 0
            return None
        if price <= self.reversal_high:
            return None

        stop = self.today_low if p.StopBasis == 2 else self.sweep_low
        risk = (price - stop) / price * 100 if 0 < stop < price else 999
        self.phase = 0                        # 第一次突破就消耗這次型態
        if not (price > s["ema_w9"] and price > s["ema_w21"] and price > s["ema_d50"]
                and price > self.live_avwap and 0 < risk <= p.MaxRiskPct):
            self.stats["breakout_rejected"] += 1
            return None

        self.alerts_today += 1
        self.last_alert_bar = n
        self.stats["alert"] += 1
        r = price - stop
        return {"code": s["code"], "name": s["name"], "bar_t": bar_t, "price": price,
                "breakout": self.reversal_high, "stop": stop, "risk_pct": round(risk, 2),
                "target_3r": round(price + 3 * r, 2), "target_5r": round(price + 5 * r, 2),
                "avwap": round(self.live_avwap, 2)}
