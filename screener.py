#!/usr/bin/env python3
"""
台股成交量短線篩選器 - GitHub Actions 版
三層過濾：量能異動 → 籌碼確認 → 技術確認
符合者送 Claude Haiku 分析，推播 Discord
"""
import os, time, requests, urllib3, anthropic
import yfinance as yf
import pandas as pd
import ta
from datetime import datetime, timezone, timedelta

urllib3.disable_warnings()

# ── 設定 ─────────────────────────────────────────────────────────────
ANTHROPIC_KEY    = os.environ["ANTHROPIC_API_KEY"]
DISCORD_WEBHOOK  = os.environ["DISCORD_WEBHOOK_URL"]
TAIPEI           = timezone(timedelta(hours=8))
VOLUME_RATIO_MIN = 1.5   # 量比門檻（今日累積量 ÷ 預期量）
CHANGE_MIN       = 0.5   # 最低漲幅 %
KD_MAX           = 80    # KD 不追高
MAX_ALERTS       = 5     # 每次最多推幾支


# ── 時間工具 ──────────────────────────────────────────────────────────
def now_tp():
    return datetime.now(TAIPEI)

def elapsed_pct():
    """今日已開盤比例（09:00 = 0%，13:30 = 100%）"""
    now = now_tp()
    start = now.replace(hour=9, minute=0, second=0, microsecond=0)
    elapsed = max(1, (now - start).total_seconds() / 60)
    return min(elapsed / 270, 1.0)


# ── 股票清單（上市主要個股 ~300 支）─────────────────────────────────
def get_stock_list() -> list[str]:
    """優先從 FinMind 取得當前上市股票清單，失敗用備用清單"""
    try:
        resp = requests.get(
            "https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInfo",
            timeout=15, verify=False
        )
        data = resp.json().get("data", [])
        stocks = [
            s["stock_id"] for s in data
            if len(s.get("stock_id", "")) == 4
            and s["stock_id"].isdigit()
            and "上市" in s.get("exchange", "")
        ]
        if len(stocks) > 100:
            print(f"FinMind 取得 {len(stocks)} 支上市股票")
            return stocks
    except Exception as e:
        print(f"FinMind 失敗，使用備用清單：{e}")

    return [
        "2330","2317","2454","2308","2382","3711","2303","2412",
        "2881","2882","2891","2886","2884","2892","2885","2883",
        "2002","1301","1303","2105","2207","2357","2395","2379",
        "3034","3037","3008","2408","2327","2344","2347","2376",
        "2385","2392","2394","3045","4904","4938","5871","5876",
        "5880","6505","6669","3661","2609","2615","2618","1216",
        "1326","3481","3533","6770","2367","2371","2377","2387",
        "2388","2404","2406","2436","2439","2449","2451","2458",
        "2478","2492","2498","2603","2606","2610","2634","2641",
        "3006","3014","3035","3041","3042","3044","3046","3052",
        "3055","3060","3063","3064","3443","3450","3479","3491",
        "3515","3529","3545","3576","3596","3611","3617","4919",
        "4977","5234","5269","5347","5388","5483","6147","6153",
        "6176","6182","6183","6184","6188","6213","6214","6217",
        "6219","6220","6227","6230","6269","6271","6272","6274",
        "6278","6281","6282","6285","6286","6288","6289","6290",
        "2345","2356","2360","2362","2365","2368","2369","2374",
        "2375","2383","2384","2386","2396","2397","2399","2401",
        "2409","2441","2450","2460","2464","2466","2467","2472",
        "2474","2476","2477","2480","2481","2483","2485","2486",
        "2488","2489","2491","2495","2496","2499","2501","2504",
        "2505","2506","2511","2515","2516","2520","2530","2531",
        "2545","2548","2601","2605","2606","2607","2608","2611",
        "2612","2613","2614","2616","2617","2619","2620","2621",
        "2622","2624","2626","2630","2633","2636","2637","2638",
        "2639","2640","2642","2643","2645","2647","2648","2649",
        "2850","2851","2852","2855","2856","2860","2861","2862",
        "2863","2864","2867","2868","2869","2870","2872","2873",
        "2874","2875","2876","2877","2878","2879","2880","2887",
        "2888","2889","2890","2893","2894","2895","2896","2897",
        "2898","2899","2901","2903","2904","2905","2906","2908",
        "3001","3002","3003","3004","3005","3007","3009","3010",
        "3011","3012","3013","3015","3016","3017","3018","3020",
        "3021","3022","3023","3024","3025","3026","3027","3028",
    ]


# ── TWSE 即時批次查詢 ─────────────────────────────────────────────────
def get_realtime_batch(symbols: list[str]) -> list[dict]:
    """每批 100 支，回傳有效報價的股票資料"""
    results = []
    for i in range(0, len(symbols), 100):
        batch = symbols[i:i + 100]
        ex_ch = "|".join(f"tse_{s}.tw" for s in batch)
        try:
            r = requests.get(
                f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
                f"?ex_ch={ex_ch}&json=1&delay=0",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=15, verify=False
            )
            for item in r.json().get("msgArray", []):
                z = item.get("z", "-")
                if z and z != "-":
                    results.append(item)
        except Exception as e:
            print(f"批次查詢失敗 (batch {i}): {e}")
        time.sleep(0.3)
    return results


def parse_realtime(item: dict) -> dict | None:
    """解析 TWSE 即時資料"""
    def sf(v):
        try: return float(v)
        except: return None

    price = sf(item.get("z"))
    yday  = sf(item.get("y"))
    if not price or not yday:
        return None

    bid_vols = [float(x) for x in item.get("g", "").split("_") if x]
    ask_vols = [float(x) for x in item.get("f", "").split("_") if x]

    return {
        "code":     item.get("c", ""),
        "name":     item.get("n", ""),
        "price":    price,
        "yday":     yday,
        "change":   price - yday,
        "change_p": (price - yday) / yday * 100,
        "open":     sf(item.get("o")),
        "high":     sf(item.get("h")),
        "low":      sf(item.get("l")),
        "volume":   sf(item.get("v")) or 0,
        "limit_up": sf(item.get("u")),
        "limit_dn": sf(item.get("w")),
        "bid_vol":  sum(bid_vols),
        "ask_vol":  sum(ask_vols),
    }


# ── 三大法人（昨日資料，盤中可查）────────────────────────────────────
def get_institutional() -> dict[str, dict]:
    """回傳 {code: {外資淨, 投信淨, 合計}} 的字典"""
    try:
        resp = requests.get(
            "https://www.twse.com.tw/rwd/zh/fund/T86"
            "?response=json&selectType=ALL",
            timeout=10, headers={"User-Agent": "Mozilla/5.0"},
            verify=False
        )
        data = resp.json()
        if data.get("stat") != "OK":
            return {}

        cols = ["代號","名稱","外資買","外資賣","外資淨",
                "投信買","投信賣","投信淨","自營買","自營賣","自營淨","合計"]
        result = {}
        for row in data.get("data", []):
            if len(row) < 12:
                continue
            d = dict(zip(cols, row))
            code = d["代號"]
            def to_int(v):
                try: return int(v.replace(",", ""))
                except: return 0
            result[code] = {
                "外資淨": to_int(d["外資淨"]),
                "投信淨": to_int(d["投信淨"]),
                "合計":   to_int(d["合計"]),
            }
        print(f"三大法人資料：{len(result)} 支")
        return result
    except Exception as e:
        print(f"法人資料失敗：{e}")
        return {}


# ── 技術指標（yfinance）─────────────────────────────────────────────
def get_technical(code: str) -> dict | None:
    """抓 30 日日線計算 KD、MA5、5日均量"""
    try:
        df = yf.download(
            f"{code}.TW", period="30d", interval="1d",
            auto_adjust=True, progress=False
        )
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if df.empty or len(df) < 10:
            return None

        close = df["Close"].squeeze()
        high  = df["High"].squeeze()
        low   = df["Low"].squeeze()

        stoch = ta.momentum.StochasticOscillator(high, low, close, window=9, smooth_window=3)
        k = float(stoch.stoch().iloc[-1])
        d = float(stoch.stoch_signal().iloc[-1])
        ma5  = float(close.rolling(5).mean().iloc[-1])
        ma20 = float(close.rolling(20).mean().iloc[-1])
        avg_vol_5d = float(df["Volume"].tail(5).mean())

        return {
            "k": k, "d": d,
            "ma5": ma5, "ma20": ma20,
            "avg_vol_5d": avg_vol_5d,
        }
    except Exception as e:
        print(f"技術指標失敗 {code}：{e}")
        return None


# ── 三層過濾 ──────────────────────────────────────────────────────────
def apply_filters(
    rt: dict,
    inst: dict,
    tech: dict,
    ep: float,
) -> tuple[bool, list[str]]:
    """
    回傳 (通過與否, 觸發條件說明列表)
    三層：量能 → 籌碼 → 技術
    """
    reasons = []

    # ── 層一：量能異動 ─────────────────────────────────────────────
    if rt["change_p"] < CHANGE_MIN:
        return False, []
    if rt["limit_up"] and rt["price"] >= rt["limit_up"]:
        return False, []    # 已漲停，追不進
    if rt["limit_dn"] and rt["price"] <= rt["limit_dn"]:
        return False, []

    expected_vol = tech["avg_vol_5d"] * ep if tech else 0
    vol_ratio = rt["volume"] / expected_vol if expected_vol > 0 else 0
    if vol_ratio < VOLUME_RATIO_MIN:
        return False, []
    reasons.append(f"量比 {vol_ratio:.1f}x 爆量")

    # 買壓確認（委買量 > 委賣量）
    if rt["bid_vol"] > rt["ask_vol"] * 1.2:
        reasons.append(f"委買 > 委賣（{rt['bid_vol']:.0f} vs {rt['ask_vol']:.0f}）")

    # ── 層二：籌碼確認 ─────────────────────────────────────────────
    inst_info = inst.get(rt["code"], {})
    foreign = inst_info.get("外資淨", 0)
    trust   = inst_info.get("投信淨", 0)
    total   = inst_info.get("合計", 0)

    if foreign > 0 and trust > 0:
        reasons.append(f"外資+投信雙買（{foreign:+,} / {trust:+,} 張）")
    elif foreign > 200:
        reasons.append(f"外資大買 {foreign:+,} 張")
    elif trust > 50:
        reasons.append(f"投信買超 {trust:+,} 張")
    elif total <= 0 and foreign <= 0:
        return False, []    # 無法人支撐，跳過

    # ── 層三：技術確認 ─────────────────────────────────────────────
    if not tech:
        return False, []

    if tech["k"] > KD_MAX:
        return False, []    # KD 高檔鈍化，不追
    if rt["price"] < tech["ma5"]:
        return False, []    # 股價低於 MA5，無力

    # KD 位置
    if tech["k"] < 30:
        reasons.append(f"KD 低檔翻揚 K:{tech['k']:.1f}")
    elif tech["k"] < 60:
        reasons.append(f"KD 中段健康 K:{tech['k']:.1f}")
    else:
        reasons.append(f"KD:{tech['k']:.1f}（留意高檔）")

    # 均線多頭
    if tech["ma5"] > tech["ma20"]:
        reasons.append("均線多頭排列")

    return True, reasons


# ── Claude Haiku 分析 ────────────────────────────────────────────────
def claude_analyze(stock: dict) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    rt, tech, inst_info, reasons = (
        stock["rt"], stock["tech"], stock["inst"], stock["reasons"]
    )
    prompt = f"""你是台股短線操作分析師，請用繁體中文分析以下個股，給出簡潔的操作建議（100字以內）。

股票：{rt['name']}（{rt['code']}）
現價：{rt['price']:,.0f} 漲幅：{rt['change_p']:+.2f}%
今日成交量：{rt['volume']:,.0f} 張
開/高/低：{rt['open']:,.0f} / {rt['high']:,.0f} / {rt['low']:,.0f}
KD：{tech['k']:.1f} / {tech['d']:.1f}
MA5/MA20：{tech['ma5']:,.0f} / {tech['ma20']:,.0f}
外資昨日：{inst_info.get('外資淨', 0):+,} 張
投信昨日：{inst_info.get('投信淨', 0):+,} 張
觸發原因：{', '.join(reasons)}

請給出：①進場時機 ②目標價 ③停損位 ④主要風險。格式簡潔。"""

    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text


# ── Discord 推播 ──────────────────────────────────────────────────────
def send_discord(stocks: list[dict]):
    now_str = now_tp().strftime("%m/%d %H:%M")

    if not stocks:
        # 無訊號時靜默，不推播
        print("無符合條件個股，本次不推播")
        return

    embeds = []
    for s in stocks:
        rt, tech, reasons, ai = s["rt"], s["tech"], s["reasons"], s["ai"]
        color = 0x3fb950 if rt["change_p"] >= 0 else 0xf85149
        inst  = s["inst"]

        fields = [
            {"name": "📈 價格", "value": f"**{rt['price']:,.0f}**　{rt['change_p']:+.2f}%", "inline": True},
            {"name": "📊 成交量", "value": f"{rt['volume']:,.0f} 張", "inline": True},
            {"name": "🔥 觸發條件", "value": "\n".join(f"• {r}" for r in reasons), "inline": False},
            {"name": "📐 技術", "value": f"KD {tech['k']:.1f}/{tech['d']:.1f}　MA5 {tech['ma5']:,.0f}", "inline": True},
            {"name": "🏦 法人(昨)", "value": f"外資 {inst.get('外資淨',0):+,}　投信 {inst.get('投信淨',0):+,}", "inline": True},
            {"name": "🤖 Claude 分析", "value": ai, "inline": False},
        ]

        embeds.append({
            "title": f"🚨 {rt['name']}（{rt['code']}）",
            "color": color,
            "fields": fields,
            "footer": {"text": f"台股短線篩選器 · {now_str}"},
        })

    payload = {
        "content": f"📡 **台股短線機會警報** `{now_str}`　共 {len(stocks)} 支符合",
        "embeds": embeds[:10],  # Discord 最多 10 個 embed
    }

    resp = requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
    if resp.status_code in (200, 204):
        print(f"Discord 推播成功：{len(stocks)} 支")
    else:
        print(f"Discord 推播失敗：{resp.status_code} {resp.text}")


# ── 主流程 ────────────────────────────────────────────────────────────
def main():
    print(f"\n=== 台股短線篩選器 {now_tp().strftime('%Y/%m/%d %H:%M')} ===")

    ep = elapsed_pct()
    print(f"今日開盤進度：{ep*100:.1f}%")

    # 1. 取股票清單
    symbols = get_stock_list()
    print(f"監測股票數：{len(symbols)}")

    # 2. 批次即時報價
    raw_data = get_realtime_batch(symbols)
    print(f"取得即時報價：{len(raw_data)} 支")

    # 解析並過濾無效資料
    rt_list = [r for r in (parse_realtime(d) for d in raw_data) if r]

    # 3. 按成交量排序，取前 50
    top50 = sorted(rt_list, key=lambda x: x["volume"], reverse=True)[:50]
    print(f"成交量前 50：{[s['code'] for s in top50[:10]]}...")

    # 4. 取三大法人資料（昨日）
    inst = get_institutional()

    # 5. 三層過濾
    candidates = []
    for rt in top50:
        # 先快速過濾：漲幅 & 非漲跌停
        if rt["change_p"] < CHANGE_MIN:
            continue
        if rt["limit_up"] and rt["price"] >= rt["limit_up"]:
            continue

        # 抓技術指標（只對通過初篩的股票才呼叫 yfinance）
        tech = get_technical(rt["code"])
        if not tech:
            continue

        passed, reasons = apply_filters(rt, inst, tech, ep)
        if passed:
            candidates.append({
                "rt": rt,
                "tech": tech,
                "inst": inst.get(rt["code"], {}),
                "reasons": reasons,
            })
            print(f"✅ {rt['code']} {rt['name']}：{', '.join(reasons)}")

    print(f"通過篩選：{len(candidates)} 支")

    # 6. 限制最多 MAX_ALERTS 支，優先選量比最高的
    candidates = sorted(
        candidates,
        key=lambda x: x["rt"]["volume"] / (x["tech"]["avg_vol_5d"] * ep or 1),
        reverse=True
    )[:MAX_ALERTS]

    # 7. Claude 分析
    for s in candidates:
        print(f"Claude 分析中：{s['rt']['code']} {s['rt']['name']}")
        s["ai"] = claude_analyze(s)
        time.sleep(0.5)

    # 8. Discord 推播
    send_discord(candidates)


if __name__ == "__main__":
    main()
