"""
╔══════════════════════════════════════════════════════════════════════╗
║         币安合约 RSI 多标的交易机器人  V3.3                            ║
║                                                                      ║
║  BTC 自动交易（BTCUSDT 永续合约）：                                    ║
║   · 15m RSI ≤ 20 → 做多，名义金额 = 账户余额 × 2.5 倍                  ║
║   · 15m RSI ≥ 80 → 做空，名义金额 = 账户余额 × 2.5 倍                  ║
║   · 止盈：限价单，开仓价 ±0.5%，3次重试；失败→保留仓位（不再紧急平仓）    ║
║   · 止损：仅 Telegram 提醒参考价（开仓价 ±5%），需人工手动止损            ║
║   · 持仓归零检测 → 冷却1小时；正常交易后冷却30分钟                        ║
║                                                                      ║
║  ETH 自动交易（ETHUSDT 永续合约）：                                    ║
║   · 15m RSI < 17 → 做多，名义金额恒定 $10,000                          ║
║   · 15m RSI > 83 → 做空，名义金额恒定 $10,000                          ║
║   · 止盈：限价单，开仓价 ±0.5%，3次重试；失败→保留仓位（不再紧急平仓）    ║
║   · 止损：自动挂 STOP_MARKET 单（ATR 动态 1%~3%，标记价触发）           ║
║          失败→记录真因+告警，仓位保留（绝不因挂单失败而平仓）            ║
║   · 反手逻辑：反向信号 → 先平旧仓 → 再开新仓                             ║
║   · 持仓归零检测 → 撤残单 + 冷却1小时；正常交易后冷却30分钟               ║
║                                                                      ║
║  DOGE 自动交易（DOGEUSDT 永续合约，可用开关关闭回到仅预警）：            ║
║   · 15m RSI < 15 且 1h RSI < 20 → 做多（双周期确认）                   ║
║   · 15m RSI > 85 且 1h RSI > 80 → 做空（双周期确认）                   ║
║   · 止盈/止损/反手逻辑与 ETH 完全一致                                   ║
║   · ⚠️ 名义金额与杠杆为占位默认值，务必按账户余额与风险偏好调整          ║
║                                                                      ║
║  核心原则：指标触发即开仓；止盈/止损挂单失败只告警、不平仓               ║
║                                                                      ║
║  公共能力：                                                            ║
║   · Telegram 推送（Markdown 失败自动降级纯文本）                        ║
║   · 文件日志 rsi_bot_v3.log（含挂单失败的币安返回真因）                  ║
║   · 24 小时心跳播报（含 BTC/ETH/DOGE 三仓状态）                         ║
║   · 签名请求统一重试（3次），失败原因落盘                               ║
║   · 精度启动时从交易所自动拉取                                           ║
╚══════════════════════════════════════════════════════════════════════╝

依赖安装：pip install requests pandas
运行方式：python rsi_bot_v3.py
"""

import hashlib
import hmac
import logging
import math
import os
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import pandas as pd
import requests

# ══════════════════════════════════════════════════════════════════════
#  🔐 必填配置
# ══════════════════════════════════════════════════════════════════════
BINANCE_API_KEY    = ""          # 币安 API Key（需要合约交易权限）
BINANCE_SECRET_KEY = ""          # 币安 Secret Key
BASE_URL           = "https://fapi.binance.com"

TG_BOT_TOKEN = ""                # @BotFather 获取
TG_CHAT_ID   = ""                # @userinfobot 获取
ENABLE_TG    = True

# 也可改用环境变量提供密钥（推荐：避免把真实密钥写进代码文件，见 .env.example）
BINANCE_API_KEY    = os.environ.get("BINANCE_API_KEY", BINANCE_API_KEY)
BINANCE_SECRET_KEY = os.environ.get("BINANCE_SECRET_KEY", BINANCE_SECRET_KEY)
TG_BOT_TOKEN       = os.environ.get("TG_BOT_TOKEN", TG_BOT_TOKEN)
TG_CHAT_ID         = os.environ.get("TG_CHAT_ID", TG_CHAT_ID)
if os.environ.get("ENABLE_TG") is not None:
    ENABLE_TG = os.environ["ENABLE_TG"].lower() not in ("0", "false", "no")

# ══════════════════════════════════════════════════════════════════════
#  📈 BTC 交易参数
# ══════════════════════════════════════════════════════════════════════
BTC_SYMBOL          = "BTCUSDT"
BTC_INTERVAL        = "15m"
BTC_RSI_BUY         = 20          # RSI ≤ 此值 → 做多
BTC_RSI_SELL        = 80          # RSI ≥ 此值 → 做空
BTC_LEVERAGE_MULT   = 2.5         # 开仓名义 = 账户余额 × 此倍数
BTC_LEVERAGE        = 10          # ⬅ 新增：BTC 账户杠杆（原版漏设，跑在遗留值上）
                                  #    名义=余额×2.5（≈2.5x 有效），设 10x 仅为让强平距离可控
                                  #    不想改 BTC 现状可注释掉 init() 里对应那行
BTC_TAKE_PROFIT_PCT = 0.005       # 止盈比例（0.5%）
BTC_STOP_LOSS_PCT   = 0.05        # 止损提醒价位（开仓价 ±5%），仅发 TG 提醒，不挂单
BTC_SL_COOLDOWN     = 3600        # 持仓归零后冷却秒数（1小时）
BTC_TRADE_COOLDOWN  = 1800        # 正常交易后冷却秒数（30分钟）
BTC_MAX_TP_RETRY    = 3           # 止盈挂单最大重试次数
BTC_DUST_QTY        = 0.0005      # 持仓判零阈值：小于此数量视为无仓，避免灰尘仓位锁死信号
RECV_WINDOW         = 10000       # API 时间戳容忍窗口（毫秒）

# 精度（启动时自动获取）
_BTC_QTY_PREC   = 3
_BTC_PRICE_PREC = 1

# ══════════════════════════════════════════════════════════════════════
#  📈 ETH 交易参数
# ══════════════════════════════════════════════════════════════════════
ETH_SYMBOL          = "ETHUSDT"
ETH_INTERVAL        = "15m"
ETH_RSI_PERIOD      = 14
ETH_RSI_OVERSELL    = 17             # RSI < 此值 → 做多
ETH_RSI_OVERBUY     = 83             # RSI > 此值 → 做空
ETH_TRADE_USDT      = 10_000.0       # 每笔恒定名义金额（美元）
ETH_LEVERAGE        = 20             # ETH 杠杆倍数
ETH_TAKE_PROFIT_PCT = 0.005          # 止盈比例（0.5%）
# —— 止损改为 ATR 动态：止损距离 = ATR × 倍数，再夹在 [MIN, MAX] 区间内 ——
ETH_ATR_PERIOD      = 14             # ATR 周期（与 RSI 同用 15m K 线）
ETH_SL_ATR_MULT     = 2.0            # 止损距离 = ATR × 此倍数
ETH_SL_MIN_PCT      = 0.01           # 止损距离下限（1%）：防行情过静时被噪音扫损
ETH_SL_MAX_PCT      = 0.03           # 止损距离上限（3%）：20x 下务必远离强平线（≈4.5%）
ETH_SL_COOLDOWN     = 3600           # 持仓归零后冷却秒数（1小时）
ETH_TRADE_COOLDOWN  = 1800           # 正常交易后冷却秒数（30分钟）
ETH_MAX_TP_RETRY    = 3              # 止盈/止损挂单最大重试次数

# 精度（启动时自动获取）
_ETH_QTY_PREC   = 2
_ETH_PRICE_PREC = 2

# ══════════════════════════════════════════════════════════════════════
#  📡 DOGE 参数（双周期 RSI；可交易，也可仅预警）
# ══════════════════════════════════════════════════════════════════════
DOGE_SYMBOL      = "DOGEUSDT"
DOGE_TF_FAST     = "15m"
DOGE_TF_SLOW     = "1h"
DOGE_RSI_PERIOD  = 14
DOGE_RSI_OB_FAST = 85
DOGE_RSI_OB_SLOW = 80
DOGE_RSI_OS_FAST = 15
DOGE_RSI_OS_SLOW = 20
DOGE_COOLDOWN_MIN = 30            # 仅预警模式下的预警冷却（分钟）

# —— DOGE 自动交易开关与参数（新增：从“仅预警”升级为“可交易”）——
DOGE_ENABLE_TRADE   = True            # True=自动交易；False=仅预警（保留旧行为）
# ⚠️⚠️ 下面两项是我替你填的占位默认值，直接关系到下单大小与强平距离，务必自行确认！
DOGE_TRADE_USDT     = 2_000.0         # 每笔恒定名义金额（美元）。ETH 是 1 万，DOGE 默认给小一些
DOGE_LEVERAGE       = 10              # DOGE 波动大于 ETH，默认杠杆设低于 ETH（10x，强平≈9%）
# —— 其余与 ETH 一致 ——
DOGE_TAKE_PROFIT_PCT = 0.005          # 止盈 0.5%
DOGE_ATR_PERIOD      = 14
DOGE_SL_ATR_MULT     = 2.0
DOGE_SL_MIN_PCT      = 0.01           # 止损下限 1%
DOGE_SL_MAX_PCT      = 0.03           # 止损上限 3%（10x 强平≈9%，留足安全垫）
DOGE_SL_COOLDOWN     = 3600           # 持仓归零后冷却（1小时）
DOGE_TRADE_COOLDOWN  = 1800           # 正常交易后冷却（30分钟）
DOGE_MAX_TP_RETRY    = 3

# 精度（启动时自动获取）
_DOGE_QTY_PREC   = 0
_DOGE_PRICE_PREC = 5

# ══════════════════════════════════════════════════════════════════════
#  ⚙️ 全局运行参数
# ══════════════════════════════════════════════════════════════════════
POLL_INTERVAL    = 2              # 主循环轮询间隔（秒），与 BTC 原版一致
RSI_KLINE_LIMIT  = 250            # 拉取 K 线数量
HEARTBEAT_HOURS  = 24             # 心跳间隔（小时）

# ══════════════════════════════════════════════════════════════════════
#  📝 日志
# ══════════════════════════════════════════════════════════════════════
logging.basicConfig(
    filename="rsi_bot_v3.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    encoding="utf-8",
)
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
#  📡 Telegram（Markdown 失败自动降级纯文本）
# ══════════════════════════════════════════════════════════════════════
def send_tg(msg: str):
    if not ENABLE_TG or not TG_BOT_TOKEN:
        return
    url     = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id":                TG_CHAT_ID,
        "text":                   msg,
        "parse_mode":             "Markdown",
        "disable_web_page_preview": True,
    }
    try:
        res = requests.post(url, json=payload, timeout=5)
        if res.status_code == 200:
            return
        # Markdown 解析失败 → 降级纯文本
        logger.warning(f"TG Markdown 推送失败，降级纯文本: {res.text[:200]}")
        payload.pop("parse_mode")
        res2 = requests.post(url, json=payload, timeout=5)
        if res2.status_code != 200:
            logger.error(f"TG 纯文本推送失败: {res2.text[:200]}")
    except Exception as e:
        logger.error(f"TG 推送异常: {e}")
        print(f"  [TG] 异常: {e}")


# ══════════════════════════════════════════════════════════════════════
#  🔑 签名 & HTTP 客户端
# ══════════════════════════════════════════════════════════════════════
# 最近一次签名请求的失败原因（含币安 code+msg），供下游告警/日志定位用
_last_api_error: str = ""


def _sign(params: dict) -> str:
    return hmac.new(
        BINANCE_SECRET_KEY.encode("utf-8"),
        urlencode(params).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def signed_request(method: str, endpoint: str, params: dict = None, quiet=False) -> dict | None:
    """带签名 + 3次重试的统一请求入口。

    任何非 200 都会把币安返回体（含 code+msg）写进日志并存入 _last_api_error，
    这样止损/止盈等挂单失败时，事后能从日志直接定位真因，而不是只看到 None。
    quiet=True 的设置类请求（如重复设杠杆/保证金模式）降级为 INFO，避免刷屏。
    """
    global _last_api_error
    if params is None:
        params = {}
    params["recvWindow"] = RECV_WINDOW
    params["timestamp"]  = int(time.time() * 1000)
    params["signature"]  = _sign(params)
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    url     = f"{BASE_URL}{endpoint}"

    for attempt in range(3):
        try:
            if method == "GET":
                r = requests.get(url, params=params, headers=headers, timeout=10)
            elif method == "DELETE":
                r = requests.delete(url, params=params, headers=headers, timeout=10)
            else:
                r = requests.post(url, params=params, headers=headers, timeout=10)

            if r.status_code == 200:
                return r.json()

            # 5xx：服务端临时故障，重试
            if r.status_code >= 500:
                _last_api_error = f"HTTP {r.status_code} {r.text[:200]}"
                logger.warning(f"API 5xx 重试 | {method} {endpoint} | attempt={attempt+1} | {r.text[:200]}")
                time.sleep(1)
                continue

            # 4xx：币安业务拒单，返回体里的 code+msg 才是真因 —— 必须落盘
            _last_api_error = f"HTTP {r.status_code} {r.text[:200]}"
            if quiet:
                logger.info(f"API {r.status_code}(quiet) | {method} {endpoint} | {r.text[:200]}")
            else:
                logger.error(f"API {r.status_code} 拒单 | {method} {endpoint} | {r.text[:300]}")
                print(f"  [API {r.status_code}] {r.text[:200]}")
            return None
        except Exception as e:
            _last_api_error = f"EXCEPTION {e}"
            logger.error(f"签名请求异常 | {method} {endpoint} | attempt={attempt+1} | {e}")
            print(f"  [请求异常 attempt={attempt+1}] {e}")
            time.sleep(1)
    return None


def public_get(endpoint: str, params: dict = None) -> list | dict | None:
    """公共无签名行情接口"""
    try:
        r = requests.get(f"{BASE_URL}{endpoint}", params=params or {}, timeout=10)
        if r.status_code == 200:
            return r.json()
        print(f"  [公共API {r.status_code}] {r.text[:120]}")
    except Exception as e:
        print(f"  [公共API异常] {e}")
    return None


def is_order_ok(res: dict | None) -> bool:
    """严格判断订单是否真正成功"""
    if not res or not res.get("orderId"):
        return False
    return res.get("status", "") in ("NEW", "FILLED", "PARTIALLY_FILLED")


# ══════════════════════════════════════════════════════════════════════
#  🔧 格式化工具
# ══════════════════════════════════════════════════════════════════════
def floor_qty(val: float, precision: int) -> float:
    """截断数量（不四舍五入，防余额不足）"""
    factor = 10 ** precision
    return math.floor(float(val) * factor) / factor


def fmt_qty(val: float, precision: int) -> str:
    v = floor_qty(val, precision)
    return str(int(v)) if precision == 0 else f"{v:.{precision}f}"


def fmt_price(val: float, precision: int) -> str:
    if precision == 0:
        return str(int(round(val)))
    return f"{round(val, precision):.{precision}f}"


def now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


# ══════════════════════════════════════════════════════════════════════
#  🔧 初始化：精度 & 账户设置
# ══════════════════════════════════════════════════════════════════════
def init():
    global _BTC_QTY_PREC, _BTC_PRICE_PREC, _ETH_QTY_PREC, _ETH_PRICE_PREC
    global _DOGE_QTY_PREC, _DOGE_PRICE_PREC

    info = public_get("/fapi/v1/exchangeInfo")
    if info:
        for s in info["symbols"]:
            if s["symbol"] == BTC_SYMBOL:
                _BTC_QTY_PREC   = s["quantityPrecision"]
                _BTC_PRICE_PREC = s["pricePrecision"]
                print(f"  BTC 精度: qty={_BTC_QTY_PREC}, price={_BTC_PRICE_PREC}")
            if s["symbol"] == ETH_SYMBOL:
                _ETH_QTY_PREC   = s["quantityPrecision"]
                _ETH_PRICE_PREC = s["pricePrecision"]
                print(f"  ETH 精度: qty={_ETH_QTY_PREC}, price={_ETH_PRICE_PREC}")
            if s["symbol"] == DOGE_SYMBOL:
                _DOGE_QTY_PREC   = s["quantityPrecision"]
                _DOGE_PRICE_PREC = s["pricePrecision"]
                print(f"  DOGE 精度: qty={_DOGE_QTY_PREC}, price={_DOGE_PRICE_PREC}")
    else:
        print("  ⚠️ 无法获取精度，使用默认值")

    # 单向持仓模式
    signed_request("POST", "/fapi/v1/positionSide/dual",
                   {"dualSidePosition": "false"}, quiet=True)

    # BTC 账户设置（杠杆原版漏设，这里补上；不想改可注释掉下一行）
    signed_request("POST", "/fapi/v1/leverage",
                   {"symbol": BTC_SYMBOL, "leverage": BTC_LEVERAGE})
    signed_request("POST", "/fapi/v1/marginType",
                   {"symbol": BTC_SYMBOL, "marginType": "CROSSED"}, quiet=True)
    # ETH 账户设置
    signed_request("POST", "/fapi/v1/leverage",
                   {"symbol": ETH_SYMBOL, "leverage": ETH_LEVERAGE})
    signed_request("POST", "/fapi/v1/marginType",
                   {"symbol": ETH_SYMBOL, "marginType": "CROSSED"}, quiet=True)
    # DOGE 账户设置（仅交易模式需要）
    if DOGE_ENABLE_TRADE:
        signed_request("POST", "/fapi/v1/leverage",
                       {"symbol": DOGE_SYMBOL, "leverage": DOGE_LEVERAGE})
        signed_request("POST", "/fapi/v1/marginType",
                       {"symbol": DOGE_SYMBOL, "marginType": "CROSSED"}, quiet=True)

    print(f"  BTC 杠杆已设置为 {BTC_LEVERAGE}x（名义仍=余额×{BTC_LEVERAGE_MULT}）")
    print(f"  ETH 杠杆已设置为 {ETH_LEVERAGE}x")
    if DOGE_ENABLE_TRADE:
        print(f"  DOGE 杠杆已设置为 {DOGE_LEVERAGE}x（交易模式）")
    else:
        print("  DOGE 仅预警模式")
    print("  账户初始化完成")


# ══════════════════════════════════════════════════════════════════════
#  📊 行情 & 指标
# ══════════════════════════════════════════════════════════════════════
def fetch_closes(symbol: str, interval: str, limit: int = RSI_KLINE_LIMIT) -> list[float] | None:
    data = public_get("/fapi/v1/klines",
                      {"symbol": symbol, "interval": interval, "limit": limit})
    return [float(b[4]) for b in data] if data else None


def fetch_ohlcv(symbol: str, interval: str, limit: int = RSI_KLINE_LIMIT):
    """返回 (highs, lows, closes)，用于 ATR 计算"""
    data = public_get("/fapi/v1/klines",
                      {"symbol": symbol, "interval": interval, "limit": limit})
    if not data:
        return None, None, None
    return (
        [float(b[2]) for b in data],
        [float(b[3]) for b in data],
        [float(b[4]) for b in data],
    )


def calc_rsi_wilder(closes: list[float], period: int = 14) -> float | None:
    """Wilder 平滑 RSI（与 TradingView 默认一致）"""
    if len(closes) < period + 1:
        return None
    deltas   = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains    = [max(d, 0.0) for d in deltas]
    losses   = [abs(min(d, 0.0)) for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    return round(100.0 - 100.0 / (1.0 + avg_gain / avg_loss), 2)


def calc_rsi_ema(closes: list[float], period: int = 14) -> float | None:
    """EWM RSI（com=period-1，与 BTC 原版 pandas ewm 一致）"""
    if len(closes) < period + 1:
        return None
    df    = pd.Series(closes)
    delta = df.diff()
    up    = delta.clip(lower=0)
    down  = -delta.clip(upper=0)
    rs    = up.ewm(com=period - 1, adjust=False).mean() / \
            down.ewm(com=period - 1, adjust=False).mean()
    rsi   = 100 - (100 / (1 + rs))
    val   = rsi.iloc[-1]
    return None if pd.isna(val) else round(float(val), 2)


def calc_atr(highs, lows, closes, period: int = 14) -> float | None:
    if not highs or len(closes) < period + 1:
        return None
    trs = [
        max(highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i]  - closes[i - 1]))
        for i in range(1, len(closes))
    ]
    return sum(trs[-period:]) / period


def get_mark_price(symbol: str) -> float | None:
    data = public_get("/fapi/v1/premiumIndex", {"symbol": symbol})
    return float(data["markPrice"]) if data else None


# ══════════════════════════════════════════════════════════════════════
#  💰 账户 & 持仓查询
# ══════════════════════════════════════════════════════════════════════
def get_usdt_balance() -> float:
    data = signed_request("GET", "/fapi/v2/balance", quiet=True)
    if data:
        for a in data:
            if a["asset"] == "USDT":
                return float(a["availableBalance"])
    return 0.0


def get_position(symbol: str) -> tuple[float, float, float]:
    """返回 (positionAmt, entryPrice, unrealizedPnl)"""
    data = signed_request("GET", "/fapi/v2/positionRisk",
                          {"symbol": symbol}, quiet=True)
    if data:
        for p in data:
            if p["symbol"] == symbol:
                return (float(p["positionAmt"]),
                        float(p["entryPrice"]),
                        float(p["unRealizedProfit"]))
    return 0.0, 0.0, 0.0


def get_fill_price(symbol: str, order_id: int) -> float:
    """查询成交均价"""
    res = signed_request("GET", "/fapi/v1/order",
                         {"symbol": symbol, "orderId": order_id}, quiet=True)
    if res and float(res.get("avgPrice", 0)) > 0:
        return float(res["avgPrice"])
    return 0.0


def get_open_orders(symbol: str) -> list[dict]:
    return signed_request("GET", "/fapi/v1/openOrders",
                          {"symbol": symbol}, quiet=True) or []


def cancel_all_orders(symbol: str):
    signed_request("DELETE", "/fapi/v1/allOpenOrders",
                   {"symbol": symbol}, quiet=True)


# ══════════════════════════════════════════════════════════════════════
#  🛒 通用下单原语
# ══════════════════════════════════════════════════════════════════════
def place_market(symbol: str, side: str, qty_str: str,
                 reduce_only: bool = False) -> dict | None:
    params = {
        "symbol":   symbol,
        "side":     side,
        "type":     "MARKET",
        "quantity": qty_str,
    }
    if reduce_only:
        params["reduceOnly"] = "true"
    return signed_request("POST", "/fapi/v1/order", params)


def place_limit_reduce(symbol: str, side: str, qty_str: str,
                       price_str: str) -> dict | None:
    params = {
        "symbol":      symbol,
        "side":        side,
        "type":        "LIMIT",
        "quantity":    qty_str,
        "price":       price_str,
        "timeInForce": "GTC",
        "reduceOnly":  "true",
    }
    return signed_request("POST", "/fapi/v1/order", params)


def place_stop_market(symbol: str, side: str, qty_str: str,
                      stop_price_str: str,
                      close_position: bool = False) -> dict | None:
    """
    挂 STOP_MARKET 止损单（标记价触发，避免插针扫损）。
    注意：closePosition=true 时，币安禁止同时传 reduceOnly / quantity，
    否则会被拒单（-1106 Parameter not required）。所以两种模式分开构造参数。
    """
    params = {
        "symbol":      symbol,
        "side":        side,
        "type":        "STOP_MARKET",
        "stopPrice":   stop_price_str,
        "workingType": "MARK_PRICE",     # 标记价触发，避免插针扫损
    }
    if close_position:
        params["closePosition"] = "true"  # 全仓止损，无需指定数量/reduceOnly
    else:
        params["reduceOnly"] = "true"
        params["quantity"]   = qty_str
    return signed_request("POST", "/fapi/v1/order", params)


# ══════════════════════════════════════════════════════════════════════
#  🛡️ 通用：ATR 动态止损挂单（供 ETH / DOGE 复用）
#  · 成功 → 返回 (True, 状态文案)
#  · 失败 → 记录币安真因到日志 + 发独立高优先级告警，仓位保留（不平仓）
# ══════════════════════════════════════════════════════════════════════
def place_atr_stop_loss(symbol: str, tag: str, tp_side: str, direction: str,
                        fill_price: float, qty_s: str,
                        atr_period: int, atr_mult: float,
                        sl_min_pct: float, sl_max_pct: float,
                        price_prec: int, interval: str,
                        max_retry: int) -> tuple[bool, str]:
    # 计算 ATR 动态止损距离，夹在 [MIN, MAX]：下限防噪音扫损，上限确保远离强平线
    highs, lows, atr_closes = fetch_ohlcv(symbol, interval)
    atr = calc_atr(highs, lows, atr_closes, atr_period)
    if atr and atr > 0:
        sl_dist_pct = (atr * atr_mult) / fill_price
        sl_dist_pct = max(sl_min_pct, min(sl_dist_pct, sl_max_pct))
        atr_note    = f"ATR={atr:.4f}×{atr_mult}"
    else:
        sl_dist_pct = sl_max_pct           # ATR 取不到 → 降级用上限，保证仍有止损
        atr_note    = "ATR不可用→用上限"
        logger.warning(f"{tag} ATR 计算失败，止损距离降级使用上限")

    sl_price   = (fill_price * (1 - sl_dist_pct) if direction == "long"
                  else fill_price * (1 + sl_dist_pct))
    sl_price_s = fmt_price(max(0.00001, sl_price), price_prec)

    for attempt in range(max_retry):
        sl_res = place_stop_market(symbol, tp_side, qty_s, sl_price_s,
                                   close_position=True)
        if is_order_ok(sl_res):
            logger.info(f"{tag} 止损挂单成功 | 价格={sl_price_s} | attempt={attempt+1}")
            return True, (f"🛡️ 止损已挂出，触发价 `{sl_price_s}`"
                          f"（距开仓 {sl_dist_pct*100:.2f}%，{atr_note}）")
        # 每次失败把真因写日志（_last_api_error 来自 signed_request）
        logger.warning(f"{tag} 止损挂单失败 attempt={attempt+1} | 原因={_last_api_error}")
        time.sleep(1)

    # 全部失败：绝不平仓（持仓可能正常，仅缺自动保护），记真因 + 独立高优先级告警
    logger.critical(f"{tag} 止损挂单全部失败 | 原因={_last_api_error} | 参考价={sl_price_s}")
    send_tg(
        f"🚨🚨 *{tag} 止损缺失告警*\n"
        f"仓位已开但**未挂上自动止损**，仓位保留（不会因挂单失败而平仓）\n"
        f"原因: `{_last_api_error}`\n"
        f"⚠️ 建议手工止损价：`{sl_price_s}`（距开仓 {sl_dist_pct*100:.2f}%）\n"
        f"请尽快登录币安手动设置\n{now_str()}"
    )
    sl_status = (f"🚨 止损挂单连续失败 {max_retry} 次，仓位缺少自动止损保护！\n"
                 f"原因: `{_last_api_error}`\n"
                 f"⚠️ 建议手工止损价：`{sl_price_s}`（距开仓 {sl_dist_pct*100:.2f}%），请尽快手动设置")
    return False, sl_status


# ══════════════════════════════════════════════════════════════════════
#  🟡 BTC 交易模块
#  · 动态仓位（余额×2.5）+ 限价止盈
#  · 止损：仅计算参考价后发 TG 提醒，不挂任何止损单，由人工处理
#  · 止盈挂不上 → 仅告警，仓位保留（不再紧急平仓）
# ══════════════════════════════════════════════════════════════════════
# BTC 运行时状态
_btc_cooldown_until:     datetime | None = None
_btc_last_known_pos:     float           = 0.0


def _btc_has_position(pos_amt: float) -> bool:
    """是否持有有效 BTC 仓位（灰尘仓位视为无仓，避免锁死信号与刷屏式防爆告警）"""
    return abs(pos_amt) > BTC_DUST_QTY


def _btc_open(side: str, rsi: float):
    """BTC 开仓主流程"""
    global _btc_cooldown_until, _btc_last_known_pos

    side_label = "抄底做多" if side == "BUY" else "摸顶做空"

    # ① 防多开：再次确认无持仓（灰尘仓位视为无仓）
    pos_amt, _, _ = get_position(BTC_SYMBOL)
    if _btc_has_position(pos_amt):
        msg = (f"🛡️ *BTC 防爆拦截*\n"
               f"RSI={rsi:.1f} 触发，但已持仓 `{pos_amt}`，拦截多开")
        send_tg(msg)
        logger.warning(f"BTC 防爆拦截 | RSI={rsi} | 持仓={pos_amt}")
        _btc_cooldown_until = datetime.now() + timedelta(seconds=BTC_TRADE_COOLDOWN)
        return

    # ② 获取余额 & 计算仓位
    balance = get_usdt_balance()
    if balance <= 0:
        print("  [BTC] 余额为0，跳过开仓")
        return

    price = get_mark_price(BTC_SYMBOL)
    if not price:
        print("  [BTC] 无法获取价格，跳过开仓")
        return

    target_notional = balance * BTC_LEVERAGE_MULT
    raw_qty         = target_notional / price
    final_qty_f     = floor_qty(raw_qty, _BTC_QTY_PREC)
    final_qty_s     = fmt_qty(final_qty_f, _BTC_QTY_PREC)

    if final_qty_f <= 0:
        print(f"  [BTC] 计算数量为0（余额={balance}），跳过")
        return

    logger.info(f"BTC 准备开单 | side={side} | qty={final_qty_s} | 余额={balance:.2f} | RSI={rsi}")

    # ③ 市价开仓
    order_res = place_market(BTC_SYMBOL, side, final_qty_s)
    if not is_order_ok(order_res):
        send_tg(f"❌ *BTC 开单失败*\nRSI={rsi:.1f} | 原因: `{_last_api_error}`")
        logger.error(f"BTC 开单失败 | 原因={_last_api_error} | 响应={order_res}")
        return

    logger.info(f"BTC 开单成功 | orderId={order_res.get('orderId')}")

    # ④ 获取真实成交价
    time.sleep(1)
    actual_entry = get_fill_price(BTC_SYMBOL, order_res["orderId"])
    if actual_entry <= 0:
        actual_entry = price
        logger.warning("BTC 未获取成交价，降级使用标记价")

    tp_side = "SELL" if side == "BUY" else "BUY"
    tp_price = (actual_entry * (1 + BTC_TAKE_PROFIT_PCT) if side == "BUY"
                else actual_entry * (1 - BTC_TAKE_PROFIT_PCT))
    tp_price_s = fmt_price(tp_price, _BTC_PRICE_PREC)

    # ⑤ 挂止盈（带重试）—— 失败不再紧急平仓，仓位保留
    tp_ok     = False
    tp_status = ""
    for attempt in range(BTC_MAX_TP_RETRY):
        tp_res = place_limit_reduce(BTC_SYMBOL, tp_side, final_qty_s, tp_price_s)
        if is_order_ok(tp_res):
            tp_status = f"🎯 止盈已挂出，目标价 `{tp_price_s}`"
            tp_ok     = True
            logger.info(f"BTC 止盈挂单成功 | 价格={tp_price_s}")
            break
        logger.warning(f"BTC 止盈挂单失败 attempt={attempt+1} | 原因={_last_api_error}")
        time.sleep(1)

    if not tp_ok:
        # 指标触发的开仓必须保留：止盈挂不上只告警、不平仓
        tp_status = (f"⚠️ 止盈连续失败 {BTC_MAX_TP_RETRY} 次（仓位保留，不平仓）\n"
                     f"原因: `{_last_api_error}`\n请关注并按需手工止盈/止损")
        logger.error(f"BTC 止盈全部失败 | 原因={_last_api_error}（仓位保留）")
        send_tg(f"⚠️ *BTC 止盈缺失*\n仓位 `{final_qty_s}` BTC 已开但止盈未挂上，仓位保留\n原因: `{_last_api_error}`\n{now_str()}")

    # ⑥ 计算止损参考价，仅发 TG 提醒，不挂单（手工止损）
    sl_price   = (actual_entry * (1 - BTC_STOP_LOSS_PCT) if side == "BUY"
                  else actual_entry * (1 + BTC_STOP_LOSS_PCT))
    sl_price_s = fmt_price(max(0.1, sl_price), _BTC_PRICE_PREC)
    sl_status  = f"⚠️ 建议手工止损价：`{sl_price_s}`（{BTC_STOP_LOSS_PCT*100:.0f}%）"
    logger.info(f"BTC 止损参考价={sl_price_s}（仅提醒，未挂单）")

    # ⑦ 推送战报
    msg = (
        f"⚡️ *BTC 战报*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"动作：{side_label}\n"
        f"触发 RSI：`{rsi:.1f}`\n"
        f"成交价：`{actual_entry:.1f}`\n"
        f"数量：`{final_qty_s}` BTC\n"
        f"名义：`~{target_notional:.0f} USDT`（余额×{BTC_LEVERAGE_MULT}）\n"
        f"{tp_status}\n"
        f"{sl_status}\n"
        f"时间：{now_str()}"
    )
    send_tg(msg)

    # 更新状态
    _btc_last_known_pos  = final_qty_f if side == "BUY" else -final_qty_f
    _btc_cooldown_until  = datetime.now() + timedelta(seconds=BTC_TRADE_COOLDOWN)
    print(f"  [BTC] ✅ {side_label} 完成 @ {actual_entry:.1f}")


def check_btc():
    """BTC 检查主入口"""
    global _btc_cooldown_until, _btc_last_known_pos

    # ① 冷却期跳过
    if _btc_cooldown_until and datetime.now() < _btc_cooldown_until:
        return

    # ② 被动检测止盈/止损是否已触发
    pos_amt, _, _ = get_position(BTC_SYMBOL)
    if _btc_last_known_pos != 0.0 and pos_amt == 0.0:
        logger.info(f"BTC 持仓归零检测：上轮={_btc_last_known_pos} → 本轮=0")
        send_tg(
            f"🔔 *BTC 持仓平仓检测*\n"
            f"上轮持仓: `{_btc_last_known_pos}`\n"
            f"当前持仓: `0`\n"
            f"原因: 止盈成交 / 止损触发 / 手动平仓\n"
            f"⏸️ 启动 {BTC_SL_COOLDOWN//60} 分钟保护性冷却"
        )
        _btc_cooldown_until = datetime.now() + timedelta(seconds=BTC_SL_COOLDOWN)
        _btc_last_known_pos = 0.0
        return
    _btc_last_known_pos = pos_amt

    # ③ 获取 RSI
    closes = fetch_closes(BTC_SYMBOL, BTC_INTERVAL)
    if not closes:
        return
    rsi = calc_rsi_ema(closes)   # BTC 原版用 ewm，保持一致
    if rsi is None or pd.isna(rsi):
        return

    print(f"  BTC 15m RSI = {rsi:.2f}", end="")

    # ④ 信号判断（灰尘仓位视为无仓）
    if rsi <= BTC_RSI_BUY and not _btc_has_position(pos_amt):
        print(" → 超卖，触发做多")
        _btc_open("BUY", rsi)
    elif rsi >= BTC_RSI_SELL and not _btc_has_position(pos_amt):
        print(" → 超买，触发做空")
        _btc_open("SELL", rsi)
    else:
        print()


# ══════════════════════════════════════════════════════════════════════
#  🔵 ETH 交易模块
#  · 恒定 $10,000 名义 + 限价止盈 + 反手逻辑
#  · 止损：自动挂 STOP_MARKET 单（ATR 动态、标记价触发、全仓平仓）
#  · 止盈/止损挂不上 → 仅告警，仓位保留（绝不因挂单失败而平仓）
# ══════════════════════════════════════════════════════════════════════
# ETH 运行时状态
_eth_cooldown_until: datetime | None = None
_eth_last_known_pos: float           = 0.0


def check_eth():
    """ETH 检查主入口（与 BTC check_btc 结构完全对齐）"""
    global _eth_cooldown_until, _eth_last_known_pos

    # ① 冷却期跳过
    if _eth_cooldown_until and datetime.now() < _eth_cooldown_until:
        return

    # ② 被动检测止盈/止损是否已触发
    pos_amt, pos_entry, pos_pnl = get_position(ETH_SYMBOL)
    if _eth_last_known_pos != 0.0 and pos_amt == 0.0:
        logger.info(f"ETH 持仓归零检测：上轮={_eth_last_known_pos} → 本轮=0")
        # 仓位已归零：撤掉残留的止盈或止损挂单，避免留下孤儿单
        cancel_all_orders(ETH_SYMBOL)
        send_tg(
            f"🔔 *ETH 持仓平仓检测*\n"
            f"上轮持仓: `{_eth_last_known_pos:.4f}`\n"
            f"当前持仓: `0`\n"
            f"原因: 止盈成交 / 止损触发 / 手动平仓\n"
            f"🧹 已自动撤销残留挂单\n"
            f"⏸️ 启动 {ETH_SL_COOLDOWN//60} 分钟保护性冷却"
        )
        _eth_cooldown_until = datetime.now() + timedelta(seconds=ETH_SL_COOLDOWN)
        _eth_last_known_pos = 0.0
        return
    _eth_last_known_pos = pos_amt

    # ③ 获取 RSI
    closes = fetch_closes(ETH_SYMBOL, ETH_INTERVAL)
    if not closes:
        return
    rsi = calc_rsi_wilder(closes, ETH_RSI_PERIOD)
    if rsi is None:
        return

    print(f"  ETH 15m RSI = {rsi:.2f}", end="")

    has_long  = pos_amt >  0.001
    has_short = pos_amt < -0.001

    # ④ 信号判断（含反手逻辑）
    if rsi < ETH_RSI_OVERSELL:
        if has_long:
            print(" → 同向多仓已存在，跳过")
            return
        if has_short:
            print(" → 超卖，反手：平空 → 开多")
            _eth_reverse(pos_amt, pos_pnl, "long", rsi)
        else:
            print(" → 超卖，触发做多")
            _eth_open("long", rsi)

    elif rsi > ETH_RSI_OVERBUY:
        if has_short:
            print(" → 同向空仓已存在，跳过")
            return
        if has_long:
            print(" → 超买，反手：平多 → 开空")
            _eth_reverse(pos_amt, pos_pnl, "short", rsi)
        else:
            print(" → 超买，触发做空")
            _eth_open("short", rsi)
    else:
        print()


def _eth_reverse(pos_amt: float, pos_pnl: float, new_direction: str, rsi: float):
    """ETH 反手：先市价平旧仓，成功后再开新仓（新仓不重置冷却计时）"""
    global _eth_last_known_pos

    has_long  = pos_amt > 0
    close_side = "SELL" if has_long else "BUY"
    cancel_all_orders(ETH_SYMBOL)
    close_res = place_market(ETH_SYMBOL, close_side,
                             fmt_qty(abs(pos_amt), _ETH_QTY_PREC),
                             reduce_only=True)
    if not is_order_ok(close_res):
        send_tg(f"🚨 *ETH 反手平仓失败，中止开仓！*\n原因: `{_last_api_error}`\n时间: {now_str()}")
        logger.error(f"ETH 反手平仓失败 | 原因={_last_api_error} | 响应={close_res}")
        return

    send_tg(
        f"🔄 *ETH 反手平旧仓*\n"
        f"旧方向: {'多' if has_long else '空'} | 浮盈: `{pos_pnl:+.2f}` USDT\n"
        f"时间: {now_str()}"
    )
    logger.info(f"ETH 反手平仓成功 | 旧持仓={pos_amt:.4f}")
    _eth_last_known_pos = 0.0
    time.sleep(2)

    # 反手开仓，标记 is_reversal=True 避免冷却被重置为30分钟
    _eth_open(new_direction, rsi, is_reversal=True)


def _eth_open(direction: str, rsi: float, is_reversal: bool = False):
    """
    ETH 开仓主流程：
      · 指标触发即开仓；开仓成功后仓位绝不因 TP/SL 挂单失败而被平掉
      · 止盈：限价单 ±0.5%，3次重试，失败→仅告警、仓位保留
      · 止损：STOP_MARKET，ATR 动态（夹在 1%~3%），标记价触发、3次重试
              失败→记录真因+告警、仓位保留
      · 交易后冷却 30 分钟；is_reversal=True 时（反手开仓）不重置冷却
    """
    global _eth_cooldown_until, _eth_last_known_pos

    side      = "BUY"  if direction == "long"  else "SELL"
    tp_side   = "SELL" if direction == "long"  else "BUY"   # 止盈/止损平仓方向
    dir_label = "做多 📈" if direction == "long" else "做空 📉"

    # ① 余额检查（保证金确实不足时无法开仓，这是物理限制，非风控拦截）
    balance  = get_usdt_balance()
    required = ETH_TRADE_USDT / ETH_LEVERAGE
    if balance < required * 1.05:
        send_tg(
            f"⚠️ *ETH 余额不足，无法开仓*\n"
            f"需要保证金: `${required:,.0f}`\n"
            f"可用余额: `${balance:,.0f}`\n"
            f"时间: {now_str()}"
        )
        logger.warning(f"ETH 余额不足 | 需要={required:.0f} | 可用={balance:.0f}")
        # 余额不足也进入交易冷却，避免每轮（2秒）刷屏式重复告警
        _eth_cooldown_until = datetime.now() + timedelta(seconds=ETH_TRADE_COOLDOWN)
        return

    # ② 获取标记价格
    price = get_mark_price(ETH_SYMBOL)
    if not price:
        print("  [ETH] 无法获取价格，跳过")
        return

    qty_f = ETH_TRADE_USDT / price
    qty_s = fmt_qty(qty_f, _ETH_QTY_PREC)
    if floor_qty(qty_f, _ETH_QTY_PREC) <= 0:
        print(f"  [ETH] 计算数量为0（名义={ETH_TRADE_USDT}，价={price}），跳过")
        logger.warning(f"ETH 计算数量为0 | 名义={ETH_TRADE_USDT} | 价={price}")
        return

    # ③ 取消旧挂单，市价开仓
    cancel_all_orders(ETH_SYMBOL)
    order_res = place_market(ETH_SYMBOL, side, qty_s)
    if not is_order_ok(order_res):
        send_tg(f"❌ *ETH 开单失败*\nRSI={rsi:.2f} | 原因: `{_last_api_error}`")
        logger.error(f"ETH 开单失败 | 原因={_last_api_error} | 响应={order_res}")
        return

    logger.info(f"ETH 开单成功 | orderId={order_res.get('orderId')}")

    # —— 开仓已成功，自此仓位一定保留，下面 TP/SL 全部为“尽力而为” ——

    # ④ 获取真实成交价
    time.sleep(1)
    fill_price = get_fill_price(ETH_SYMBOL, order_res["orderId"])
    if fill_price <= 0:
        fill_price = price
        logger.warning("ETH 未获取成交价，降级使用标记价")

    # ⑤ 计算止盈价（±0.5%，与 BTC 一致）
    tp_price   = (fill_price * (1 + ETH_TAKE_PROFIT_PCT) if direction == "long"
                  else fill_price * (1 - ETH_TAKE_PROFIT_PCT))
    tp_price_s = fmt_price(tp_price, _ETH_PRICE_PREC)

    # ⑥ 挂止盈限价单（3次重试，失败→仅告警、仓位保留、继续挂止损）
    tp_ok     = False
    tp_status = ""
    for attempt in range(ETH_MAX_TP_RETRY):
        tp_res = place_limit_reduce(ETH_SYMBOL, tp_side, qty_s, tp_price_s)
        if is_order_ok(tp_res):
            tp_status = f"🎯 止盈已挂出，目标价 `{tp_price_s}`"
            tp_ok     = True
            logger.info(f"ETH 止盈挂单成功 | 价格={tp_price_s} | attempt={attempt+1}")
            break
        logger.warning(f"ETH 止盈挂单失败 attempt={attempt+1} | 原因={_last_api_error}")
        time.sleep(1)

    if not tp_ok:
        # 指标触发的开仓必须保留：不平仓，仅告警，并继续尝试挂止损保护
        tp_status = (f"⚠️ 止盈连续失败 {ETH_MAX_TP_RETRY} 次（仓位保留，不平仓）\n"
                     f"原因: `{_last_api_error}`")
        logger.error(f"ETH 止盈全部失败 | 原因={_last_api_error}（仓位保留）")
        send_tg(f"⚠️ *ETH 止盈缺失*\n仓位 `{qty_s}` ETH 已开但止盈未挂上，仓位保留\n原因: `{_last_api_error}`\n{now_str()}")

    # ⑦ 挂 ATR 动态止损（失败→记录真因+告警、仓位保留，绝不平仓）
    sl_ok, sl_status = place_atr_stop_loss(
        ETH_SYMBOL, "ETH", tp_side, direction, fill_price, qty_s,
        ETH_ATR_PERIOD, ETH_SL_ATR_MULT, ETH_SL_MIN_PCT, ETH_SL_MAX_PCT,
        _ETH_PRICE_PREC, ETH_INTERVAL, ETH_MAX_TP_RETRY,
    )

    # ⑧ 推送战报
    margin_used = (qty_f * fill_price) / ETH_LEVERAGE
    msg = (
        f"{'🚀' if direction == 'long' else '🔻'} *ETH 战报*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"动作：{dir_label}\n"
        f"触发 RSI：`{rsi:.2f}`\n"
        f"成交价：`{fill_price:.2f}` USDT\n"
        f"数量：`{qty_s}` ETH\n"
        f"名义：`${qty_f * fill_price:,.0f}`（恒定 $10,000）\n"
        f"保证金：`${margin_used:,.0f}`（{ETH_LEVERAGE}x 杠杆）\n"
        f"{tp_status}\n"
        f"{sl_status}\n"
        f"时间：{now_str()}"
    )
    send_tg(msg)
    logger.info(f"ETH 开仓完成 | {dir_label} @ {fill_price:.2f} | 止盈成功={tp_ok} | 止损成功={sl_ok}")
    print(f"  [ETH] ✅ {dir_label} @ {fill_price:.2f}（止盈={tp_ok} 止损={sl_ok}）")

    # ⑨ 更新冷却状态（反手开仓时跳过，避免双重冷却）
    if not is_reversal:
        _eth_last_known_pos  = qty_f if direction == "long" else -qty_f
        _eth_cooldown_until  = datetime.now() + timedelta(seconds=ETH_TRADE_COOLDOWN)
    else:
        _eth_last_known_pos  = qty_f if direction == "long" else -qty_f


# ══════════════════════════════════════════════════════════════════════
#  🟠 DOGE 交易模块（双周期信号；结构对齐 ETH）
#  · DOGE_ENABLE_TRADE=False 时回退为“仅预警”，行为与旧版一致
#  · 入场条件：15m 与 1h RSI 同时确认（双周期），比单周期更稳健
#  · 止盈/止损/反手/冷却/归零检测 全部与 ETH 对齐
# ══════════════════════════════════════════════════════════════════════
# DOGE 运行时状态
_doge_cooldown_until: datetime | None  = None
_doge_last_known_pos: float            = 0.0
_doge_alert_cooldown: dict[str, float] = {}   # 仅预警模式下的 OB/OS 冷却


def _doge_alert_cooling(key: str) -> bool:
    return (time.time() - _doge_alert_cooldown.get(key, 0)) < DOGE_COOLDOWN_MIN * 60


def _doge_alert_set(key: str):
    _doge_alert_cooldown[key] = time.time()


def check_doge():
    """DOGE 双周期 RSI：交易模式（默认）或仅预警模式"""
    global _doge_cooldown_until, _doge_last_known_pos

    closes_fast = fetch_closes(DOGE_SYMBOL, DOGE_TF_FAST)
    closes_slow = fetch_closes(DOGE_SYMBOL, DOGE_TF_SLOW)
    if not closes_fast or not closes_slow:
        return

    rsi_fast = calc_rsi_wilder(closes_fast, DOGE_RSI_PERIOD)
    rsi_slow = calc_rsi_wilder(closes_slow, DOGE_RSI_PERIOD)
    if rsi_fast is None or rsi_slow is None:
        return

    print(f"  DOGE 15m RSI={rsi_fast:.2f} | 1h RSI={rsi_slow:.2f}", end="")

    is_ob = rsi_fast > DOGE_RSI_OB_FAST and rsi_slow > DOGE_RSI_OB_SLOW
    is_os = rsi_fast < DOGE_RSI_OS_FAST and rsi_slow < DOGE_RSI_OS_SLOW

    # ───────── 仅预警模式（保留旧行为）─────────
    if not DOGE_ENABLE_TRADE:
        if is_ob:
            print(" → 双周期超买预警")
            if not _doge_alert_cooling("OB"):
                send_tg(
                    f"🔴 *DOGE 双周期超买预警*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"15m RSI: `{rsi_fast:.2f}` > `{DOGE_RSI_OB_FAST}` ✅\n"
                    f"1h  RSI: `{rsi_slow:.2f}` > `{DOGE_RSI_OB_SLOW}` ✅\n"
                    f"⚠️ 短中期同步过热，注意做多风险\n"
                    f"时间: {now_str()}"
                )
                _doge_alert_set("OB")
        elif is_os:
            print(" → 双周期超卖预警")
            if not _doge_alert_cooling("OS"):
                send_tg(
                    f"🟢 *DOGE 双周期超卖预警*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"15m RSI: `{rsi_fast:.2f}` < `{DOGE_RSI_OS_FAST}` ✅\n"
                    f"1h  RSI: `{rsi_slow:.2f}` < `{DOGE_RSI_OS_SLOW}` ✅\n"
                    f"⚠️ 短中期同步超跌，关注反弹机会\n"
                    f"时间: {now_str()}"
                )
                _doge_alert_set("OS")
        else:
            print()
        return

    # ───────── 自动交易模式 ─────────
    # ① 冷却期跳过
    if _doge_cooldown_until and datetime.now() < _doge_cooldown_until:
        print(" (冷却中)")
        return

    # ② 被动检测止盈/止损是否已触发
    pos_amt, pos_entry, pos_pnl = get_position(DOGE_SYMBOL)
    if _doge_last_known_pos != 0.0 and pos_amt == 0.0:
        logger.info(f"DOGE 持仓归零检测：上轮={_doge_last_known_pos} → 本轮=0")
        cancel_all_orders(DOGE_SYMBOL)
        send_tg(
            f"🔔 *DOGE 持仓平仓检测*\n"
            f"上轮持仓: `{_doge_last_known_pos:.0f}`\n"
            f"当前持仓: `0`\n"
            f"原因: 止盈成交 / 止损触发 / 手动平仓\n"
            f"🧹 已自动撤销残留挂单\n"
            f"⏸️ 启动 {DOGE_SL_COOLDOWN//60} 分钟保护性冷却"
        )
        _doge_cooldown_until = datetime.now() + timedelta(seconds=DOGE_SL_COOLDOWN)
        _doge_last_known_pos = 0.0
        return
    _doge_last_known_pos = pos_amt

    has_long  = pos_amt >  0.5     # DOGE 数量精度通常为 0，最小 1 DOGE，0.5 作 dust 阈值
    has_short = pos_amt < -0.5

    # ③ 信号判断（含反手逻辑，与 ETH 一致）
    if is_os:
        if has_long:
            print(" → 同向多仓已存在，跳过")
            return
        if has_short:
            print(" → 双周期超卖，反手：平空 → 开多")
            _doge_reverse(pos_amt, pos_pnl, "long", rsi_fast, rsi_slow)
        else:
            print(" → 双周期超卖，触发做多")
            _doge_open("long", rsi_fast, rsi_slow)
    elif is_ob:
        if has_short:
            print(" → 同向空仓已存在，跳过")
            return
        if has_long:
            print(" → 双周期超买，反手：平多 → 开空")
            _doge_reverse(pos_amt, pos_pnl, "short", rsi_fast, rsi_slow)
        else:
            print(" → 双周期超买，触发做空")
            _doge_open("short", rsi_fast, rsi_slow)
    else:
        print()


def _doge_reverse(pos_amt: float, pos_pnl: float, new_direction: str,
                  rsi_fast: float, rsi_slow: float):
    """DOGE 反手：先市价平旧仓，成功后再开新仓（新仓不重置冷却计时）"""
    global _doge_last_known_pos

    has_long   = pos_amt > 0
    close_side = "SELL" if has_long else "BUY"
    cancel_all_orders(DOGE_SYMBOL)
    close_res = place_market(DOGE_SYMBOL, close_side,
                             fmt_qty(abs(pos_amt), _DOGE_QTY_PREC),
                             reduce_only=True)
    if not is_order_ok(close_res):
        send_tg(f"🚨 *DOGE 反手平仓失败，中止开仓！*\n原因: `{_last_api_error}`\n时间: {now_str()}")
        logger.error(f"DOGE 反手平仓失败 | 原因={_last_api_error} | 响应={close_res}")
        return

    send_tg(
        f"🔄 *DOGE 反手平旧仓*\n"
        f"旧方向: {'多' if has_long else '空'} | 浮盈: `{pos_pnl:+.2f}` USDT\n"
        f"时间: {now_str()}"
    )
    logger.info(f"DOGE 反手平仓成功 | 旧持仓={pos_amt:.0f}")
    _doge_last_known_pos = 0.0
    time.sleep(2)

    _doge_open(new_direction, rsi_fast, rsi_slow, is_reversal=True)


def _doge_open(direction: str, rsi_fast: float, rsi_slow: float,
               is_reversal: bool = False):
    """
    DOGE 开仓主流程（与 _eth_open 完全同构）：
      · 指标触发即开仓；开仓成功后仓位绝不因 TP/SL 挂单失败而被平掉
      · 止盈：限价单 ±0.5%，3次重试，失败→仅告警、仓位保留
      · 止损：STOP_MARKET，ATR 动态（夹在 1%~3%），标记价触发、3次重试，失败→告警、仓位保留
    """
    global _doge_cooldown_until, _doge_last_known_pos

    side      = "BUY"  if direction == "long"  else "SELL"
    tp_side   = "SELL" if direction == "long"  else "BUY"
    dir_label = "做多 📈" if direction == "long" else "做空 📉"

    # ① 余额检查
    balance  = get_usdt_balance()
    required = DOGE_TRADE_USDT / DOGE_LEVERAGE
    if balance < required * 1.05:
        send_tg(
            f"⚠️ *DOGE 余额不足，无法开仓*\n"
            f"需要保证金: `${required:,.0f}`\n"
            f"可用余额: `${balance:,.0f}`\n"
            f"时间: {now_str()}"
        )
        logger.warning(f"DOGE 余额不足 | 需要={required:.0f} | 可用={balance:.0f}")
        _doge_cooldown_until = datetime.now() + timedelta(seconds=DOGE_TRADE_COOLDOWN)
        return

    # ② 获取标记价格
    price = get_mark_price(DOGE_SYMBOL)
    if not price:
        print("  [DOGE] 无法获取价格，跳过")
        return

    qty_f = DOGE_TRADE_USDT / price
    qty_s = fmt_qty(qty_f, _DOGE_QTY_PREC)
    if floor_qty(qty_f, _DOGE_QTY_PREC) <= 0:
        print(f"  [DOGE] 计算数量为0（名义={DOGE_TRADE_USDT}，价={price}），跳过")
        return

    # ③ 取消旧挂单，市价开仓
    cancel_all_orders(DOGE_SYMBOL)
    order_res = place_market(DOGE_SYMBOL, side, qty_s)
    if not is_order_ok(order_res):
        send_tg(f"❌ *DOGE 开单失败*\n15m RSI={rsi_fast:.2f} 1h RSI={rsi_slow:.2f} | 原因: `{_last_api_error}`")
        logger.error(f"DOGE 开单失败 | 原因={_last_api_error} | 响应={order_res}")
        return

    logger.info(f"DOGE 开单成功 | orderId={order_res.get('orderId')}")

    # —— 开仓已成功，自此仓位一定保留，下面 TP/SL 全部为“尽力而为” ——

    # ④ 获取真实成交价
    time.sleep(1)
    fill_price = get_fill_price(DOGE_SYMBOL, order_res["orderId"])
    if fill_price <= 0:
        fill_price = price
        logger.warning("DOGE 未获取成交价，降级使用标记价")

    # ⑤ 止盈价
    tp_price   = (fill_price * (1 + DOGE_TAKE_PROFIT_PCT) if direction == "long"
                  else fill_price * (1 - DOGE_TAKE_PROFIT_PCT))
    tp_price_s = fmt_price(tp_price, _DOGE_PRICE_PREC)

    # ⑥ 挂止盈限价单（失败→仅告警、仓位保留）
    tp_ok     = False
    tp_status = ""
    for attempt in range(DOGE_MAX_TP_RETRY):
        tp_res = place_limit_reduce(DOGE_SYMBOL, tp_side, qty_s, tp_price_s)
        if is_order_ok(tp_res):
            tp_status = f"🎯 止盈已挂出，目标价 `{tp_price_s}`"
            tp_ok     = True
            logger.info(f"DOGE 止盈挂单成功 | 价格={tp_price_s} | attempt={attempt+1}")
            break
        logger.warning(f"DOGE 止盈挂单失败 attempt={attempt+1} | 原因={_last_api_error}")
        time.sleep(1)

    if not tp_ok:
        tp_status = (f"⚠️ 止盈连续失败 {DOGE_MAX_TP_RETRY} 次（仓位保留，不平仓）\n"
                     f"原因: `{_last_api_error}`")
        logger.error(f"DOGE 止盈全部失败 | 原因={_last_api_error}（仓位保留）")
        send_tg(f"⚠️ *DOGE 止盈缺失*\n仓位 `{qty_s}` DOGE 已开但止盈未挂上，仓位保留\n原因: `{_last_api_error}`\n{now_str()}")

    # ⑦ 挂 ATR 动态止损（失败→记录真因+告警、仓位保留）
    sl_ok, sl_status = place_atr_stop_loss(
        DOGE_SYMBOL, "DOGE", tp_side, direction, fill_price, qty_s,
        DOGE_ATR_PERIOD, DOGE_SL_ATR_MULT, DOGE_SL_MIN_PCT, DOGE_SL_MAX_PCT,
        _DOGE_PRICE_PREC, DOGE_TF_FAST, DOGE_MAX_TP_RETRY,
    )

    # ⑧ 推送战报
    margin_used = (qty_f * fill_price) / DOGE_LEVERAGE
    msg = (
        f"{'🚀' if direction == 'long' else '🔻'} *DOGE 战报*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"动作：{dir_label}\n"
        f"触发 RSI：15m `{rsi_fast:.2f}` / 1h `{rsi_slow:.2f}`\n"
        f"成交价：`{fill_price:.5f}` USDT\n"
        f"数量：`{qty_s}` DOGE\n"
        f"名义：`${qty_f * fill_price:,.0f}`（设定 ${DOGE_TRADE_USDT:,.0f}）\n"
        f"保证金：`${margin_used:,.0f}`（{DOGE_LEVERAGE}x 杠杆）\n"
        f"{tp_status}\n"
        f"{sl_status}\n"
        f"时间：{now_str()}"
    )
    send_tg(msg)
    logger.info(f"DOGE 开仓完成 | {dir_label} @ {fill_price:.5f} | 止盈成功={tp_ok} | 止损成功={sl_ok}")
    print(f"  [DOGE] ✅ {dir_label} @ {fill_price:.5f}（止盈={tp_ok} 止损={sl_ok}）")

    # ⑨ 更新冷却状态（反手开仓时跳过）
    if not is_reversal:
        _doge_last_known_pos = qty_f if direction == "long" else -qty_f
        _doge_cooldown_until = datetime.now() + timedelta(seconds=DOGE_TRADE_COOLDOWN)
    else:
        _doge_last_known_pos = qty_f if direction == "long" else -qty_f


# ══════════════════════════════════════════════════════════════════════
#  💓 24 小时心跳
# ══════════════════════════════════════════════════════════════════════
_next_heartbeat = datetime.now() + timedelta(hours=HEARTBEAT_HOURS)

def maybe_heartbeat():
    global _next_heartbeat
    if datetime.now() < _next_heartbeat:
        return
    _next_heartbeat = datetime.now() + timedelta(hours=HEARTBEAT_HOURS)

    balance = get_usdt_balance()

    # BTC 持仓
    btc_amt, btc_entry, btc_pnl = get_position(BTC_SYMBOL)
    if abs(btc_amt) > 0.0001:
        btc_pos = (f"{'多' if btc_amt > 0 else '空'} `{abs(btc_amt):.4f}` BTC\n"
                   f"   均价: `{btc_entry:.1f}` | 浮盈: `{btc_pnl:+.2f}` USDT")
    else:
        btc_pos = "空仓 💤"

    # ETH 持仓 & 止损
    eth_amt, eth_entry, eth_pnl = get_position(ETH_SYMBOL)
    eth_orders = get_open_orders(ETH_SYMBOL)
    eth_sl_info = "⚠️ 无止损单"
    for o in eth_orders:
        if o["type"] == "STOP_MARKET":
            eth_sl_info = f"止损 @ `{float(o['stopPrice']):.2f}`"
            break
    if abs(eth_amt) > 0.001:
        eth_pos = (f"{'多' if eth_amt > 0 else '空'} `{abs(eth_amt):.4f}` ETH\n"
                   f"   均价: `{eth_entry:.2f}` | 浮盈: `{eth_pnl:+.2f}` USDT\n"
                   f"   {eth_sl_info}")
    else:
        eth_pos = "空仓 💤"

    # DOGE 持仓 & 止损
    if DOGE_ENABLE_TRADE:
        doge_amt, doge_entry, doge_pnl = get_position(DOGE_SYMBOL)
        doge_orders = get_open_orders(DOGE_SYMBOL)
        doge_sl_info = "⚠️ 无止损单"
        for o in doge_orders:
            if o["type"] == "STOP_MARKET":
                doge_sl_info = f"止损 @ `{float(o['stopPrice']):.5f}`"
                break
        if abs(doge_amt) > 0.5:
            doge_pos = (f"{'多' if doge_amt > 0 else '空'} `{abs(doge_amt):.0f}` DOGE\n"
                        f"   均价: `{doge_entry:.5f}` | 浮盈: `{doge_pnl:+.2f}` USDT\n"
                        f"   {doge_sl_info}")
        else:
            doge_pos = "空仓 💤"
    else:
        doge_pos = "仅预警模式"

    msg = (
        f"💓 *24H 系统心跳*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"可用余额: `${balance:,.2f}` USDT\n\n"
        f"*BTC 持仓*\n{btc_pos}\n\n"
        f"*ETH 持仓*\n{eth_pos}\n\n"
        f"*DOGE 持仓*\n{doge_pos}\n\n"
        f"🤖 引擎运行正常\n"
        f"时间: {now_str()}"
    )
    send_tg(msg)
    logger.info(f"心跳 | 余额={balance:.2f} | BTC={btc_amt} | ETH={eth_amt}")
    print(f"  [心跳] 已发送")


# ══════════════════════════════════════════════════════════════════════
#  🚀 主循环
# ══════════════════════════════════════════════════════════════════════
def run():
    print("=" * 64)
    print("  币安合约 RSI 多标的机器人  V3.3")
    print("  BTC 自动交易 | ETH 自动交易 | DOGE " +
          ("自动交易" if DOGE_ENABLE_TRADE else "仅预警"))
    print("  原则：指标触发即开仓；止盈/止损挂单失败只告警、不平仓")
    print("=" * 64)
    logger.info("机器人启动")
    print("  初始化中...")
    init()

    doge_line = (
        f"*DOGE 自动交易*\n"
        f"  15m<{DOGE_RSI_OS_FAST} & 1h<{DOGE_RSI_OS_SLOW} → 做多 | "
        f"15m>{DOGE_RSI_OB_FAST} & 1h>{DOGE_RSI_OB_SLOW} → 做空\n"
        f"  仓位: ${DOGE_TRADE_USDT:,.0f} | {DOGE_LEVERAGE}x杠杆 | 止盈:{DOGE_TAKE_PROFIT_PCT*100:.1f}%\n"
        f"  止损: ATR 动态 STOP_MARKET（{DOGE_SL_MIN_PCT*100:.0f}%~{DOGE_SL_MAX_PCT*100:.0f}%）\n\n"
    ) if DOGE_ENABLE_TRADE else (
        f"*DOGE 预警*（不交易）\n"
        f"  15m>{DOGE_RSI_OB_FAST} & 1h>{DOGE_RSI_OB_SLOW} → 超买\n"
        f"  15m<{DOGE_RSI_OS_FAST} & 1h<{DOGE_RSI_OS_SLOW} → 超卖\n\n"
    )

    startup_msg = (
        f"🤖 *RSI 多标的机器人 V3.3 已上线*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚙️ 原则：指标触发即开仓；止盈/止损挂单失败只告警、不平仓\n\n"
        f"*BTC 自动交易*\n"
        f"  15m RSI ≤{BTC_RSI_BUY} → 做多 | ≥{BTC_RSI_SELL} → 做空\n"
        f"  仓位: 余额×{BTC_LEVERAGE_MULT} | 止盈:{BTC_TAKE_PROFIT_PCT*100:.1f}%\n"
        f"  止损: 仅提醒参考价 ±{BTC_STOP_LOSS_PCT*100:.0f}%，需人工手动操作\n\n"
        f"*ETH 自动交易*\n"
        f"  15m RSI <{ETH_RSI_OVERSELL} → 做多 | >{ETH_RSI_OVERBUY} → 做空\n"
        f"  仓位: 恒定 $10,000 | {ETH_LEVERAGE}x杠杆 | 止盈:{ETH_TAKE_PROFIT_PCT*100:.1f}%\n"
        f"  止损: ATR 动态 STOP_MARKET（{ETH_SL_MIN_PCT*100:.0f}%~{ETH_SL_MAX_PCT*100:.0f}%，标记价触发）\n\n"
        f"{doge_line}"
        f"轮询间隔: {POLL_INTERVAL}s"
    )
    print(startup_msg.replace("*", "").replace("`", ""))
    send_tg(startup_msg)

    while True:
        try:
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"\n[{ts}] ── 轮询开始 ──")

            check_btc()
            check_eth()
            check_doge()
            maybe_heartbeat()

            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            print("\n⏹ 收到停止信号")
            send_tg("⏹ *RSI 多标的机器人已手动停止*")
            logger.info("机器人手动停止")
            break
        except Exception as e:
            print(f"  ❌ 主循环异常: {e}")
            logger.exception(f"主循环异常: {e}")
            send_tg(f"⚠️ *主循环异常*\n`{e}`\n{now_str()}")
            time.sleep(15)


if __name__ == "__main__":
    run()
