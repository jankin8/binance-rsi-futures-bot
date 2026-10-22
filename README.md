# Binance RSI Futures Bot V3.3

币安合约 RSI 多标的自动交易机器人 —— BTC / ETH / DOGE 三标的，15m RSI 极值触发，免费开源（MIT）。

> ⚠️ **风险提示（务必先读）**
> - 本项目仅供学习与研究，不构成任何投资建议。合约交易带杠杆，可能导致本金全部损失甚至强平。
> - 代码中的名义金额、杠杆（尤其 DOGE 的 $2,000 / 10x）为占位默认值，运行前务必按你的账户余额与风险偏好调整，并先用小额或测试网验证。
> - BTC 的止损仅为 Telegram 提醒参考价、需人工手动止损；ETH/DOGE 止损挂单失败时仓位会保留且缺少自动保护，请保持对告警的关注。
> - **绝不要把填了真实 API Key 的文件提交到 GitHub**。建议为 API Key 开启 IP 白名单且不要开启提现权限。

## 策略概览

### BTC（BTCUSDT 永续）
- 15m RSI ≤ 20 → 做多；≥ 80 → 做空
- 名义金额 = 账户余额 × 2.5，杠杆 10x
- 止盈：限价单 ±0.5%，3 次重试，失败保留仓位并告警
- 止损：仅 Telegram 提醒参考价（±5%），人工手动处理
- 持仓归零 → 冷却 1 小时；正常交易后冷却 30 分钟

### ETH（ETHUSDT 永续）
- 15m RSI < 17 → 做多；> 83 → 做空；名义恒定 $10,000，20x
- 止盈：限价单 ±0.5%，3 次重试
- 止损：自动 STOP_MARKET（ATR 动态 1%~3%，标记价触发）
- 反手：反向信号先平旧仓再开新仓
- 持仓归零 → 撤残单 + 冷却 1 小时

### DOGE（DOGEUSDT 永续，双周期确认）
- 15m RSI < 15 且 1h RSI < 20 → 做多；15m > 85 且 1h > 80 → 做空
- 止盈 / 止损 / 反手与 ETH 一致；`DOGE_ENABLE_TRADE=False` 可切回仅预警模式

### 公共能力
- Telegram 推送（Markdown 失败自动降级纯文本）
- 文件日志 `rsi_bot_v3.log`（含挂单失败时币安返回的真实 code+msg）
- 24 小时心跳播报（三标的持仓状态）
- 签名请求统一 3 次重试，失败原因落盘
- 数量 / 价格精度启动时从交易所自动拉取

## 快速开始

```bash
pip install -r requirements.txt
# 编辑 rsi_bot_v3.py 顶部的必填配置：BINANCE_API_KEY / BINANCE_SECRET_KEY / TG_BOT_TOKEN / TG_CHAT_ID
python rsi_bot_v3.py
```

## 配置说明
全部参数都在 `rsi_bot_v3.py` 顶部按标的分组（BTC / ETH / DOGE / 全局），注释齐全，改完保存重启即可生效。

## 项目结构
```
rsi_bot_v3.py      # 主程序（单文件，V3.3）
requirements.txt   # requests, pandas
tests/             # RSI / ATR / 格式化与持仓判零的单元测试（不连网）
.env.example       # 环境变量模板（不要提交填了真实密钥的文件）
CHANGELOG.md       # 版本更新记录
.github/           # CI（语法检查+单元测试）与 Dependabot 配置
```

## 环境变量（推荐）
不想把密钥写进代码文件时，可以用环境变量（见 `.env.example`）：
`BINANCE_API_KEY`、`BINANCE_SECRET_KEY`、`TG_BOT_TOKEN`、`TG_CHAT_ID`、`ENABLE_TG`。
环境变量优先于代码顶部的同名配置，两种方式选其一即可。

## 测试
```bash
python -m unittest discover -s tests -v
```
覆盖 RSI / ATR 计算与数量、价格格式化，不连网、不需要密钥。

## License
MIT — 见 [LICENSE](LICENSE)
