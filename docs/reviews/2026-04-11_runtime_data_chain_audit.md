# 运行时数据链路排障与日志字段标准

日期: 2026-04-11

## 代码链路结论

当前仓库代码下，`missing_tf_data` 不能简单归因为“fund_flow_bot 完全没有主动拉 1h/4h 数据”。

已确认链路:

1. [fund_flow_bot.py](/D:/AIDCA/AI8/src/app/fund_flow_bot.py)
   - `get_market_data_for_symbol()` 会按当前规则主动请求:
     - `primary_timeframe`
     - `entry_timeframe`
     - `regime_timeframe`
     - `dual_risk_timeframe`（启用 4h 风险过滤时）
   - 结果写入 `trend_filters_by_timeframe`
   - `_apply_timeframe_context()` 会把这些快照注入 `flow_context["timeframes"]`

2. [market_data.py](/D:/AIDCA/AI8/src/data/market_data.py)
   - `get_trend_filter_metrics()` 当前明确返回:
     - `macd_hist_array`
     - `macd_hist_series`
     - `macd_hist_prev`
     - `close_series`
     - `vwap_series`

3. [decision_engine.py](/D:/AIDCA/AI8/src/fund_flow/decision_engine.py)
   - `macd_v2` 和旧 `macd_mtf` 路径都会优先读取:
     - `macd_hist_series`
     - `macd_hist_array`
   - 若没有 series，再回退到:
     - `macd_hist`
     - `macd_hist_prev`

## 故障分类

### A. 配置未加载 / 进程未重启

特征:
- 启动日志仍打印旧门槛
- 当前仓库配置与运行日志不一致
- 同一时间窗里策略行为完全保留旧语义

判定方式:
- 看启动日志的 `策略阈值`
- 看实际运行进程启动时间
- 对比配置文件修改时间与实例启动时间

### B. `timeframes` 注入失败或对应 timeframe 快照为空

特征:
- `macd_v2_missing_tf_data`
- 某个 timeframe `timeframe_present = false`

典型原因:
- `get_trend_filter_metrics(symbol, interval)` 返回空 dict
- 请求失败、缓存空洞、限流或外部数据异常

### C. 多时间框架数据存在，但 1H 评分被逻辑压成 0

特征:
- `macd_tf_diagnostics` 显示 `series_source = series` 或 `fallback_pair`
- 但 `score_1h = 0`
- `score_1h_source = no_direction_credit`

结论:
- 这是逻辑结果，不是数据缺失

## 新增诊断字段标准

### Decision metadata

新增字段:

```json
"macd_tf_diagnostics": {
  "15m": {
    "timeframe_present": true,
    "series_source": "series|array|fallback_pair|missing",
    "series_len": 60,
    "macd_hist_present": true,
    "macd_hist_prev_present": true,
    "close_series_len": 60
  },
  "1h": { ... },
  "4h": { ... }
}
```

语义:
- `timeframe_present`
  - `true`: `timeframes[tf]` 是非空 dict
  - `false`: timeframe 快照本身缺失
- `series_source`
  - `series`: 来自 `macd_hist_series`
  - `array`: 来自 `macd_hist_array`
  - `fallback_pair`: 只有 `macd_hist/macd_hist_prev`
  - `missing`: 上述字段都没有

### MACD V2 debug

新增字段:

```json
"score_1h_source": "neutral_allowed_light_credit|signal_type_flip|signal_type_growing|aligned_direction_strength|no_direction_credit"
```

语义:
- `neutral_allowed_light_credit`
  - 4H 主方向 + 允许 neutral 1H + light confirmation 生效
- `signal_type_flip`
  - 1H 来自 flip 类信号
- `signal_type_growing`
  - 1H 来自 growing 类信号
- `aligned_direction_strength`
  - 1H 方向与 trade direction 一致，按强度给分
- `no_direction_credit`
  - 数据存在，但逻辑上不给 1H 分

## Runtime 日志标准

当前运行时新增一行:

```text
MACD_V2时框诊断: score_1h_source=...,
15m=series/len=60/hist=1/prev=1 |
1h=series/len=60/hist=1/prev=1 |
4h=fallback_pair/len=2/hist=1/prev=1
```

输出条件:
- `score_1h <= 0`
- 或任一 timeframe 的 `series_source != series`
- 或 `missing_tf_data` 场景

目的:
- 一眼区分“数据缺失”与“逻辑不给分”

## 最小回归测试集

已新增/锁定:

1. [tests/test_fund_flow_timeframe_context.py](/D:/AIDCA/AI8/tests/test_fund_flow_timeframe_context.py)
   - `get_trend_filter_metrics()` 必须返回 `macd_hist_series / macd_hist_array / macd_hist_prev`

2. [tests/test_fund_flow_decision_engine.py](/D:/AIDCA/AI8/tests/test_fund_flow_decision_engine.py)
   - `macd_v2_missing_tf_data` 必须携带 `macd_tf_diagnostics`
   - 正常 `macd_v2` 决策也必须携带 `macd_tf_diagnostics`

3. [tests/test_macd_strategy_v2_4h_scoring.py](/D:/AIDCA/AI8/tests/test_macd_strategy_v2_4h_scoring.py)
   - `neutral 1H + light confirmation` 必须记录 `score_1h_source = neutral_allowed_light_credit`

## 本轮结论

- 当前仓库的运行时数据链路已经具备注入 1h/4h `macd_hist_series` 的能力
- 若后续日志仍出现 `missing_tf_data`，优先排查运行时数据获取失败，而不是假设代码根本没拉多周期数据
- 若后续日志出现 `1H=0.0000(-)`，必须同时看:
  - `macd_tf_diagnostics`
  - `score_1h_source`
  才能判断是数据问题还是逻辑结果
