# 最终实施优先级文档

**日期**：2026-03-30  
**前置状态**：P0 已完成并已验证  
**范围**：本阶段只落地 `fast_exit / time_exit / partial_tp / trailing_stop / entry_hard_gates` 的可执行部分  
**原则**：只保留可直接编码、可直接测试、可直接上线验证的结论

---

## 一、实施结论

### P1. 持仓退出链路重排

本阶段不采用“任意 2/4 投票”的 fast exit。改为：

1. `partial_tp` 优先于激进退出
2. `fast_exit` 只处理高置信失效
3. `time_exit` 处理“持仓超时但没有进展”
4. `slow_exit` 保留为兜底

**执行结论**：

- 盈利已达到 `1R` 且尚未触发首个 partial TP 时，禁止 fast_exit 直接全平
- fast_exit 首阶段只纳入以下高置信条件：
  - `direction_lock` 与持仓方向明确反向
  - `entry_signal_invalidated = true`
  - `trap_score` 高于高置信阈值且伴随至少一个流向转弱信号
- `time_exit` 第一阶段不按入场类型分叉，统一使用：
  - 持仓超过 30 分钟
  - 且浮盈低于 `0.35%`
  - 且 `cvd_momentum` 未继续朝持仓方向扩张
  - 满足则全平

### P1. 分批止盈与 trailing stop

本阶段采用“先 partial TP，再激活 trailing”的结构，不做更复杂的趋势分类。

**执行结论**：

- `partial_tp_levels`
  - `1R -> 减仓 30%`
  - `2R -> 再减仓 40%`
- trailing 不在首个 partial TP 之前激活
- trailing 距离使用 ATR 动态值，不再使用固定 `0.5%`
- 第一阶段参数：
  - `trailing_stop_atr_multiplier = 1.0`
  - `trailing_stop_min_distance = 0.007`
  - `trailing_stop_max_distance = 0.015`
- trailing 只做保护单收紧，不在策略层直接市价平仓

### P2. 入场 hard gates

本阶段不采用“9 项全 AND”，也不重复做已经在 score 里出现的重过滤。

**执行结论**：

- `entry_hard_gates` 只对新开仓生效，不影响已有持仓的平仓/减仓
- 调用顺序保持：
  - `score -> hard_gates -> entry decision`
- 结构采用三层：
  - `L1 结构层`: 必须全部满足
  - `L2 流向层`: 3 选 2
  - `L3 盘口层`: 3 选 2
- 第一阶段参数：
  - `ADX >= 22`
  - `ATR pct in [0.006, 0.020]`
  - `spread_bps <= 0.0008`
  - `flow_min_pass = 2`
  - `micro_min_pass = 2`

---

## 二、明确不做

本阶段先不做以下内容，避免把链路一次性改散：

- 不做按 `entry_type` 细分的 time_exit 观察期
- 不做 fast_exit 的复杂加权投票模型
- 不做 partial TP 参数的品种级差异化
- 不做 hard gates 的符号级动态阈值
- 不做回测参数搜索器或自动 ablation

---

## 三、代码落点

### 1. `src/fund_flow/decision_engine.py`

负责：

- `macd_v2` 新开仓的 `entry_hard_gates`
- `macd_v2` 的高置信 `fast_exit`

### 2. `src/app/fund_flow_bot.py`

负责：

- 持仓 `time_exit`
- `partial_tp` 状态管理
- `trailing_stop` 动态更新与保护单收紧
- 在真实执行链中保证：
  - `partial_tp` 不被 fast_exit 直接压制

---

## 四、上线前验收标准

- `entry_hard_gates` 开启后，低质量新开仓被阻断，但不会阻断已有持仓平仓
- 达到 `1R` 时先触发 `30% partial TP`，不会直接被 fast_exit 全平
- 达到 `2R` 时再触发 `40% partial TP`
- trailing 只会在 partial TP 之后开始收紧保护
- 30 分钟无进展的仓位会触发 `time_exit`
- `direction_lock` 明确反向的持仓会触发 `fast_exit`
- 所有新增逻辑都有针对性测试

---

## 五、最终优先级

1. `entry_hard_gates` 接到真实 `macd_v2` 开仓路径
2. `partial_tp` 落地并优先于激进退出
3. `trailing_stop` 改为 ATR 动态距离
4. `time_exit` 落地到持仓管理层
5. `fast_exit` 落地到高置信退出链路
