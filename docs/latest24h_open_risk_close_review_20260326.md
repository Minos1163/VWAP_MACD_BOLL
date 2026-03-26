# 最新24H日志审计（开仓/风控/平仓）

**生成时间**：`2026-03-26 16:36 +08`  
**目标窗口（UTC）**：`2026-03-25 08:16:17` ~ `2026-03-26 08:16:17`  
**实际覆盖（UTC）**：`2026-03-25 08:30:03` ~ `2026-03-26 06:45:14`（最新日志只到 `06:45`）  
**审计入口**：`python3 src/main.py --config config/trading_config_fund_flow.json`

---

## 1. 核心结论（先看）

1. **开仓供给明显过载，容量成为第一瓶颈**  
   `3/3` 持仓上限拦截达到 **103 次**，发生在 **52/90 个周期（57.8%）**。
2. **AI终审在本窗口全部“拦截”**  
   AI终审请求 **29 次**，全部给出 `reason=entry_score<0.080 (0.000)`，无一次 `OPEN`。
3. **非 HOLD 决策总量不大，但方向高度偏空**  
   决策侧：`BUY=2, SELL=27, CLOSE=3`，其中 `SELL` 占绝对主导。
4. **风控触发集中在 CIRCUIT_EXIT，且带“降级减仓”机制**  
   风控摘要共 **231** 条，`state=CIRCUIT_EXIT` 仅 **3** 条（`ALGOUSDT*2, ICPUSDT*1`），并出现 `CIRCUIT_EXIT降级为REDUCE`。
5. **手续费可统计，但当前成交日志不适合直接算胜率/PF**  
   最新窗口成交 `46` 笔，手续费合计约 `0.824322 USDT`；`trade_fills_utc.csv` 中 `已实现盈亏` 本窗口为 `0`，无法用它直接评估胜率。

---

## 2. 数据范围与样本

### 2.1 使用文件

- `logs/2026-03/2026-03-25/fund_flow_attribution.jsonl`
- `logs/2026-03/2026-03-26/fund_flow_attribution.jsonl`
- `logs/2026-03/2026-03-25/runtime.out.06.log`
- `logs/2026-03/2026-03-25/runtime.out.12.log`
- `logs/2026-03/2026-03-25/runtime.out.18.log`
- `logs/2026-03/2026-03-26/runtime.out.00.log`
- `logs/2026-03/2026-03-26/runtime.out.06.log`
- `logs/2026-03/2026-03-25/trade_fills_utc.csv`
- `logs/2026-03/2026-03-26/trade_fills_utc.csv`

### 2.2 关键统计

#### A) attribution（决策+执行）

- 窗口内记录数：`4456`
- 决策事件：
  - `HOLD=2196`
  - `BUY=2`
  - `SELL=27`
  - `CLOSE=3`
- 执行事件：
  - `HOLD:noop=2196`
  - `BUY:pending=2`
  - `SELL:pending=27`
  - `CLOSE:pending=2, CLOSE:success=1`

#### B) 3/3容量拦截

- `候选开仓被跳过：持仓交易对已满(3/3)`：**103 次**
- 触发周期：`52/90`（**57.8%**）
- 单周期最高拦截：`7` 次（`2026-03-26 05:15 UTC`）
- 被拦截Top：
  - `ZROUSDT 21`
  - `ETCUSDT 15`
  - `BCHUSDT 14`
  - `TONUSDT 12`
  - `ICPUSDT 10`
  - `ZECUSDT 8`

#### C) AI终审（平仓以外候选）

- `AI终审请求`：`29`
- `AI终审建议`：`29`
- 建议结果：全部为 `reason=entry_score<0.080 (0.000)`，无 `OPEN`
- 请求方向：`SELL=27, BUY=2`

#### D) 风控摘要与平仓

- `🧪 风控摘要`：`231` 条
  - `state=HOLD`: `228`
  - `state=CIRCUIT_EXIT`: `3`
- `RISK_PROTECT` 触发平仓：`3`（`ALGOUSDT*2, ICPUSDT*1`）
- 典型日志：
  - `logs/2026-03/2026-03-26/runtime.out.00.log:1066`
  - `logs/2026-03/2026-03-26/runtime.out.00.log:1247`
  - `logs/2026-03/2026-03-26/runtime.out.00.log:1464`
  - `logs/2026-03/2026-03-25/runtime.out.06.log:4055`

#### E) 成交与费用（fills）

- 成交笔数：`46`
- 方向：`卖出 44` / `买入 2`
- 手续费合计：`0.824322 USDT`
- 成交额合计：`1648.64409 USDT`
- `已实现盈亏`合计：`0`（该文件口径不足以做胜率/PF判断）

---

## 3. 开仓链路梳理（代码+日志对照）

### 3.1 执行顺序（关键）

1. 各 symbol 先产出 `decision` 放入 `pending_new_entries`。  
2. 在 `_finalize_entries` 里先拆分：
   - `close_candidates`
   - `open_candidates`
3. 合并执行顺序是：`close_candidates + open_candidates`（平仓优先）。  
4. 对开仓候选按 `score` 降序排序。  
5. 先做容量门控：若 `active_count >= max_active_symbols` 则直接跳过（打印 `3/3` 日志）。  
6. 通过容量后，若满足 AI 终审条件，再做 AI 终审与 `_ai_entry_guard`。  
7. 最后 `_execute_and_log_decision` 执行并记录。

代码定位：

- 容量门控与 3/3 日志：`src/app/fund_flow_bot.py:8490-8502`
- AI终审请求/建议：`src/app/fund_flow_bot.py:8524-8599`
- AI终审打分门槛：`src/app/fund_flow_bot.py:1508-1514`
- 候选执行入口：`src/app/fund_flow_bot.py:8605-8616`

### 3.2 为什么会出现“开仓很多但被挡很多”

本窗口内，“可视化到日志的开仓尝试”保守估算：

- 已执行开仓（BUY+SELL）：`29`
- AI终审拦截：`29`
- 3/3容量拦截：`103`

合计约 `161` 次尝试，拦截占比约 **81.99%**。  
其中容量拦截占比约 **63.98%**，说明当前更像是“候选供给过多 + 仓位槽位紧张”。

---

## 4. 风控链路梳理（CIRCUIT_EXIT -> REDUCE -> CLOSE）

### 4.1 链路步骤

1. 每轮持仓评估输出 `🧪 风控摘要`（含 `state`、`mfe/mae`、`hold`）。  
2. 当 `risk_state in (EXIT, CIRCUIT_EXIT)`，进入硬保护路径。  
3. 若满足“小MAE软化条件”，触发 `CIRCUIT_EXIT降级为REDUCE`，先减仓而非一步全平。  
4. 通过最短持仓、方向评估节流后，生成 `FundFlowOperation.CLOSE`，并 `bypass_capacity_guard=True`、`bypass_ai_final_review=True`，保证风控单优先落地。

代码定位：

- 硬保护与软化：`src/app/fund_flow_bot.py:7833-7860`
- CIRCUIT_EXIT状态处理：`src/app/fund_flow_bot.py:7871-7899`
- 生成 RISK_PROTECT 平仓决策：`src/app/fund_flow_bot.py:7943-7971`

### 4.2 本窗口观察

- `CIRCUIT_EXIT` 共 3 次，均伴随 `RISK_PROTECT` 平仓原因。
- 有“先减仓 pending，再最终平仓 success”的过程（ALGO案例）。

---

## 5. 平仓链路梳理（可审计字段）

平仓执行时会输出：

- `平仓前后仓位快照`（pre/post/exch_live/status）
- `回合摘要`（`hold_min`、`mfe`、`mae`、`exit_reason`）

代码定位：

- `src/app/fund_flow_bot.py:6166-6216`

本窗口样例：

- `logs/2026-03/2026-03-25/runtime.out.06.log:4055-4062`（ICP）
- `logs/2026-03/2026-03-26/runtime.out.00.log:1247-1254`（ALGO 第一次）
- `logs/2026-03/2026-03-26/runtime.out.00.log:1464-1471`（ALGO 第二次）

---

## 6. 是否需要更严格开仓门槛（供专家组讨论）

结论：**建议收紧，但应“门槛+容量+节流”组合微调，不建议只改单一阈值。**

理由：

1. 当前问题不只是“开得多”，更是“高频候选在 3/3 槽位处堆积”。  
2. AI终审在本窗口全部拒绝，说明 shortlist 候选质量偏低（至少在 AI 口径下如此）。  
3. 风控层能兜底，但兜底发生时已进入持仓阶段，成本高于前置过滤。

### 6.1 候选参数组（仅讨论稿，未落地）

#### 方案A（胜率优先，交易频次明显下降）

- `fund_flow.max_active_symbols: 3 -> 2`
- `fund_flow.engine_params.TREND.long_open_threshold: 0.095 -> 0.11`
- `fund_flow.engine_params.TREND.short_open_threshold: 0.08 -> 0.10`
- `fund_flow.macd_mtf_strategy_v2.entry_thresholds.min_signal_score: 0.85 -> 0.88`
- `fund_flow.macd_mtf_strategy_v2.entry_filters.min_vwap_score_for_entry: 0.12 -> 0.14`
- `fund_flow.ai_review.final_min_score: 0.08 -> 0.10`

#### 方案B（平衡型，优先减少“候选拥堵”）

- `fund_flow.max_active_symbols: 保持 3`
- `fund_flow.engine_params.TREND.short_open_threshold: 0.08 -> 0.09`
- `fund_flow.macd_mtf_strategy_v2.entry_filters.min_vwap_score_for_entry: 0.12 -> 0.13`
- `risk.conflict_protection.directional_eval_interval_seconds: 60 -> 120`（减少高频反复风控抖动）
- `fund_flow.ai_review.final_min_score: 0.08 -> 0.09`

#### 方案C（容量不改，先做“拥堵治理”）

- 保持当前开仓阈值
- 新增“候选拥堵节流”：当连续 N 个周期出现 `3/3` 阻挡时，临时上调 `open_threshold` 或仅保留 top1 候选执行
- 观察 48h 后再决定是否抬高长期阈值

---

## 7. 本次分析限制

1. `trade_fills_utc.csv` 的 `已实现盈亏` 在本窗口为 0，不能据此计算胜率/PF。  
2. 最新日志止于 `2026-03-26 06:45 UTC`，距离目标 24H 末端尚有约 `1h31m` 空档。  
3. 本文结论聚焦“链路与拥堵结构”，不替代完整回测/前向验证。

---

## 8. 给专家组的讨论问题

1. 在当前“3/3拥堵率 57.8%”下，是否先降槽位（3->2）还是先提信号阈值？  
2. AI终审全拒绝是否代表 shortlist 质量不足，还是 AI gate 与本地评分体系尚未对齐？  
3. 风控 `CIRCUIT_EXIT` 的软化减仓比例（35%）是否要分品种动态化（高波动币更高）？
