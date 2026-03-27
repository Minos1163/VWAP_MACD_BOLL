# Live Trading Convergence TODO

日期: 2026-03-27
基于: `live_trading_convergence_plan_20260327.md`
目标: 将实盘链路向 `backtest_macd_v2.py` 收敛，并补齐可验证的配置与监控

## TODO

- [x] 将 `pretrade_risk_gate` 改为 hard-rules-only 模式，关闭 CVD veto，并仅保留极端 ATR / 资金占用 / 现有持仓深度回撤三类硬规则
- [x] 为 `entry_window` 增加显式 `enabled` 配置支持，避免因缺省逻辑误伤入场机会集
- [x] 放宽 `protection_sla` 强平逻辑，增加 `pnl_grace_threshold` 与 `api_health_check_before_force`
- [x] 补齐 alpha 稀释漏斗埋点: `v2_engine_raw -> after_entry_window -> after_signal_pool -> after_ma10_macd -> after_pretrade_gate -> after_capacity_check -> actually_executed`
- [x] 在 live production 配置中显式关闭 `MA10/MACD` 外层硬过滤
- [x] 在 live production 配置中显式关闭 `DCA` 与 `winner pyramiding`
- [x] 在 live production 配置中显式关闭 `entry_window`
- [x] 在 live production 配置中显式关闭 CVD 相关外层 veto / context filter
- [x] 在 live production 配置中显式写入与回测一致的关键阈值: `long_open_threshold / short_open_threshold / close_threshold / stop_loss_pct / take_profit_pct / entry_slippage / reverse_close_confirm_bars / max_active_symbols`
- [x] 新增 `validate_live_backtest_alignment.py`，用于部署前对齐检查
- [ ] 运行一轮小资金实盘观察 alpha 稀释日志，确认主要损耗层不再集中在 outer filters
- [ ] 基于 24h / 72h 实盘日志决定是否需要二阶段重新引入 soft filter

## 已落地文件

- `src/app/fund_flow_bot.py`
- `config/trading_config_fund_flow_live_production.json`
- `scripts/validate_live_backtest_alignment.py`

## 验证命令

```powershell
python -m py_compile src/app/fund_flow_bot.py scripts/validate_live_backtest_alignment.py
python scripts/validate_live_backtest_alignment.py
```
