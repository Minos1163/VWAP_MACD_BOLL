# V3 策略可执行修改方案
## 技术实施文档 · 含 JSON Diff + Python 伪代码

> **文件性质**：基于专家组确认方案（v1.1）的可执行技术实施文档
> **执行原则**：按 Phase 分阶段推进，每个 Phase 必须独立回测验证后才进入下一 Phase
> **代码风格**：伪代码以 Python diff 形式表示（`+` 新增，`-` 删除，` ` 保留）

---

## 目录

1. [Phase 1：锁定已验证主线（可立即执行）](#phase-1锁定已验证主线可立即执行)
2. [Phase 2A：强趋势杠杆小步测试](#phase-2a强趋势杠杆小步测试)
3. [Phase 2B：静态持仓槽扩容测试](#phase-2b静态持仓槽扩容测试)
4. [Phase 3A：空头质量过滤器（需新增代码）](#phase-3a空头质量过滤器需新增代码)
5. [Phase 3B：赢家加仓机制（需新增代码）](#phase-3b赢家加仓机制需新增代码)
6. [Phase 3C：时间窗口过滤（需新增代码）](#phase-3c时间窗口过滤需新增代码)
7. [Phase 3D：Symbol 级信号 Override（需新增代码）](#phase-3dsymbol-级信号-override需新增代码)
8. [回测验证标准](#回测验证标准)
9. [回滚方案](#回滚方案)

---

## Phase 1：锁定已验证主线（可立即执行）

> **状态**：已通过统一时间轴回测验证（`universe-only` → `+82.70%`）
> **风险**：极低，纯配置修改，无代码改动

### 1.1 Config JSON Diff

```diff
// trading_config_fund_flow.json

  "trading": {
    "symbols": [
-     "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT",
-     "TRXUSDT", "DOTUSDT", "SHIBUSDT", "LTCUSDT",
-     "BCHUSDT", "UNIUSDT", "NEARUSDT", "ATOMUSDT",
-     "ICPUSDT", "HBARUSDT", "VETUSDT", "FILUSDT",
-     "OPUSDT", "ARBUSDT", "MKRUSDT", "INJUSDT",
-     "TIAUSDT", "AAVEUSDT",
-     "GRTUSDT", "RUNEUSDT", "STXUSDT", "ALGOUSDT", "MINAUSDT",
-     "ENSUSDT", "THETAUSDT", "FETUSDT", "RNDRUSDT",
-     "AGIXUSDT", "WOOUSDT", "PEPEUSDT"
+     "SOLUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT",
+     "TRXUSDT", "DOTUSDT", "LTCUSDT",
+     "BCHUSDT", "NEARUSDT", "ATOMUSDT",
+     "ICPUSDT", "VETUSDT",
+     "OPUSDT", "ARBUSDT", "MKRUSDT", "INJUSDT",
+     "TIAUSDT", "AAVEUSDT",
+     "GRTUSDT", "RUNEUSDT", "STXUSDT", "ALGOUSDT", "MINAUSDT",
+     "ENSUSDT", "THETAUSDT", "FETUSDT",
+     "AGIXUSDT", "WOOUSDT"
+     // 移除：UNIUSDT, HBARUSDT, FILUSDT, DOGEUSDT
+     // 移除：SHIBUSDT, RNDRUSDT, PEPEUSDT（数据口径问题，待修正后单独验证）
    ],
```

```diff
  "fund_flow": {
    // 保持以下参数不变（已验证为最优主线）
    "max_active_symbols": 3,              // 不变
    "take_profit_pct": 0.0,              // 不变
    "breakeven_enabled": true,           // 不变
    "breakeven_trigger_pnl_ratio": 0.003, // 不变，不改为 0.005
    "breakeven_lock_ratio": 0.001,       // 不变，不改为 0.002
    "ema_strong_trend_leverage_mult": 0.8, // 不变，Phase 2 再测
    "dca_max_additions": 0,              // 不变，Phase 3 再做

    "macd_mtf_strategy_v2": {
      "signal_config": {
        "disable_flip_bullish_entries": true,       // 不变
        "disable_green_bar_growing_entries": true,  // 不变
        "enable_flip_bullish_strict_filter": true,  // 不变
        "min_signal_score": 0.85                   // 不变
      }
    }
  }
```

### 1.2 验证步骤

```bash
# Step 1：备份当前配置
cp trading_config_fund_flow.json trading_config_fund_flow_baseline_v3.json

# Step 2：应用 Phase 1 修改
cp trading_config_fund_flow_phase1_universe_only_20260319.json \
   trading_config_fund_flow.json

# Step 3：运行统一时间轴回测，验证结果与已知结果一致
python run_backtest.py \
  --config trading_config_fund_flow.json \
  --start "2026-02-08 02:15:00" \
  --end "2026-03-18 13:00:00" \
  --output output/backtest/phase1_verify_$(date +%Y%m%d_%H%M%S).json

# Step 4：验收标准（必须全部满足）
# - 收益率 ≥ +78%（允许 ±5% 的重跑误差）
# - 盈利因子 ≥ 8.0
# - 最大回撤 ≤ -5%
# - 总交易数在 120～145 之间
```

### 1.3 Backtest Profile 口径说明（2026-03-19 已验证）

> **结论**：由于当前回测缓存缺少 `funding_rate / oi_delta_ratio` 字段，`short_quality_filter` 在历史回放中会对 `flip_bearish` 形成口径偏紧。  
> **处理**：回测默认 profile 已切换为 `macd_v2_disable_short_filter`，仅对 backtest 临时关闭空头质量过滤器；实盘主配置不受影响。

#### A/B 对照结果

| Profile | short filter | 收益率 | 总交易数 | 胜率 | 盈利因子 | `flip_bearish` 交易数 | 结论 |
|--------|--------------|-------:|---------:|-----:|---------:|---------------------:|------|
| `macd_v2_disable_short_filter` | 关闭 | +82.70% | 132 | 72.7% | 11.28 | 34 | 作为当前回测基线 |
| `macd_v2_full_filters` | 开启 | +61.89% | 89 | 68.5% | 12.98 | 0 | 作为严格过滤对照组 |

#### 运行方式

```bash
# 默认：走当前 default_profile（已切到 macd_v2_disable_short_filter）
python scripts/backtest_macd_v2.py \
  --config config/trading_config_fund_flow.json

# 对照组：显式启用完整过滤器
python scripts/backtest_macd_v2.py \
  --config config/trading_config_fund_flow.json \
  --profile macd_v2_full_filters

# 批量对比：同时跑多个 profile 并输出汇总表
python scripts/diagnostics/compare_backtest_profiles.py \
  --config config/trading_config_fund_flow.json
```

#### 配置位置

```diff
  "fund_flow": {
    "backtest": {
-     "default_profile": ""
+     "default_profile": "macd_v2_disable_short_filter",
      "profiles": {
+       "macd_v2_disable_short_filter": {
+         "config_overrides": {
+           "fund_flow": {
+             "macd_mtf_strategy_v2": {
+               "short_quality_filter": {
+                 "enabled": false
+               }
+             }
+           }
+         }
+       },
+       "macd_v2_full_filters": {
+         "config_overrides": {
+           "fund_flow": {
+             "macd_mtf_strategy_v2": {
+               "short_quality_filter": {
+                 "enabled": true
+               }
+             }
+           }
+         }
+       }
      }
    }
  }
```

### 1.4 Live 调度语义更新（2026-03-19）

> **目的**：避免 `symbols_per_cycle=7` 这类轮询截断导致同一开仓窗口漏扫 symbol，从而错失入场信号。  
> **结论**：live 调度已改为“默认每轮全扫 + symbol 间隔节流”；若未来需要进一步压缩 API 压力，使用“同窗分批扫完”而不是“跨轮轮转截断”。

#### 新语义

| 配置项 | 旧语义 | 新语义 |
|-------|--------|--------|
| `symbols_per_cycle = 0` | 未明确 | 每轮全扫全部 symbol |
| `symbols_per_cycle > 0` | 每轮只扫前 N 个 / 轮转 N 个 | 作为单轮 `batch_size`，但仍在同一开仓窗口内扫完整个 universe |
| `symbol_stagger_seconds` | symbol 间固定 delay | 保持不变，作为主要节流手段 |
| `max_cycle_runtime_seconds = 0` | 未明确 | 禁用单轮预算截断，避免另一种“隐式降载” |

#### 当前建议配置

```diff
  "schedule": {
-   "symbols_per_cycle": 7,
+   "symbols_per_cycle": 0,
    "symbols_per_cycle_prioritize_positions": true,
-   "max_cycle_runtime_seconds": 45,
+   "max_cycle_runtime_seconds": 0,
-   "symbol_stagger_seconds": 0.15,
+   "symbol_stagger_seconds": 0.2,
+   "symbols_batch_pause_seconds": 0.0
  }
```

#### 新增观测项

- 控制台每轮结束打印：
  - `processed=已处理/总数`
  - `elapsed=单轮耗时`
  - `api=200/4xx/429/异常名`
- 同时落盘到 UTC 日志：
  - `logs/YYYY-MM/YYYY-MM-DD/api_cycle_stats_utc.jsonl`
- 目的：
  - 先用真实运行数据判断是否触碰 Binance 限流
  - 若 `429`、`5xx` 或超时明显上升，再切换到“同窗分批”模式

#### 后续扩展原则

```text
优先级顺序：
1. 每轮全扫
2. symbol_stagger_seconds 控节奏
3. 观察 api_cycle_stats_utc.jsonl
4. 如确有压力，再将 symbols_per_cycle 调成 8/10/12，
   配合 symbols_batch_pause_seconds，在同一开仓窗口内分批扫完整个 universe
```

---

## Phase 2A：强趋势杠杆小步测试

> **状态**：候选参数，需独立回测验证
> **前提**：Phase 1 验收完成
> **注意**：只测 `1.0`，结果稳定再测 `1.1`，不直接跳到 `1.2`

### 2A.1 Config JSON Diff（第一步：1.0）

```diff
  "fund_flow": {
-   "ema_strong_trend_leverage_mult": 0.8,
+   "ema_strong_trend_leverage_mult": 1.0,  // 从"强趋势降杠杆"改为"不干预"
  }
```

### 2A.2 涉及的代码逻辑（不需改代码，只需理解当前实现）

```python
# 当前代码位置：fund_flow/leverage_calculator.py（示意路径）

def calculate_final_leverage(base_leverage: float,
                              ema_multiplier: float,
                              config: dict) -> float:
    """
    当前实现：评分 → base_leverage，再由 EMA 乘数二次修正
    """
    # base_leverage 由评分决定：
    #   score >= 0.90 -> 4x
    #   score >= 0.85 -> 3x
    #   score >= 0.75 -> 2x

    ema_structure = get_ema_structure()   # 返回 "strong"/"normal"/"weak"

    if ema_structure == "strong" and ema_multiplier >= 1.2:
        # 当前配置 0.8：强趋势 4x * 0.8 = 3.2x -> 实际用 3x（向下取整）
        # 测试配置 1.0：强趋势 4x * 1.0 = 4x  -> 实际用 4x（不变）
        # 测试配置 1.1：强趋势 4x * 1.1 = 4.4x -> cap 到 max_leverage=4x
        adjusted = base_leverage * config["ema_strong_trend_leverage_mult"]
    else:
        adjusted = base_leverage

    # 最终受 min/max 约束
    return max(
        config["min_leverage"],
        min(config["max_leverage"], int(adjusted))
    )
```

### 2A.3 回测对比矩阵

```
测试序列（每次只改一个参数，基于 Phase 1 universe 重跑）：

Run A：ema_strong_trend_leverage_mult = 0.8  (基线，已知 +82.70%)
Run B：ema_strong_trend_leverage_mult = 1.0  (第一步测试)
Run C：ema_strong_trend_leverage_mult = 1.1  (仅在 Run B 通过后才测)

验收标准（Run B 相对 Run A）：
  - 收益率提升 or 持平（允许 ±3%）
  - 最大回撤不恶化超过 +2%（绝对值）
  - 盈利因子不低于 8.0 的 80%（即 ≥ 6.4）
  - 胜率不低于 65%

若 Run B 不满足验收标准：停止，不进入 Run C，保留 0.8
若 Run B 满足验收标准：进入 Run C
```

---

## Phase 2B：静态持仓槽扩容测试

> **状态**：候选参数，需独立回测验证
> **前提**：Phase 2A 完成（无论结果如何）
> **注意**：扩容后每个 symbol 的仓位比例同步下调

### 2B.1 Config JSON Diff

```diff
  "fund_flow": {
-   "max_active_symbols": 3,
+   "max_active_symbols": 4,

    // 注意：扩容后单 symbol 目标仓位需同步调整
    // 原逻辑：总资金 × 60% / 3 symbols ≈ 20% per symbol
    // 新逻辑：总资金 × 60% / 4 symbols ≈ 15% per symbol
    // 这由 default_target_portion 和 max_symbol_position_portion 联动控制
    // 当前配置 max_symbol_position_portion = 0.60 足够，无需改动
  }
```

### 2B.2 扩容后的仓位计算验证（伪代码）

```python
# 验证扩容不破坏仓位安全约束

def verify_position_safety(config: dict, n_symbols: int = 4):
    """
    验证 max_active_symbols = 4 时的仓位安全性
    """
    max_per_symbol = config["fund_flow"]["max_symbol_position_portion"]  # 0.60
    reserve = config["fund_flow"].get("reserve_percent", 20) / 100       # 0.20
    deployable = 1.0 - reserve                                            # 0.80

    # 最坏情况：4 个 symbol 全部满仓
    worst_case_exposure = n_symbols * max_per_symbol  # 4 * 0.60 = 2.40x (杠杆后名义)

    # 实际净敞口（考虑 default_target_portion）
    target_per_symbol = config["fund_flow"]["default_target_portion"]     # 0.60
    # 注意：这是按当前余额的比例，不是账户总资金
    # 4 个 symbol 同时持仓时，系统是否会超出 deployable？
    # 关键验证：是否存在"4 个信号同时满足且每个都开到 60%"的极端情况

    print(f"max_active_symbols: {n_symbols}")
    print(f"最差情况名义敞口: {worst_case_exposure:.1f}x 账户资金")
    print(f"实际受 max_leverage={config['trading']['max_leverage']}x 约束")
    print(f"reserve_percent: {reserve*100:.0f}%（固定保留，不参与交易）")

    # 验收：单次开仓不能让总杠杆超过账户 × max_leverage
    assert worst_case_exposure <= n_symbols * config["trading"]["max_leverage"]

verify_position_safety(config, n_symbols=4)
```

### 2B.3 回测对比标准

```
Run D：max_active_symbols = 3（Phase 1 主线，已知 +82.70%）
Run E：max_active_symbols = 4（测试）

验收标准（Run E 相对 Run D）：
  - 收益率提升 ≥ +3%（扩容应带来增量机会）
  - 最大回撤不恶化超过 +2%（绝对值）
  - 最差单日亏损不低于 Run D 的 150%
  - 盈利因子 ≥ 7.0

若不满足：保留 max_active_symbols = 3
```

---

## Phase 3A：空头质量过滤器（需新增代码）

> **状态**：需要新增策略代码后才能测试
> **前提**：Phase 1 + Phase 2 全部完成并稳定

### 3A.1 新增模块：`short_quality_filter.py`

```python
# 新增文件：src/strategy/filters/short_quality_filter.py

+ from dataclasses import dataclass
+ from typing import Optional
+
+
+ @dataclass
+ class ShortQualityFilterConfig:
+     """空头质量过滤器配置"""
+     enabled: bool = False
+
+     # Funding Rate 阈值
+     require_positive_funding: bool = True
+     min_funding_rate: float = 0.0005     # 0.05%（年化约 54%）
+
+     # OI Delta 阈值
+     require_oi_delta_negative: bool = True
+
+     # VWAP 位置
+     require_price_above_vwap: bool = True
+     min_price_vwap_ratio: float = 1.005  # 价格 > VWAP × 1.005
+
+
+ class ShortQualityFilter:
+     """
+     flip_bearish 独立前置过滤器
+     只在 1H 信号类型为 flip_bearish 时调用
+     """
+
+     def __init__(self, config: ShortQualityFilterConfig):
+         self.config = config
+
+     def should_allow(
+         self,
+         signal_type: str,
+         funding_rate: Optional[float],
+         oi_delta_ratio: Optional[float],
+         price: float,
+         vwap: float,
+     ) -> tuple[bool, str]:
+         """
+         返回 (是否允许入场, 拒绝原因)
+         """
+         # 只对 flip_bearish 生效
+         if signal_type != "flip_bearish":
+             return True, ""
+
+         if not self.config.enabled:
+             return True, ""
+
+         # 条件1：Funding Rate 必须为正且超过阈值
+         if self.config.require_positive_funding:
+             if funding_rate is None:
+                 return False, "funding_rate 数据缺失，拒绝空头入场"
+             if funding_rate <= self.config.min_funding_rate:
+                 return False, (
+                     f"funding_rate={funding_rate:.6f} "
+                     f"未达到阈值 {self.config.min_funding_rate:.6f}"
+                 )
+
+         # 条件2：OI Delta 必须为负（多头在减仓）
+         if self.config.require_oi_delta_negative:
+             if oi_delta_ratio is None:
+                 return False, "oi_delta_ratio 数据缺失，拒绝空头入场"
+             if oi_delta_ratio >= 0:
+                 return False, (
+                     f"oi_delta_ratio={oi_delta_ratio:.4f} >= 0，"
+                     f"OI 未下降，空头质量不足"
+                 )
+
+         # 条件3：价格在 VWAP 上方（有均值回归空间）
+         if self.config.require_price_above_vwap:
+             price_vwap_ratio = price / vwap if vwap > 0 else 1.0
+             if price_vwap_ratio < self.config.min_price_vwap_ratio:
+                 return False, (
+                     f"price/vwap={price_vwap_ratio:.4f} "
+                     f"< {self.config.min_price_vwap_ratio}，"
+                     f"价格不够高，空头入场质量差"
+                 )
+
+         return True, ""
```

### 3A.2 在信号决策层插入过滤器

```python
# 修改文件：src/strategy/macd_mtf_strategy_v2.py
# 在 generate_signal() 方法中插入

  def generate_signal(self, context: SignalContext) -> Optional[TradingSignal]:

      # === 原有逻辑：1H 方向检测 ===
      signal_1h = self._detect_1h_direction(context.hist_1h_current,
                                             context.hist_1h_prev)
      if signal_1h is None:
          return None

      direction = signal_1h.direction
      signal_type = signal_1h.signal_type

      # === 原有逻辑：禁用信号检查 ===
      if self._is_signal_disabled(signal_type):
          return None

+     # === 新增：空头质量过滤器（仅对 flip_bearish 生效）===
+     if signal_type == "flip_bearish":
+         allowed, reason = self.short_quality_filter.should_allow(
+             signal_type=signal_type,
+             funding_rate=context.funding_rate,
+             oi_delta_ratio=context.oi_delta_ratio,
+             price=context.close_1h,
+             vwap=context.vwap_1h,
+         )
+         if not allowed:
+             self.logger.debug(f"[ShortQualityFilter] 拒绝 flip_bearish: {reason}")
+             return None

      # === 原有逻辑：EMA 结构 / VWAP / 评分 / 入场 ===
      ema_result = self._evaluate_ema_structure(context, direction)
      vwap_score = self._calculate_vwap_score(context, direction)
      # ... 其余不变
```

### 3A.3 Config 新增字段

```diff
  "fund_flow": {
    "macd_mtf_strategy_v2": {
+     "short_quality_filter": {
+       "enabled": false,            // 初始关闭，回测验证后再打开
+       "require_positive_funding": true,
+       "min_funding_rate": 0.0005,
+       "require_oi_delta_negative": true,
+       "require_price_above_vwap": true,
+       "min_price_vwap_ratio": 1.005
+     }
    }
  }
```

---

## Phase 3B：赢家加仓机制（需新增代码）

> **状态**：需要新增策略代码，不复用现有 DCA 字段
> **前提**：Phase 1 + Phase 2 全部完成并稳定

### 3B.1 新增模块：`winner_pyramiding.py`

```python
# 新增文件：src/strategy/position/winner_pyramiding.py

+ from dataclasses import dataclass
+ from typing import Optional
+
+
+ @dataclass
+ class WinnerPyramidingConfig:
+     """赢家加仓配置（与现有 DCA 完全独立）"""
+     enabled: bool = False
+
+     # 触发条件
+     min_unrealized_pnl_pct: float = 0.003  # 浮盈 ≥ 0.3%
+     require_signal_type: str = "red_bar_growing"
+     allowed_ema_structures: list = None     # ["strong", "normal"]
+     min_vwap_score: float = 0.10
+     min_signal_score: float = 0.85
+
+     # 加仓参数
+     max_additions: int = 1                  # 最多一次
+     addition_ratio: float = 0.50            # 加仓量 = 初始仓位 × 0.5
+
+     def __post_init__(self):
+         if self.allowed_ema_structures is None:
+             self.allowed_ema_structures = ["strong", "normal"]
+
+
+ class WinnerPyramiding:
+     """
+     赢家加仓管理器
+     只在浮盈 + 信号持续确认时才允许加仓
+     与现有 drawdown DCA 语义完全不同，不复用同一配置
+     """
+
+     def __init__(self, config: WinnerPyramidingConfig):
+         self.config = config
+         self._addition_count: dict[str, int] = {}  # {symbol: addition_count}
+
+     def should_add(
+         self,
+         symbol: str,
+         unrealized_pnl_pct: float,
+         signal_type_1h: str,
+         ema_structure: str,
+         vwap_score: float,
+         signal_score: float,
+         position_side: str,  # "long" or "short"
+     ) -> tuple[bool, str]:
+         """
+         判断是否允许加仓
+         返回 (是否加仓, 原因)
+         """
+         if not self.config.enabled:
+             return False, "winner_pyramiding 未启用"
+
+         # 只加多头（空头侧不做赢家加仓）
+         if position_side != "long":
+             return False, "当前版本只对多头做赢家加仓"
+
+         # 次数限制
+         current_count = self._addition_count.get(symbol, 0)
+         if current_count >= self.config.max_additions:
+             return False, f"已达最大加仓次数 {self.config.max_additions}"
+
+         # 条件1：必须处于浮盈
+         if unrealized_pnl_pct < self.config.min_unrealized_pnl_pct:
+             return False, (
+                 f"浮盈 {unrealized_pnl_pct:.4f} "
+                 f"< 阈值 {self.config.min_unrealized_pnl_pct}"
+             )
+
+         # 条件2：1H 信号仍为指定类型（趋势延续）
+         if signal_type_1h != self.config.require_signal_type:
+             return False, (
+                 f"当前 1H 信号 {signal_type_1h} "
+                 f"!= 要求 {self.config.require_signal_type}"
+             )
+
+         # 条件3：EMA 结构仍支持
+         if ema_structure not in self.config.allowed_ema_structures:
+             return False, (
+                 f"EMA 结构 {ema_structure} "
+                 f"不在允许列表 {self.config.allowed_ema_structures}"
+             )
+
+         # 条件4：VWAP 位置未过热
+         if vwap_score < self.config.min_vwap_score:
+             return False, (
+                 f"VWAP 分 {vwap_score:.3f} "
+                 f"< 阈值 {self.config.min_vwap_score}"
+             )
+
+         # 条件5：综合评分仍达门槛
+         if signal_score < self.config.min_signal_score:
+             return False, (
+                 f"综合评分 {signal_score:.3f} "
+                 f"< 阈值 {self.config.min_signal_score}"
+             )
+
+         return True, "所有赢家加仓条件满足"
+
+     def record_addition(self, symbol: str):
+         """记录加仓成功，更新计数器"""
+         self._addition_count[symbol] = self._addition_count.get(symbol, 0) + 1
+
+     def reset_symbol(self, symbol: str):
+         """持仓关闭后重置计数器"""
+         self._addition_count.pop(symbol, None)
```

### 3B.2 在持仓管理层接入

```python
# 修改文件：src/fund_flow/position_manager.py

  def on_position_update(self, symbol: str, position: Position,
                          market_context: MarketContext):
      """每次市场数据更新时调用"""

      if not position.is_open:
          # 持仓关闭，重置赢家加仓计数器
+         self.winner_pyramiding.reset_symbol(symbol)
          return

+     # === 新增：赢家加仓检查 ===
+     signal_context = self._build_signal_context(symbol, market_context)
+     should_add, reason = self.winner_pyramiding.should_add(
+         symbol=symbol,
+         unrealized_pnl_pct=position.unrealized_pnl_pct,
+         signal_type_1h=signal_context.signal_type_1h,
+         ema_structure=signal_context.ema_structure,
+         vwap_score=signal_context.vwap_score,
+         signal_score=signal_context.total_score,
+         position_side=position.side,
+     )
+
+     if should_add:
+         addition_size = position.initial_size * \
+                         self.winner_pyramiding.config.addition_ratio
+
+         # 检查不超过 max_symbol_position_portion
+         projected_total = position.current_size + addition_size
+         max_allowed = self.account_balance * \
+                       self.config["max_symbol_position_portion"]
+
+         if projected_total <= max_allowed:
+             self._execute_winner_addition(symbol, addition_size)
+             self.winner_pyramiding.record_addition(symbol)
+             self.logger.info(
+                 f"[WinnerPyramiding] {symbol} 赢家加仓 "
+                 f"+{addition_size:.4f}，当前浮盈 "
+                 f"{position.unrealized_pnl_pct:.3%}"
+             )
+         else:
+             self.logger.debug(
+                 f"[WinnerPyramiding] {symbol} 加仓被拒："
+                 f"超出单 symbol 最大仓位 {max_allowed:.2f}"
+             )

      # === 原有逻辑：保本、动态止损等 ===
      self._check_breakeven(position)
      self._update_trailing_stop(position)
```

### 3B.3 Config 新增字段

```diff
  "fund_flow": {
    "macd_mtf_strategy_v2": {
+     "winner_pyramiding": {
+       "enabled": false,
+       "min_unrealized_pnl_pct": 0.003,
+       "require_signal_type": "red_bar_growing",
+       "allowed_ema_structures": ["strong", "normal"],
+       "min_vwap_score": 0.10,
+       "min_signal_score": 0.85,
+       "max_additions": 1,
+       "addition_ratio": 0.50
+     }
    }
  }
```

---

## Phase 3C：时间窗口过滤（需新增代码）

> **状态**：需要新增代码，不是现成配置项
> **前提**：Phase 1 完成

### 3C.1 新增模块：`time_window_filter.py`

```python
# 新增文件：src/strategy/filters/time_window_filter.py

+ from datetime import datetime, timezone
+ from typing import Optional
+ from dataclasses import dataclass, field
+
+
+ @dataclass
+ class TimeWindowFilterConfig:
+     enabled: bool = False
+     timezone: str = "UTC"
+     # UTC 小时白名单（空列表 = 不过滤）
+     allowed_hours_utc: list[int] = field(default_factory=list)
+     # 默认建议值（来自 backtest profile 分析）
+     # 排除：01, 02（亚洲深夜低流动）
+     # 排除：11（欧洲午休前）
+     # 排除：13（欧洲午休）
+     # 排除：17, 18（美欧交接）
+
+
+ class TimeWindowFilter:
+
+     def __init__(self, config: TimeWindowFilterConfig):
+         self.config = config
+
+     def should_allow_entry(
+         self,
+         timestamp: Optional[datetime] = None
+     ) -> tuple[bool, str]:
+
+         if not self.config.enabled:
+             return True, ""
+
+         if not self.config.allowed_hours_utc:
+             return True, ""
+
+         if timestamp is None:
+             timestamp = datetime.now(timezone.utc)
+
+         # 统一转 UTC
+         if timestamp.tzinfo is None:
+             timestamp = timestamp.replace(tzinfo=timezone.utc)
+         utc_hour = timestamp.astimezone(timezone.utc).hour
+
+         if utc_hour not in self.config.allowed_hours_utc:
+             return False, (
+                 f"当前 UTC 小时 {utc_hour} 不在允许交易窗口内，跳过入场"
+             )
+
+         return True, ""
```

### 3C.2 在入场决策层插入

```python
# 修改文件：src/strategy/macd_mtf_strategy_v2.py

  def generate_signal(self, context: SignalContext) -> Optional[TradingSignal]:

+     # === 新增：时间窗口过滤（最先执行，减少后续计算浪费）===
+     allowed, reason = self.time_window_filter.should_allow_entry(
+         timestamp=context.current_time
+     )
+     if not allowed:
+         self.logger.debug(f"[TimeWindowFilter] {reason}")
+         return None

      # === 原有逻辑 ===
      signal_1h = self._detect_1h_direction(...)
      # ...
```

### 3C.3 Config 新增字段

```diff
  "fund_flow": {
    "macd_mtf_strategy_v2": {
+     "time_window_filter": {
+       "enabled": false,
+       "allowed_hours_utc": [
+         0, 3, 4, 5, 6, 7, 8, 9, 10,
+         12, 14, 15, 16,
+         19, 20, 21, 22, 23
+       ]
+     }
    }
  }
```

---

## Phase 3D：Symbol 级信号 Override（需新增代码）

> **状态**：需要新增代码，当前只支持全局开关
> **用途**：为 ATOMUSDT / SOLUSDT 等白名单 symbol 有条件恢复 `flip_bullish`
> **前提**：单币种独立回测验证 `flip_bullish` 有稳定 edge 之后

### 3D.1 新增模块：`symbol_signal_override.py`

```python
# 新增文件：src/strategy/config/symbol_signal_override.py

+ from dataclasses import dataclass, field
+ from typing import Optional
+
+
+ @dataclass
+ class SymbolSignalOverride:
+     """单 symbol 的信号配置覆盖"""
+     symbol: str
+     disable_flip_bullish: Optional[bool] = None    # None = 继承全局配置
+     disable_green_bar_growing: Optional[bool] = None
+     min_signal_score_override: Optional[float] = None
+
+
+ class SymbolSignalOverrideRegistry:
+     """
+     symbol 级信号 override 注册表
+     只覆盖显式指定的字段，其余继承全局配置
+     """
+
+     def __init__(self, overrides: list[dict], global_config: dict):
+         self.global_config = global_config
+         self._registry: dict[str, SymbolSignalOverride] = {}
+
+         for item in overrides:
+             symbol = item["symbol"]
+             self._registry[symbol] = SymbolSignalOverride(
+                 symbol=symbol,
+                 disable_flip_bullish=item.get("disable_flip_bullish"),
+                 disable_green_bar_growing=item.get("disable_green_bar_growing"),
+                 min_signal_score_override=item.get("min_signal_score_override"),
+             )
+
+     def is_signal_disabled(self, symbol: str, signal_type: str) -> bool:
+         """
+         判断指定 symbol 的指定信号是否被禁用
+         symbol 级配置优先于全局配置
+         """
+         override = self._registry.get(symbol)
+
+         if signal_type == "flip_bullish":
+             if override is not None and override.disable_flip_bullish is not None:
+                 return override.disable_flip_bullish    # symbol 级覆盖全局
+             return self.global_config.get("disable_flip_bullish_entries", True)
+
+         if signal_type == "green_bar_growing":
+             if override is not None and override.disable_green_bar_growing is not None:
+                 return override.disable_green_bar_growing
+             return self.global_config.get("disable_green_bar_growing_entries", True)
+
+         return False  # 其他信号类型默认不禁用
+
+     def get_min_signal_score(self, symbol: str) -> float:
+         """获取指定 symbol 的入场评分门槛（可被 symbol 级别覆盖）"""
+         override = self._registry.get(symbol)
+         if override and override.min_signal_score_override is not None:
+             return override.min_signal_score_override
+         return self.global_config.get("min_signal_score", 0.85)
```

### 3D.2 在信号决策层替换全局禁用检查

```python
# 修改文件：src/strategy/macd_mtf_strategy_v2.py

  def _is_signal_disabled(self, signal_type: str) -> bool:
-     """全局信号禁用检查"""
-     if signal_type == "flip_bullish":
-         return self.config.get("disable_flip_bullish_entries", True)
-     if signal_type == "green_bar_growing":
-         return self.config.get("disable_green_bar_growing_entries", True)
-     return False

+     """symbol 级 override 优先的信号禁用检查（需传入 symbol）"""
+     # 注意：调用时需改为传入 symbol 参数
+     pass

  def generate_signal(self, context: SignalContext) -> Optional[TradingSignal]:
      ...
-     if self._is_signal_disabled(signal_type):
+     if self.override_registry.is_signal_disabled(context.symbol, signal_type):
          return None
      ...
```

### 3D.3 Config 新增字段（仅在单币种验证完成后启用）

```diff
  "fund_flow": {
    "macd_mtf_strategy_v2": {
+     "symbol_signal_overrides": [
+       // 初始为空，只有单币种 flip_bullish 回测通过后才添加
+       // 示例（验证通过后才取消注释）：
+       // {
+       //   "symbol": "ATOMUSDT",
+       //   "disable_flip_bullish": false,
+       //   "min_signal_score_override": 0.88
+       // },
+       // {
+       //   "symbol": "SOLUSDT",
+       //   "disable_flip_bullish": false,
+       //   "min_signal_score_override": 0.88
+       // }
+     ]
    }
  }
```

---

## 回测验证标准

### 全局验收矩阵

每个 Phase 执行后，必须通过以下全部验收标准才能进入下一 Phase：

```python
# 回测验收检查器

def validate_backtest_result(result: dict, baseline: dict, phase: str) -> bool:
    """
    验收标准：新结果相对基线的改善/维持
    """
    checks = {
        "Phase 1": {
            "min_return_pct": 78.0,        # +82.70% ± 5%
            "min_profit_factor": 8.0,
            "max_drawdown_pct": -5.0,       # 不超过 -5%
            "trade_count_range": (120, 145),
        },
        "Phase 2A (leverage 1.0)": {
            "vs_baseline_return_delta": -3.0,   # 不低于基线 3%
            "max_drawdown_absolute_delta": 2.0,  # 不恶化 2%（绝对值）
            "min_profit_factor": 6.4,            # 基线 8.0 的 80%
            "min_win_rate": 0.65,
        },
        "Phase 2B (symbols=4)": {
            "vs_baseline_return_delta": 3.0,     # 必须提升 3%
            "max_drawdown_absolute_delta": 2.0,
            "max_worst_day_factor": 1.5,         # 最差单日 ≤ 基线 × 1.5
            "min_profit_factor": 7.0,
        },
        "Phase 3A (short filter)": {
            "flip_bearish_win_rate_improvement": 0.05,  # 胜率提升 ≥ 5%
            "no_regression_overall": True,
        },
        "Phase 3B (winner pyramiding)": {
            "vs_baseline_return_delta": 2.0,     # 提升 ≥ 2%
            "max_drawdown_absolute_delta": 3.0,
        },
    }

    criteria = checks.get(phase, {})
    passed = True

    for metric, threshold in criteria.items():
        actual = result.get(metric)
        if actual is None:
            print(f"  ❓ {metric}: 数据缺失")
            continue
        if isinstance(threshold, bool):
            ok = actual == threshold
        elif isinstance(threshold, tuple):
            ok = threshold[0] <= actual <= threshold[1]
        elif metric.startswith("max_drawdown"):
            ok = actual >= threshold  # 回撤是负数，越接近0越好
        else:
            ok = actual >= threshold
        status = "✅" if ok else "❌"
        print(f"  {status} {metric}: {actual} (阈值: {threshold})")
        if not ok:
            passed = False

    return passed
```

---

## 回滚方案

```bash
# 任何 Phase 验收不通过时，执行以下回滚

# 回滚 Phase 1 配置
cp trading_config_fund_flow_baseline_v3.json trading_config_fund_flow.json

# 回滚 Phase 3 新增代码（git 方式）
git stash          # 暂存未提交修改
# 或
git checkout HEAD -- src/strategy/filters/
git checkout HEAD -- src/strategy/position/

# 确认系统恢复基线
python run_backtest.py \
  --config trading_config_fund_flow.json \
  --quick_verify true \
  --expected_return 44.67
```

---

## 附录：修改项快速索引

| Phase | 类型 | 文件 | 是否需要新代码 | 当前状态 |
|-------|------|------|--------------|----------|
| 1 | Config | `trading_config_fund_flow.json` | ❌ 否 | ✅ 可立即执行 |
| 2A | Config | `trading_config_fund_flow.json` | ❌ 否 | 📊 需回测 |
| 2B | Config | `trading_config_fund_flow.json` | ❌ 否 | 📊 需回测 |
| 3A | Code | `short_quality_filter.py` | ✅ 是 | 🔧 需开发 |
| 3A | Code | `macd_mtf_strategy_v2.py` | ✅ 是（改动小） | 🔧 需开发 |
| 3B | Code | `winner_pyramiding.py` | ✅ 是 | 🔧 需开发 |
| 3B | Code | `position_manager.py` | ✅ 是（改动中） | 🔧 需开发 |
| 3C | Code | `time_window_filter.py` | ✅ 是 | 🔧 需开发 |
| 3C | Code | `macd_mtf_strategy_v2.py` | ✅ 是（改动小） | 🔧 需开发 |
| 3D | Code | `symbol_signal_override.py` | ✅ 是 | 🔧 需开发（最后） |
| 3D | Code | `macd_mtf_strategy_v2.py` | ✅ 是（改动中） | 🔧 需开发（最后） |

---

*文档版本：V3 Technical Implementation v1.0*
*基准策略：MACD_MTF_Strategy_V3 universe-only（+82.70%）*
*最后更新：2026-03-19*
*供专家组内部讨论使用，请勿对外传播*
