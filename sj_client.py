"""
永豐 Shioaji 連線工具
Key 優先讀環境變數（GitHub Actions），沒有就讀本機 .env
"""
import os, re
from pathlib import Path

import warnings
import pandas as pd
import shioaji as sj

warnings.filterwarnings("ignore", message="api.Contracts is deprecated")

ENV_FILE = Path(__file__).parent / ".env"


def env(name: str) -> str | None:
    """先讀環境變數，沒有再讀 .env"""
    if os.environ.get(name):
        return os.environ[name]
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            m = re.match(rf"\s*{name}\s*=\s*(.*)$", line)
            if m and m.group(1).strip():
                return m.group(1).strip()
    return None


def login() -> sj.Shioaji:
    """登入正式環境（只用行情權限的 Key）"""
    api = sj.Shioaji()
    api.login(api_key=env("SJ_API_KEY"), secret_key=env("SJ_SECRET_KEY"))
    return api


def stock_universe(api: sj.Shioaji) -> dict[str, dict]:
    """上市櫃普通股：4 碼、非 0 開頭、排除 ETF（category 00）、排除停牌"""
    out = {}
    for market, suffix in (("TSE", ".TW"), ("OTC", ".TWO")):
        for c in getattr(api.Contracts.Stocks, market):
            if (len(c.code) == 4 and c.code[0] != "0"
                    and c.category != "00" and not c.trading_suspended):
                out[c.code] = {"name": c.name, "yf": c.code + suffix, "market": market}
    return out


def minute_kbars(api: sj.Shioaji, code: str, start: str, end: str) -> pd.DataFrame:
    """1 分 K，ts 是 K 棒結束時間（09:01 那根 = 09:00~09:01）；Volume 單位張、Amount 單位元"""
    kb = api.kbars(api.Contracts.Stocks[code], start=start, end=end)
    df = pd.DataFrame({**kb})
    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df["ts"])
    return df
