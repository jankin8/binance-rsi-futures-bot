# Changelog

## Unreleased — 维护改进（2026-10-23）
- 修复：止盈挂单改按"实际成交持仓"而非"请求数量"下单（新增 `_tp_sl_qty_s`）：市价单部分成交时，reduceOnly 止盈单数量若超过实际持仓会被币安 -2022 拒单，导致仓位失去止盈保护；持仓查询失败时回退到请求数量（旧行为）
- 持仓跟踪（`_btc_last_known_pos` / `_eth_last_known_pos` / `_doge_last_known_pos`）同步改用实际成交量，归零检测更准确
- 测试：补 `_tp_sl_qty_s` 单元测试（部分成交 / 全额成交 / 持仓未知回退 / 反手余量 / 零精度）

## Unreleased — 维护改进（2026-10-22）
- 修复：BTC 防多开与信号判断改用灰尘仓位阈值（`BTC_DUST_QTY=0.0005`），残留灰尘仓位不再锁死信号、也不再刷屏"防爆拦截"告警；新增 `_btc_has_position` 并补单元测试
- 修复：`_eth_open` 补零数量下单保护（与 BTC / DOGE 一致），数量截断为 0 时直接跳过并记日志
- 文档：README 项目结构章节补全 tests / .env.example / CHANGELOG / .github 说明

## Unreleased — 维护改进（2026-10-21）
- 配置：`BINANCE_API_KEY` / `BINANCE_SECRET_KEY` / `TG_BOT_TOKEN` / `TG_CHAT_ID` / `ENABLE_TG` 现在也支持环境变量（见 `.env.example`），代码内填写方式保持兼容
- 新增 `tests/test_indicators.py`：RSI（Wilder / EMA）、ATR、数量/价格格式化的单元测试，不连网、不需要密钥
- `requirements.txt` 补最低版本下限（requests>=2.31.0, pandas>=2.0.0）
- 新增 Dependabot 配置（pip 与 GitHub Actions 月度检查）

## V3.3 — 初始开源版本（2026-10-21）
- BTC：15m RSI ≤20 做多 / ≥80 做空，名义=余额×2.5，限价止盈 ±0.5%，止损仅 TG 提醒
- ETH：15m RSI <17 做多 / >83 做空，恒定 $10,000，ATR 动态 STOP_MARKET 止损，支持反手
- DOGE：15m+1h 双周期 RSI 确认，止盈/止损/反手与 ETH 一致，可切换仅预警
- 公共：Telegram 推送降级、文件日志含失败真因、24h 心跳、签名请求 3 次重试、精度自动拉取
