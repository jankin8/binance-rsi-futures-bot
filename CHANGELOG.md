# Changelog

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
