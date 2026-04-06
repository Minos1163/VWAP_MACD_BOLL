"""
多币种回测脚本

功能：
1. 从Binance公开API获取历史K线数据（无需API密钥）
2. 使用跨周期冲突处理系统进行交易分析
3. 支持多币种同时回测，最大持仓数限制
4. 生成详细的回测报告

使用方法：
    python scripts/backtest_multi_symbol.py

配置：
    - 币种列表：在配置文件中定义
    - 回测天数：默认30天
    - 最大持仓：默认2个
"""

import json
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
import requests
import time
import sys

# 添加项目路径
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.fund_flow.timeframe_conflict_resolver import (
    TimeframeConflictResolver,
    ConflictResolverConfig,
    ConflictIntensity,
    ResolutionPath,
    ConflictState
)
from src.fund_flow.stabilization_scorer import StabilizationScorer


# ==================== 配置 ====================

@dataclass
class BacktestConfig:
    """回测配置"""
    # 虚拟货币市值5-50名（排除稳定币和BTC/ETH/BNB）
    # 移除问题币种: AKTUSDT, FLOWUSDT, SEIUSDT, CFXUSDT
    # 二次移除低胜率币种: IMXUSDT(0%), WLDUSDT(25%), SUIUSDT(28%), APTUSDT(31%), LINKUSDT(33%)
    # 三次移除: XLMUSDT(持续大额亏损)
    symbols: List[str] = field(default_factory=lambda: [
        # 市值5-50名 - 保留高质量币种
        "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT",
        "TRXUSDT", "DOTUSDT", "SHIBUSDT", "LTCUSDT",
        "BCHUSDT", "UNIUSDT", "NEARUSDT", "ATOMUSDT",
        "ICPUSDT", "HBARUSDT", "VETUSDT", "FILUSDT",
        "OPUSDT", "ARBUSDT", "MKRUSDT", "INJUSDT",
        "TIAUSDT", "AAVEUSDT",
        "GRTUSDT", "RUNEUSDT", "STXUSDT", "ALGOUSDT", "MINAUSDT",
        "ENSUSDT", "THETAUSDT", "FETUSDT", "RNDRUSDT",
        "AGIXUSDT", "WOOUSDT", "PEPEUSDT"
    ])
    days: int = 30
    max_positions: int = 2
    initial_capital: float = 10000.0  # USDT
    fee_rate: float = 0.0004  # 0.04% 手续费
    
    # 时间周期
    entry_timeframe: str = "15m"      # 入场扫描周期
    primary_timeframe: str = "1h"      # 主要分析周期
    higher_timeframe: str = "4h"       # 高级趋势周期
    
    # 动态止损止盈ATR倍数（优化止损策略）
    sl_atr_base: float = 1.5          # 基础止损ATR倍数
    sl_atr_min: float = 1.0           # 最小止损ATR倍数
    sl_atr_max: float = 2.5           # 最大止损ATR倍数
    sl_atr_long_mult: float = 1.2     # 做多止损额外倍数（更宽松）
    tp_rr_base: float = 2.0           # 基础盈亏比
    tp_rr_min: float = 1.5            # 最小盈亏比
    tp_rr_max: float = 3.5            # 最大盈亏比
    
    # 保本止损配置
    breakeven_trigger_pct: float = 0.004  # 盈利0.4%后触发保本（提高门槛）
    breakeven_lock_pct: float = 0.001     # 锁定0.1%利润
    
    # 止损延迟激活（避免入场即止损）
    sl_activation_bars: int = 2       # 入场后2根K线才激活止损
    sl_initial_buffer_pct: float = 0.006  # 初始止损缓冲0.6%（更宽）
    
    # 仓位管理
    max_position_pct: float = 0.3  # 单仓最大30%
    min_position_pct: float = 0.1  # 单仓最小10%

    # 动态杠杆配置（根据信号评分）- 做多使用更低杠杆
    leverage_tiers: Dict[float, int] = field(default_factory=lambda: {
        0.45: 3,   # 信号分 >= 0.45: 3倍杠杆
        0.60: 4,   # 信号分 >= 0.60: 4倍杠杆
        0.75: 5,   # 信号分 >= 0.75: 5倍杠杆
    })
    default_leverage: int = 3  # 默认杠杆
    long_leverage_mult: float = 0.8  # 做多杠杆乘数（降低做多杠杆）

    # 本地数据缓存
    use_local_data: bool = True   # 优先使用本地数据
    save_local_data: bool = True  # 保存数据到本地
    local_data_dir: str = "data/backtest_cache"  # 本地数据目录
    
    # 优化后的入场条件
    min_stabilization_score: int = 65  # 提高企稳门槛（原60）
    min_direction_conditions: int = 3   # 4H方向判断要求
    allow_weak_conflict: bool = False   # 禁止弱冲突入场（原True）
    conflict_score_penalty: int = 25    # 冲突时扣分增加（原20）
    min_signal_score: float = 0.55      # 提高最低信号评分（原0.50）
    
    # 做多额外趋势确认（更严格）
    long_extra_confirm_enabled: bool = True  # 启用做多额外确认
    long_require_macd_positive: bool = True  # 做多要求MACD柱>0
    long_require_ema_align: bool = True      # 做多要求EMA多头排列
    long_require_price_above_vwap: bool = True  # 做多要求价格>VWAP
    long_min_rsi: float = 40.0               # 做多要求RSI>40（避免超卖区域做多）
    long_max_rsi: float = 70.0               # 做多要求RSI<70（避免超买区域做多）
    
    # 做空额外确认
    short_require_macd_negative: bool = True  # 做空要求MACD柱<0
    short_max_rsi: float = 60.0               # 做空要求RSI<60
    
    # 入场价格位置过滤（避免追高杀跌）
    price_position_min: float = 0.3    # 价格在近期区间最低30%不做多
    price_position_max: float = 0.7    # 价格在近期区间最高30%不做空
    lookback_period: int = 24          # 回看周期（根K线）
    
    # 波动性过滤（避免震荡行情）
    min_adx: float = 25.0              # 最低ADX值（趋势强度，提高避免弱趋势）
    max_adx: float = 40.0              # 最高ADX值（调整到40，平衡趋势末端和强趋势）
    min_atr_pct: float = 0.003         # 最低ATR百分比（0.3%）
    max_atr_pct: float = 0.08          # 最高ATR百分比（8%，避免极端波动）
    use_volatility_filter: bool = True # 是否启用波动性过滤
    
    # 避免集中止损（放宽限制）
    max_same_direction_entry: int = 2   # 同一时间最多开仓数
    
    # 单笔最大亏损限制
    max_single_loss_pct: float = 0.015  # 单笔最大亏损1.5%资金

    # K线形态过滤（避免追高/假突破）
    check_candle_pattern: bool = True  # 是否检查K线形态
    max_body_ratio: float = 0.7        # 入场K线实体最大占比（避免大阳线后追高）
    min_lower_wick_ratio: float = 0.3  # 做多时最低下影线占比（确认支撑）
    avoid_doji: bool = True            # 避免十字星入场

    # 突破确认
    require_pullback: bool = True      # 是否要求回踩确认
    pullback_pct: float = 0.01         # 回踩幅度（1%）
    max_breakout_age: int = 5          # 突破后最大入场延迟（小时）

    # 动态时间止损
    base_time_stop: int = 10           # 基础时间止损（小时）
    time_stop_atr_mult: float = 2.0    # 时间止损ATR倍数调整系数


# ==================== 数据结构 ====================

@dataclass
class Position:
    """持仓信息"""
    symbol: str
    side: str  # 'long' or 'short'
    entry_price: float
    size: float  # 仓位大小（USDT）
    stop_loss: float
    take_profit: float
    entry_time: datetime
    bars_held: int = 0
    max_profit_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    entry_score: int = 0
    signal_score: float = 0.0  # 信号评分
    conflict_level: str = ""
    direction_4h_confidence: float = 0.0
    leverage: int = 4  # 动态杠杆倍数
    # 新增：动态ATR参数
    sl_atr_mult: float = 1.5
    tp_rr: float = 2.0
    adx: float = 0.0
    atr_pct: float = 0.0


@dataclass
class Trade:
    """交易记录"""
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    size: float
    pnl: float
    pnl_pct: float
    entry_time: datetime
    exit_time: datetime
    bars_held: int
    exit_reason: str
    entry_score: int
    signal_score: float
    conflict_level: str
    max_profit_pct: float
    max_drawdown_pct: float
    leverage: int = 4  # 动态杠杆倍数
    # 新增：动态ATR参数
    sl_atr_mult: float = 1.5
    tp_rr: float = 2.0
    adx: float = 0.0
    atr_pct: float = 0.0


@dataclass
class BacktestResult:
    """回测结果"""
    # 基本信息
    start_date: datetime
    end_date: datetime
    initial_capital: float
    final_capital: float
    
    # 统计指标
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    
    # 按币种统计
    symbol_stats: Dict[str, Dict] = field(default_factory=dict)
    
    # 按冲突等级统计
    conflict_level_stats: Dict[str, Dict] = field(default_factory=dict)
    
    # 交易记录
    trades: List[Trade] = field(default_factory=list)
    
    # 权益曲线
    equity_curve: List[Dict] = field(default_factory=list)


# ==================== 数据获取 ====================

class BinanceDataFetcher:
    """Binance历史数据获取器（公开API，无需密钥）"""
    
    BASE_URL = "https://fapi.binance.com"
    
    def __init__(self, cache_dir: str = "data/backtest_cache"):
        self.session = requests.Session()
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
    def get_cache_path(self, symbol: str, interval: str, days: int) -> Path:
        """获取缓存文件路径"""
        date_str = datetime.now().strftime("%Y%m%d")
        return self.cache_dir / f"{symbol}_{interval}_{days}d_{date_str}.parquet"
    
    def save_to_cache(self, df: pd.DataFrame, symbol: str, interval: str, days: int) -> None:
        """保存数据到本地缓存"""
        cache_path = self.get_cache_path(symbol, interval, days)
        df.to_parquet(cache_path, index=False)
        print(f"    已缓存到: {cache_path.name}")
    
    def load_from_cache(self, symbol: str, interval: str, days: int) -> Optional[pd.DataFrame]:
        """从本地缓存读取数据"""
        cache_path = self.get_cache_path(symbol, interval, days)
        if cache_path.exists():
            df = pd.read_parquet(cache_path)
            print(f"    从缓存加载: {cache_path.name} ({len(df)} 根K线)")
            return df
        return None
    
    def clear_old_cache(self, days: int = 7) -> None:
        """清理过期缓存"""
        import os
        cutoff = datetime.now() - timedelta(days=days)
        for file in self.cache_dir.glob("*.parquet"):
            if datetime.fromtimestamp(file.stat().st_mtime) < cutoff:
                file.unlink()
                print(f"清理过期缓存: {file.name}")
        
    def fetch_klines(
        self,
        symbol: str,
        interval: str,
        start_time: datetime,
        end_time: datetime,
        limit: int = 1500,
        use_cache: bool = True,
        save_cache: bool = True,
        days: int = 30
    ) -> pd.DataFrame:
        """
        获取K线数据（支持本地缓存）
        
        Args:
            symbol: 交易对
            interval: 时间周期 ('1m', '5m', '15m', '1h', '4h', '1d')
            start_time: 开始时间
            end_time: 结束时间
            limit: 每次请求的最大K线数
            use_cache: 是否使用缓存
            save_cache: 是否保存到缓存
            days: 缓存文件标识天数
        
        Returns:
            DataFrame with OHLCV data
        """
        # 尝试从缓存读取
        if use_cache:
            cached_df = self.load_from_cache(symbol, interval, days)
            if cached_df is not None and not cached_df.empty:
                # 过滤时间范围
                cached_df['timestamp'] = pd.to_datetime(cached_df['timestamp'])
                cached_df = cached_df[(cached_df['timestamp'] >= start_time) & 
                                       (cached_df['timestamp'] <= end_time)]
                return cached_df
        
        # 从API获取
        all_klines = []
        current_start = start_time
        
        while current_start < end_time:
            params = {
                "symbol": symbol,
                "interval": interval,
                "startTime": int(current_start.timestamp() * 1000),
                "limit": limit
            }
            
            try:
                response = self.session.get(
                    f"{self.BASE_URL}/fapi/v1/klines",
                    params=params,
                    timeout=30
                )
                response.raise_for_status()
                klines = response.json()
                
                if not klines:
                    break
                
                all_klines.extend(klines)
                
                # 更新开始时间
                last_time = klines[-1][0] / 1000
                current_start = datetime.fromtimestamp(last_time) + timedelta(seconds=1)
                
                # 避免请求过快
                time.sleep(0.1)
                
                # 如果已到达结束时间，退出
                if current_start >= end_time:
                    break
                    
            except Exception as e:
                print(f"获取 {symbol} {interval} 数据失败: {e}")
                break
        
        if not all_klines:
            return pd.DataFrame()
        
        # 转换为DataFrame
        df = pd.DataFrame(all_klines, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_volume', 'trades', 'taker_buy_base',
            'taker_buy_quote', 'ignore'
        ])
        
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
        
        # 过滤时间范围
        df = df[(df['timestamp'] >= start_time) & (df['timestamp'] <= end_time)]
        df = df.sort_values('timestamp').reset_index(drop=True)
        
        # 保存到缓存
        if save_cache and not df.empty:
            self.save_to_cache(df, symbol, interval, days)
        
        return df
    
    def fetch_multiple_timeframes(
        self,
        symbol: str,
        timeframes: List[str],
        start_time: datetime,
        end_time: datetime,
        use_cache: bool = True,
        save_cache: bool = True,
        days: int = 30
    ) -> Dict[str, pd.DataFrame]:
        """
        获取多个时间周期的数据
        
        Returns:
            {timeframe: DataFrame}
        """
        result = {}
        for tf in timeframes:
            print(f"  获取 {symbol} {tf} 数据...")
            df = self.fetch_klines(symbol, tf, start_time, end_time, 
                                   use_cache=use_cache, save_cache=save_cache, days=days)
            if not df.empty:
                result[tf] = df
                print(f"    获取到 {len(df)} 根K线")
            time.sleep(0.2)  # 避免请求过快
        
        return result


# ==================== 回测引擎 ====================

class MultiSymbolBacktester:
    """多币种回测引擎"""
    
    def __init__(self, config: BacktestConfig):
        self.config = config
        self.fetcher = BinanceDataFetcher()
        self.conflict_resolver = TimeframeConflictResolver()
        self.scorer = StabilizationScorer()
        
        # 数据存储
        self.data: Dict[str, Dict[str, pd.DataFrame]] = {}  # {symbol: {timeframe: df}}
        
        # 持仓管理
        self.positions: Dict[str, Position] = {}
        self.capital = config.initial_capital
        self.available_capital = config.initial_capital
        
        # 交易记录
        self.trades: List[Trade] = []
        
        # 权益曲线
        self.equity_curve: List[Dict] = []
    
    def fetch_all_data(self) -> None:
        """获取所有币种的数据"""
        end_time = datetime.now()
        start_time = end_time - timedelta(days=self.config.days + 10)  # 多获取一些用于预热
        
        print(f"\n{'='*60}")
        print(f"获取历史数据 ({start_time.strftime('%Y-%m-%d')} ~ {end_time.strftime('%Y-%m-%d')})")
        if self.config.use_local_data:
            print(f"优先使用本地缓存: {self.config.local_data_dir}")
        print(f"{'='*60}")
        
        for symbol in self.config.symbols:
            print(f"\n[{symbol}]")
            data = self.fetcher.fetch_multiple_timeframes(
                symbol,
                [self.config.entry_timeframe, self.config.primary_timeframe, self.config.higher_timeframe],
                start_time,
                end_time,
                use_cache=self.config.use_local_data,
                save_cache=self.config.save_local_data,
                days=self.config.days
            )
            
            if data:
                self.data[symbol] = data
            else:
                print(f"  [WARN] {symbol} 数据获取失败，跳过")
    
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算技术指标"""
        close = df['close'].values
        high = df['high'].values
        low = df['low'].values
        
        # EMA
        df['ema21'] = self._calculate_ema(close, 21)
        df['ema55'] = self._calculate_ema(close, 55)
        df['ema200'] = self._calculate_ema(close, 200)
        
        # MACD
        macd, signal, hist = self._calculate_macd(close)
        df['macd'] = macd
        df['macd_signal'] = signal
        df['macd_hist'] = hist
        
        # ATR
        df['atr'] = self._calculate_atr(high, low, close, 14)
        
        # ADX（趋势强度指标）
        df['adx'] = self._calculate_adx(high, low, close, 14)
        df['atr_pct'] = df['atr'] / close  # ATR百分比
        
        return df
    
    def _calculate_ema(self, data: np.ndarray, period: int) -> np.ndarray:
        """计算EMA"""
        ema = np.zeros_like(data)
        ema[:period] = data[:period].mean()
        multiplier = 2 / (period + 1)
        for i in range(period, len(data)):
            ema[i] = (data[i] - ema[i-1]) * multiplier + ema[i-1]
        return ema
    
    def _calculate_macd(
        self, 
        data: np.ndarray,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """计算MACD"""
        ema_fast = self._calculate_ema(data, fast)
        ema_slow = self._calculate_ema(data, slow)
        macd_line = ema_fast - ema_slow
        signal_line = self._calculate_ema(macd_line, signal)
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram
    
    def _calculate_atr(
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        period: int = 14
    ) -> np.ndarray:
        """计算ATR"""
        tr = np.zeros(len(close))
        tr[0] = high[0] - low[0]
        for i in range(1, len(close)):
            tr[i] = max(
                high[i] - low[i],
                abs(high[i] - close[i-1]),
                abs(low[i] - close[i-1])
            )
        atr = np.zeros_like(tr)
        atr[:period] = tr[:period].mean()
        for i in range(period, len(tr)):
            atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period
        return atr
    
    def _calculate_adx(
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        period: int = 14
    ) -> np.ndarray:
        """
        计算ADX（Average Directional Index）
        ADX > 25: 趋势市场
        ADX < 20: 震荡市场
        """
        n = len(close)
        
        # 计算+DM和-DM
        plus_dm = np.zeros(n)
        minus_dm = np.zeros(n)
        
        for i in range(1, n):
            up_move = high[i] - high[i-1]
            down_move = low[i-1] - low[i]
            
            if up_move > down_move and up_move > 0:
                plus_dm[i] = up_move
            else:
                plus_dm[i] = 0
                
            if down_move > up_move and down_move > 0:
                minus_dm[i] = down_move
            else:
                minus_dm[i] = 0
        
        # 计算TR
        tr = np.zeros(n)
        tr[0] = high[0] - low[0]
        for i in range(1, n):
            tr[i] = max(
                high[i] - low[i],
                abs(high[i] - close[i-1]),
                abs(low[i] - close[i-1])
            )
        
        # 平滑处理
        atr_smooth = np.zeros(n)
        plus_dm_smooth = np.zeros(n)
        minus_dm_smooth = np.zeros(n)
        
        atr_smooth[:period] = tr[:period].sum()
        plus_dm_smooth[:period] = plus_dm[:period].sum()
        minus_dm_smooth[:period] = minus_dm[:period].sum()
        
        for i in range(period, n):
            atr_smooth[i] = atr_smooth[i-1] - atr_smooth[i-1] / period + tr[i]
            plus_dm_smooth[i] = plus_dm_smooth[i-1] - plus_dm_smooth[i-1] / period + plus_dm[i]
            minus_dm_smooth[i] = minus_dm_smooth[i-1] - minus_dm_smooth[i-1] / period + minus_dm[i]
        
        # 计算+DI和-DI
        plus_di = np.zeros(n)
        minus_di = np.zeros(n)
        
        for i in range(period, n):
            if atr_smooth[i] > 0:
                plus_di[i] = 100 * plus_dm_smooth[i] / atr_smooth[i]
                minus_di[i] = 100 * minus_dm_smooth[i] / atr_smooth[i]
        
        # 计算DX
        dx = np.zeros(n)
        for i in range(period, n):
            di_sum = plus_di[i] + minus_di[i]
            if di_sum > 0:
                dx[i] = 100 * abs(plus_di[i] - minus_di[i]) / di_sum
        
        # 计算ADX（DX的平滑）
        adx = np.zeros(n)
        adx[:period*2] = dx[:period*2].mean() if period*2 <= n else 0
        
        for i in range(period*2, n):
            adx[i] = (adx[i-1] * (period - 1) + dx[i]) / period
        
        return adx
    
    def check_volatility_filter(
        self,
        df_1h: pd.DataFrame,
        idx_1h: int
    ) -> Tuple[bool, Dict]:
        """
        波动性过滤检查
        
        过滤条件：
        1. ADX >= min_adx（趋势强度足够）
        2. min_atr_pct <= ATR% <= max_atr_pct（波动适中）
        
        Returns:
            passed: 是否通过过滤
            details: 过滤详情
        """
        if not self.config.use_volatility_filter:
            return True, {'filter_disabled': True}
        
        details = {}
        
        # 获取指标值
        adx = df_1h['adx'].iloc[idx_1h]
        atr = df_1h['atr'].iloc[idx_1h]
        close = df_1h['close'].iloc[idx_1h]
        atr_pct = atr / close if close > 0 else 0
        
        details['adx'] = adx
        details['atr'] = atr
        details['atr_pct'] = atr_pct
        
        passed = True
        reasons = []
        
        # ADX检查（趋势强度）
        if adx < self.config.min_adx:
            passed = False
            reasons.append(f"ADX={adx:.1f}<{self.config.min_adx}(趋势弱)")
        
        # ADX上限检查（避免趋势末端）
        if hasattr(self.config, 'max_adx') and adx > self.config.max_adx:
            passed = False
            reasons.append(f"ADX={adx:.1f}>{self.config.max_adx}(趋势末端风险)")
        
        # ATR百分比检查（波动适中）
        if atr_pct < self.config.min_atr_pct:
            passed = False
            reasons.append(f"ATR%={atr_pct*100:.2f}%<{self.config.min_atr_pct*100:.1f}%(波动低)")
        elif atr_pct > self.config.max_atr_pct:
            passed = False
            reasons.append(f"ATR%={atr_pct*100:.2f}%>{self.config.max_atr_pct*100:.1f}%(波动极端)")
        
        details['passed'] = passed
        details['reasons'] = reasons
        
        return passed, details
    
    def check_candle_pattern(
        self,
        df_1h: pd.DataFrame,
        idx_1h: int,
        direction: str
    ) -> Tuple[bool, Dict]:
        """
        K线形态检查（避免追高/假突破）
        
        检查内容：
        1. 避免在大阳线后追高（实体过大，可能回调）
        2. 做多时要求有下影线（确认支撑）
        3. 避免十字星入场（方向不明）
        4. 避免长上影线后做多（阻力位）
        
        Returns:
            passed: 是否通过检查
            details: 检查详情
        """
        if not self.config.check_candle_pattern:
            return True, {'pattern_check_disabled': True}
        
        details = {}
        passed = True
        warnings = []
        
        # 当前K线数据
        open_price = df_1h['open'].iloc[idx_1h]
        close = df_1h['close'].iloc[idx_1h]
        high = df_1h['high'].iloc[idx_1h]
        low = df_1h['low'].iloc[idx_1h]
        
        # 计算K线特征
        body = abs(close - open_price)
        candle_range = high - low if high > low else 0.0001
        body_ratio = body / candle_range if candle_range > 0 else 0
        
        upper_wick = high - max(close, open_price)
        lower_wick = min(close, open_price) - low
        upper_wick_ratio = upper_wick / candle_range if candle_range > 0 else 0
        lower_wick_ratio = lower_wick / candle_range if candle_range > 0 else 0
        
        details['body_ratio'] = body_ratio
        details['upper_wick_ratio'] = upper_wick_ratio
        details['lower_wick_ratio'] = lower_wick_ratio
        
        if direction == 'long':
            # 1. 检查是否为大阳线后追高
            if body_ratio > self.config.max_body_ratio:
                # 实体过大，可能是强势拉升后的追高
                if idx_1h >= 1:
                    prev_close = df_1h['close'].iloc[idx_1h - 1]
                    # 如果已经连续上涨，风险更大
                    if close > prev_close and body_ratio > 0.8:
                        passed = False
                        warnings.append(f"大阳线后追高风险(实体占比{body_ratio:.1%})")
            
            # 2. 做多要求有下影线支撑
            if self.config.min_lower_wick_ratio > 0:
                if lower_wick_ratio < self.config.min_lower_wick_ratio and body_ratio < 0.5:
                    # 没有明显下影线且实体较小，可能是假企稳
                    warnings.append(f"下影线不足({lower_wick_ratio:.1%})，支撑未确认")
            
            # 3. 避免长上影线后做多（阻力位）
            if upper_wick_ratio > 0.5:
                warnings.append(f"长上影线({upper_wick_ratio:.1%})，上方阻力明显")
                if upper_wick_ratio > 0.7:
                    passed = False
                    warnings.append("长上影线过长，拒绝入场")
        
        else:  # short
            # 做空时的检查
            if body_ratio > self.config.max_body_ratio:
                if idx_1h >= 1:
                    prev_close = df_1h['close'].iloc[idx_1h - 1]
                    if close < prev_close and body_ratio > 0.8:
                        passed = False
                        warnings.append(f"大阴线后追空风险(实体占比{body_ratio:.1%})")
            
            # 做空要求有上影线阻力
            if upper_wick_ratio < self.config.min_lower_wick_ratio and body_ratio < 0.5:
                warnings.append(f"上影线不足({upper_wick_ratio:.1%})，阻力未确认")
            
            # 避免长下影线后做空（支撑位）
            if lower_wick_ratio > 0.5:
                warnings.append(f"长下影线({lower_wick_ratio:.1%})，下方支撑明显")
                if lower_wick_ratio > 0.7:
                    passed = False
                    warnings.append("长下影线过长，拒绝入场")
        
        # 4. 十字星检查
        if self.config.avoid_doji and body_ratio < 0.1:
            passed = False
            warnings.append("十字星形态，方向不明确")
        
        # 5. 前3根K线波动方向检查（避免追高追低）
        if idx_1h >= 3:
            # 获取前3根K线数据（不含当前K线）
            prev_closes = df_1h['close'].iloc[idx_1h-3:idx_1h].values
            prev_opens = df_1h['open'].iloc[idx_1h-3:idx_1h].values
            prev_highs = df_1h['high'].iloc[idx_1h-3:idx_1h].values
            prev_lows = df_1h['low'].iloc[idx_1h-3:idx_1h].values
            
            # 计算前3根K线的方向和幅度
            bullish_candles = 0
            bearish_candles = 0
            total_change_pct = 0.0
            consecutive_same_dir = 0
            
            for i in range(len(prev_closes)):
                candle_body = prev_closes[i] - prev_opens[i]
                if candle_body > 0:
                    bullish_candles += 1
                    if i > 0 and (prev_closes[i-1] - prev_opens[i-1]) > 0:
                        consecutive_same_dir += 1
                else:
                    bearish_candles += 1
                    if i > 0 and (prev_closes[i-1] - prev_opens[i-1]) < 0:
                        consecutive_same_dir += 1
                
                # 累计价格变动
                if i > 0:
                    change = (prev_closes[i] - prev_closes[i-1]) / prev_closes[i-1]
                    total_change_pct += change
            
            details['prev_bullish_candles'] = bullish_candles
            details['prev_bearish_candles'] = bearish_candles
            details['total_change_pct'] = total_change_pct
            
            if direction == 'long':
                # 做多时的检查
                # 1. 避免3根连续阳线后追高
                if bullish_candles == 3:
                    passed = False
                    warnings.append(f"前3根全部为阳线，追高风险极大")
                
                # 2. 避免2根连续阳线+当前阳线
                if bullish_candles >= 2 and close > open_price:
                    warnings.append(f"前{bullish_candles}根阳线+当前阳线，短期可能回调")
                    # 如果涨幅过大，拒绝入场
                    if total_change_pct > 0.03:  # 3%涨幅
                        passed = False
                        warnings.append(f"前3根累计涨幅{total_change_pct*100:.1f}%，拒绝追高")
                
                # 3. 检查是否有回调迹象
                if bullish_candles >= 2:
                    # 检查最近一根K线是否有上影线（回调信号）
                    last_upper_wick = prev_highs[-1] - max(prev_closes[-1], prev_opens[-1])
                    last_body = abs(prev_closes[-1] - prev_opens[-1])
                    if last_upper_wick > last_body * 0.5:
                        warnings.append("前一根K线有明显上影线，可能正在回调")
                    # 检查成交量是否萎缩（动能不足）
                    if idx_1h >= 4:
                        vol_1 = df_1h['volume'].iloc[idx_1h-3]
                        vol_2 = df_1h['volume'].iloc[idx_1h-2]
                        vol_3 = df_1h['volume'].iloc[idx_1h-1]
                        if vol_3 < vol_2 < vol_1:
                            warnings.append("成交量连续萎缩，上涨动能不足")
                
                # 4. 理想入场：前3根中有回调（至少1根阴线）
                if bearish_candles >= 1:
                    details['pullback_detected'] = True
                    
            else:  # short
                # 做空时的检查
                # 1. 避免3根连续阴线后追空
                if bearish_candles == 3:
                    passed = False
                    warnings.append(f"前3根全部为阴线，追空风险极大")
                
                # 2. 避免2根连续阴线+当前阴线
                if bearish_candles >= 2 and close < open_price:
                    warnings.append(f"前{bearish_candles}根阴线+当前阴线，短期可能反弹")
                    # 如果跌幅过大，拒绝入场
                    if total_change_pct < -0.03:  # 3%跌幅
                        passed = False
                        warnings.append(f"前3根累计跌幅{abs(total_change_pct)*100:.1f}%，拒绝追空")
                
                # 3. 检查是否有反弹迹象
                if bearish_candles >= 2:
                    last_lower_wick = min(prev_closes[-1], prev_opens[-1]) - prev_lows[-1]
                    last_body = abs(prev_closes[-1] - prev_opens[-1])
                    if last_lower_wick > last_body * 0.5:
                        warnings.append("前一根K线有明显下影线，可能正在反弹")
                    if idx_1h >= 4:
                        vol_1 = df_1h['volume'].iloc[idx_1h-3]
                        vol_2 = df_1h['volume'].iloc[idx_1h-2]
                        vol_3 = df_1h['volume'].iloc[idx_1h-1]
                        if vol_3 < vol_2 < vol_1:
                            warnings.append("成交量连续萎缩，下跌动能不足")
                
                # 4. 理想入场：前3根中有反弹（至少1根阳线）
                if bullish_candles >= 1:
                    details['bounce_detected'] = True
        
        details['passed'] = passed
        details['warnings'] = warnings
        
        return passed, details
    
    def check_breakout_confirmation(
        self,
        df_1h: pd.DataFrame,
        idx_1h: int,
        direction: str
    ) -> Tuple[bool, Dict]:
        """
        突破确认检查
        
        检查内容：
        1. 检测最近是否有有效突破
        2. 确认是否已完成回踩确认
        3. 避免在突破高点直接追
        
        Returns:
            confirmed: 是否确认突破有效
            details: 检查详情
        """
        if not self.config.require_pullback:
            return True, {'pullback_check_disabled': True}
        
        details = {}
        confirmed = True
        notes = []
        
        if idx_1h < 10:
            return True, {'insufficient_data': True}
        
        close = df_1h['close'].iloc[idx_1h]
        
        # 检测最近10根K线的高低点
        lookback = min(10, idx_1h)
        recent_high = df_1h['high'].iloc[idx_1h-lookback:idx_1h].max()
        recent_low = df_1h['low'].iloc[idx_1h-lookback:idx_1h].min()
        
        details['recent_high'] = recent_high
        details['recent_low'] = recent_low
        
        if direction == 'long':
            # 检查是否刚突破前高
            if close >= recent_high * 0.99:  # 接近或突破前高
                # 检查是否已完成回踩
                # 回踩确认：突破后价格回调但未跌破关键支撑
                pullback_occurred = False
                
                for i in range(idx_1h - 1, max(idx_1h - 5, 0), -1):
                    # 检查是否出现过回踩
                    prev_close = df_1h['close'].iloc[i]
                    if prev_close < recent_high * (1 - self.config.pullback_pct):
                        pullback_occurred = True
                        notes.append(f"已回踩确认(回调{self.config.pullback_pct*100:.1f}%)")
                        break
                
                if not pullback_occurred and close > recent_high:
                    # 刚突破未回踩，风险较高
                    notes.append("刚突破前高，未回踩确认")
                    # 不直接拒绝，但标记风险
                    details['breakout_fresh'] = True
            
            # 检查是否在支撑位附近（EMA21或前期低点）
            ema21 = df_1h['ema21'].iloc[idx_1h]
            support_dist = (close - ema21) / close
            
            if support_dist > 0.03:  # 距离支撑超过3%
                notes.append(f"距离EMA21支撑{support_dist*100:.1f}%，可能追高")
        
        else:  # short
            if close <= recent_low * 1.01:
                pullback_occurred = False
                
                for i in range(idx_1h - 1, max(idx_1h - 5, 0), -1):
                    prev_close = df_1h['close'].iloc[i]
                    if prev_close > recent_low * (1 + self.config.pullback_pct):
                        pullback_occurred = True
                        notes.append(f"已回踩确认(反弹{self.config.pullback_pct*100:.1f}%)")
                        break
                
                if not pullback_occurred and close < recent_low:
                    notes.append("刚跌破前低，未反弹确认")
                    details['breakdown_fresh'] = True
        
        details['confirmed'] = confirmed
        details['notes'] = notes
        
        return confirmed, details
    
    def detect_1h_macd_direction(
        self,
        df_1h: pd.DataFrame,
        idx_1h: int
    ) -> Tuple[Optional[str], Dict]:
        """
        MACD_1H定方向（核心方向判断）
        
        检测柱子翻红/翻绿/缩短等确认买卖方向：
        - 翻红（负转正）: 做多信号
        - 翻绿（正转负）: 做空信号
        - 红柱缩短（仍在0轴上）: 多头减弱，不考虑做多
        - 绿柱缩短（仍在0轴下）: 空头减弱，不考虑做空
        - 红柱增长（0轴上）: 多头增强，可做多
        - 绿柱增长（0轴下）: 空头增强，可做空
        
        Returns:
            direction: 'long'/'short'/None
            details: 详细信息
        """
        if idx_1h < 3:
            return None, {}
        
        details = {}
        
        # 当前和前几根K线的MACD柱
        hist_0 = df_1h['macd_hist'].iloc[idx_1h]
        hist_1 = df_1h['macd_hist'].iloc[idx_1h - 1]
        hist_2 = df_1h['macd_hist'].iloc[idx_1h - 2]
        
        macd_line = df_1h['macd'].iloc[idx_1h]
        macd_signal = df_1h['macd_signal'].iloc[idx_1h]
        
        direction = None
        signal_type = None
        
        # 1. 翻红检测（负转正）- 强做多信号
        if hist_1 <= 0 and hist_0 > 0:
            direction = 'long'
            signal_type = 'flip_bullish'
            details['signal_strength'] = 1.0  # 最强信号
            
        # 2. 翻绿检测（正转负）- 强做空信号
        elif hist_1 >= 0 and hist_0 < 0:
            direction = 'short'
            signal_type = 'flip_bearish'
            details['signal_strength'] = 1.0
            
        # 3. 红柱增长（0轴上）- 多头增强
        elif hist_0 > 0 and hist_1 > 0 and hist_0 > hist_1:
            direction = 'long'
            signal_type = 'red_bar_growing'
            details['signal_strength'] = 0.8
            
        # 4. 绿柱增长（0轴下）- 空头增强
        elif hist_0 < 0 and hist_1 < 0 and hist_0 < hist_1:
            direction = 'short'
            signal_type = 'green_bar_growing'
            details['signal_strength'] = 0.8
            
        # 5. 红柱缩短（0轴上）- 多头减弱，不入场
        elif hist_0 > 0 and hist_1 > 0 and hist_0 < hist_1:
            direction = None
            signal_type = 'red_bar_shrinking'
            details['signal_strength'] = 0.0
            
        # 6. 绿柱缩短（0轴下）- 空头减弱，不入场
        elif hist_0 < 0 and hist_1 < 0 and hist_0 > hist_1:
            direction = None
            signal_type = 'green_bar_shrinking'
            details['signal_strength'] = 0.0
        
        # 额外检查：MACD线与信号线关系
        if direction == 'long':
            if macd_line > macd_signal:
                details['macd_above_signal'] = True
                details['signal_strength'] = min(1.0, details.get('signal_strength', 0.5) + 0.1)
        elif direction == 'short':
            if macd_line < macd_signal:
                details['macd_below_signal'] = True
                details['signal_strength'] = min(1.0, details.get('signal_strength', 0.5) + 0.1)
        
        details['signal_type'] = signal_type
        details['hist_current'] = hist_0
        details['hist_prev'] = hist_1
        
        return direction, details
    
    def check_4h_macd_enhancement(
        self,
        df_4h: pd.DataFrame,
        idx_4h: int,
        direction: str
    ) -> Tuple[bool, float]:
        """
        MACD_4H确认方向增强（不能作为买卖点，只确认增强）
        
        Args:
            direction: 1H确定的方向
        
        Returns:
            is_enhanced: 是否增强
            enhancement_score: 增强评分 (0-1)
        """
        if idx_4h < 2:
            return False, 0.0
        
        hist_0 = df_4h['macd_hist'].iloc[idx_4h]
        hist_1 = df_4h['macd_hist'].iloc[idx_4h - 1]
        
        enhancement_score = 0.0
        is_enhanced = False
        
        if direction == 'long':
            # 4H MACD柱>0 且在增长 = 多头增强
            if hist_0 > 0:
                enhancement_score += 0.5
                if hist_0 > hist_1:
                    enhancement_score += 0.3
                    is_enhanced = True
                elif hist_0 < hist_1:
                    # 红柱缩短，减弱增强效果
                    enhancement_score += 0.1
            else:
                # 4H方向相反，不增强
                enhancement_score = 0.0
                
        elif direction == 'short':
            # 4H MACD柱<0 且在下降 = 空头增强
            if hist_0 < 0:
                enhancement_score += 0.5
                if hist_0 < hist_1:
                    enhancement_score += 0.3
                    is_enhanced = True
                elif hist_0 > hist_1:
                    # 绿柱缩短，减弱增强效果
                    enhancement_score += 0.1
            else:
                # 4H方向相反，不增强
                enhancement_score = 0.0
        
        return is_enhanced, enhancement_score
    
    def check_15m_macd_follow(
        self,
        df_15m: pd.DataFrame,
        idx_15m: int,
        direction: str
    ) -> Tuple[bool, float, Dict]:
        """
        MACD_15M跟随1H方向执行买卖
        
        15M不能作为方向的关键点，只是跟随1H方向找入场点
        
        Args:
            direction: 1H确定的方向
        
        Returns:
            can_enter: 是否可以入场
            entry_score: 入场评分 (0-1)
            details: 详细信息
        """
        if idx_15m < 2:
            return False, 0.0, {}
        
        details = {}
        hist_0 = df_15m['macd_hist'].iloc[idx_15m]
        hist_1 = df_15m['macd_hist'].iloc[idx_15m - 1]
        
        entry_score = 0.0
        can_enter = False
        
        # MACD阈值，避免震荡区
        macd_threshold = 0.00005
        
        if direction == 'long':
            # 跟随做多：15M MACD柱需要>0（或刚翻红）
            
            # 情况1：刚翻红（最强入场）
            if hist_1 <= 0 and hist_0 > 0:
                entry_score = 1.0
                can_enter = True
                details['entry_type'] = 'flip_bullish'
                
            # 情况2：红柱增长
            elif hist_0 > 0 and hist_1 > 0 and hist_0 > hist_1:
                entry_score = 0.85
                can_enter = True
                details['entry_type'] = 'red_bar_growing'
                
            # 情况3：红柱稳定（仍可入场，但评分较低）
            elif hist_0 > macd_threshold and hist_0 > 0:
                entry_score = 0.6
                can_enter = True
                details['entry_type'] = 'red_bar_stable'
                
            # 情况4：红柱缩短 - 入场评分降低
            elif hist_0 > 0 and hist_1 > 0 and hist_0 < hist_1:
                entry_score = 0.3  # 仍可入场，但评分低
                can_enter = True
                details['entry_type'] = 'red_bar_shrinking'
                
        elif direction == 'short':
            # 跟随做空：15M MACD柱需要<0（或刚翻绿）
            
            # 情况1：刚翻绿（最强入场）
            if hist_1 >= 0 and hist_0 < 0:
                entry_score = 1.0
                can_enter = True
                details['entry_type'] = 'flip_bearish'
                
            # 情况2：绿柱增长
            elif hist_0 < 0 and hist_1 < 0 and hist_0 < hist_1:
                entry_score = 0.85
                can_enter = True
                details['entry_type'] = 'green_bar_growing'
                
            # 情况3：绿柱稳定
            elif hist_0 < -macd_threshold and hist_0 < 0:
                entry_score = 0.6
                can_enter = True
                details['entry_type'] = 'green_bar_stable'
                
            # 情况4：绿柱缩短
            elif hist_0 < 0 and hist_1 < 0 and hist_0 > hist_1:
                entry_score = 0.3
                can_enter = True
                details['entry_type'] = 'green_bar_shrinking'
        
        details['hist_current'] = hist_0
        details['hist_prev'] = hist_1
        
        return can_enter, entry_score, details
    
    def check_macd_alignment(
        self,
        df_15m: pd.DataFrame,
        df_1h: pd.DataFrame,
        df_4h: pd.DataFrame,
        idx_15m: int
    ) -> Optional[str]:
        """
        检查MACD方向 - 新策略架构
        
        策略逻辑：
        1. MACD_1H 定方向（柱子翻红/翻绿/缩短等确认买卖方向）
        2. MACD_4H 确认增强（同向增强信号，不作为买卖点）
        3. MACD_15M 跟随入场（跟随1H方向执行买卖）
        
        Args:
            df_15m: 15分钟K线数据
            df_1h: 1小时K线数据
            df_4h: 4小时K线数据
            idx_15m: 当前15分钟K线索引
        
        Returns:
            'long': 做多信号
            'short': 做空信号
            None: 无信号
        """
        # 获取当前15M时间
        current_time = df_15m['timestamp'].iloc[idx_15m]
        
        # 找到对应的1H K线索引
        mask_1h = (df_1h['timestamp'] <= current_time)
        if not mask_1h.any():
            return None
        idx_1h = mask_1h[mask_1h].index[-1]
        
        # 找到对应的4H K线索引
        mask_4h = (df_4h['timestamp'] <= current_time)
        if not mask_4h.any():
            return None
        idx_4h = mask_4h[mask_4h].index[-1]
        
        # ========== 第1步：MACD_1H 定方向 ==========
        direction_1h, details_1h = self.detect_1h_macd_direction(df_1h, idx_1h)
        if direction_1h is None:
            return None
        
        # ========== 第2步：MACD_4H 确认增强 ==========
        is_4h_enhanced, enhancement_score = self.check_4h_macd_enhancement(
            df_4h, idx_4h, direction_1h
        )
        
        # 如果4H方向相反，降低信号强度（但不完全拒绝）
        if enhancement_score == 0:
            # 4H方向相反，但允许入场（只是降低评分）
            pass
        
        # ========== 第3步：MACD_15M 跟随入场 ==========
        can_enter, entry_score, details_15m = self.check_15m_macd_follow(
            df_15m, idx_15m, direction_1h
        )
        
        if not can_enter:
            return None
        
        # 最低入场评分要求
        min_entry_score = 0.3
        if entry_score < min_entry_score:
            return None
        
        # ========== 综合评分 ==========
        # 存储 MACD 分析结果用于后续评分
        self._macd_analysis = {
            'direction_1h': direction_1h,
            'signal_type_1h': details_1h.get('signal_type'),
            'signal_strength_1h': details_1h.get('signal_strength', 0.5),
            'is_4h_enhanced': is_4h_enhanced,
            'enhancement_score': enhancement_score,
            'entry_type_15m': details_15m.get('entry_type'),
            'entry_score_15m': entry_score
        }
        
        return direction_1h
    
    def calculate_entry_signal_score(
        self,
        df_15m: pd.DataFrame,
        df_1h: pd.DataFrame,
        df_4h: pd.DataFrame,
        idx_15m: int,
        direction: str
    ) -> Tuple[float, Dict]:
        """
        计算入场信号评分（新版 - 基于MACD策略架构）
        
        评分权重：
        - MACD_1H定方向: 40%（核心）
        - MACD_4H增强确认: 20%
        - MACD_15M跟随入场: 25%
        - 成交量确认: 15%
        """
        score = 0.0
        details = {}
        
        # 使用之前存储的MACD分析结果
        macd_analysis = getattr(self, '_macd_analysis', {})
        
        # ========== 1H定方向评分 (40%) ==========
        signal_strength_1h = macd_analysis.get('signal_strength_1h', 0.5)
        signal_type_1h = macd_analysis.get('signal_type_1h', '')
        
        # 翻红/翻绿信号最强
        if signal_type_1h in ['flip_bullish', 'flip_bearish']:
            score += 0.40
        # 柱子增长次之
        elif signal_type_1h in ['red_bar_growing', 'green_bar_growing']:
            score += 0.35
        # 其他情况按强度评分
        else:
            score += 0.25 * signal_strength_1h
        
        details['1h_signal_type'] = signal_type_1h
        details['1h_signal_strength'] = signal_strength_1h
        
        # ========== 4H增强确认评分 (20%) ==========
        enhancement_score = macd_analysis.get('enhancement_score', 0)
        is_4h_enhanced = macd_analysis.get('is_4h_enhanced', False)
        
        if is_4h_enhanced:
            score += 0.20 * enhancement_score
        elif enhancement_score > 0:
            score += 0.10 * enhancement_score  # 部分增强
        
        details['4h_enhanced'] = is_4h_enhanced
        details['4h_enhancement_score'] = enhancement_score
        
        # ========== 15M跟随入场评分 (25%) ==========
        entry_score_15m = macd_analysis.get('entry_score_15m', 0.5)
        entry_type_15m = macd_analysis.get('entry_type_15m', '')
        
        # 翻红/翻绿跟随最强
        if entry_type_15m in ['flip_bullish', 'flip_bearish']:
            score += 0.25
        # 柱子增长跟随次之
        elif entry_type_15m in ['red_bar_growing', 'green_bar_growing']:
            score += 0.22
        # 稳定跟随
        elif entry_type_15m in ['red_bar_stable', 'green_bar_stable']:
            score += 0.18
        # 柱子缩短跟随（评分最低）
        elif entry_type_15m in ['red_bar_shrinking', 'green_bar_shrinking']:
            score += 0.10
        
        details['15m_entry_type'] = entry_type_15m
        details['15m_entry_score'] = entry_score_15m
        
        # ========== 成交量确认 (15%) ==========
        volume = df_15m['volume'].iloc[idx_15m]
        if idx_15m >= 20:
            avg_volume = df_15m['volume'].iloc[idx_15m-20:idx_15m].mean()
            if volume > avg_volume * 1.5:
                score += 0.15  # 显著放量
            elif volume > avg_volume:
                score += 0.10  # 放量确认
            else:
                score += 0.05  # 缩量
        
        details['volume_ratio'] = volume / avg_volume if idx_15m >= 20 else 1.0
        
        # 归一化评分
        final_score = min(score, 1.0)
        details['score'] = final_score
        
        return final_score, details
        
        return min(score, 1.0), details
    
    def calculate_dynamic_leverage(self, signal_score: float, direction: str = 'long') -> int:
        """
        根据信号评分计算动态杠杆
        
        杠杆档位：
        - 信号分 >= 0.75: 5倍杠杆
        - 信号分 >= 0.60: 4倍杠杆
        - 信号分 >= 0.45: 3倍杠杆
        
        做多时降低杠杆（做多更容易被止损）
        
        Args:
            signal_score: 信号评分 (0-1)
            direction: 方向 ('long' or 'short')
        
        Returns:
            leverage: 杠杆倍数
        """
        # 从高到低检查
        leverage = self.config.default_leverage
        for threshold, lev in sorted(
            self.config.leverage_tiers.items(), 
            key=lambda x: x[0], 
            reverse=True
        ):
            if signal_score >= threshold:
                leverage = lev
                break
        
        # 做多时降低杠杆
        if direction == 'long':
            leverage = max(2, int(leverage * self.config.long_leverage_mult))
        
        return leverage
    
    def calculate_dynamic_time_stop(
        self,
        position: Position,
        df_1h: pd.DataFrame,
        idx: int
    ) -> int:
        """
        计算动态时间止损
        
        根据市场状态调整持仓时间限制：
        - 强趋势(ADX高)：延长持仓时间
        - 弱趋势(ADX低)：缩短持仓时间
        - 高波动(ATR高)：延长持仓时间
        - 低波动(ATR低)：缩短持仓时间
        
        Returns:
            time_stop_bars: 动态时间止损K线数
        """
        base_time = self.config.base_time_stop
        
        adx = position.adx if position.adx > 0 else 25
        atr_pct = position.atr_pct if position.atr_pct > 0 else 0.01
        
        # ADX调整
        if adx > 35:
            # 强趋势，延长持仓
            adx_adj = int((adx - 35) / 5) * 2
            base_time += adx_adj
        elif adx < 25:
            # 弱趋势，缩短持仓
            adx_adj = int((25 - adx) / 3) * 2
            base_time = max(5, base_time - adx_adj)
        
        # ATR调整
        if atr_pct > 0.02:
            # 高波动，延长持仓
            atr_adj = int((atr_pct - 0.02) / 0.01) * 2
            base_time += atr_adj
        elif atr_pct < 0.005:
            # 低波动，缩短持仓
            atr_adj = int((0.005 - atr_pct) / 0.002) * 2
            base_time = max(5, base_time - atr_adj)
        
        # 信号评分调整
        if position.signal_score > 0.7:
            base_time += 3  # 高质量信号延长持仓
        elif position.signal_score < 0.45:
            base_time = max(5, base_time - 3)  # 低质量信号缩短持仓
        
        return base_time
    
    def calculate_dynamic_atr_mult(
        self,
        signal_score: float,
        atr_pct: float,
        adx: float,
        direction_4h_confidence: float
    ) -> Tuple[float, float]:
        """
        计算动态ATR倍数
        
        根据以下因素调整：
        1. 信号评分：高分收紧止损，扩大止盈
        2. ATR百分比：高波动扩大止损
        3. ADX趋势强度：强趋势扩大止盈
        
        Returns:
            sl_atr_mult: 止损ATR倍数
            tp_rr: 盈亏比
        """
        # 基础值
        sl_atr = self.config.sl_atr_base
        tp_rr = self.config.tp_rr_base
        
        # 1. 信号评分调整
        # 高分信号(>0.6)：收紧止损，扩大止盈
        if signal_score > 0.6:
            score_adj = (signal_score - 0.6) / 0.4  # 0-1
            sl_atr -= score_adj * 0.3  # 最多减少0.3
            tp_rr += score_adj * 0.8   # 最多增加0.8
        # 低分信号(0.4-0.5)：扩大止损，降低盈亏比
        elif signal_score < 0.5:
            score_adj = (0.5 - signal_score) / 0.2  # 0-0.5
            sl_atr += score_adj * 0.4  # 最多增加0.2
            tp_rr -= score_adj * 0.4   # 最多减少0.2
        
        # 2. 波动性调整（ATR百分比）
        # 高波动扩大止损
        if atr_pct > 0.02:  # >2%
            vol_adj = min(1.0, (atr_pct - 0.02) / 0.03)
            sl_atr += vol_adj * 0.5
        # 低波动收紧止损
        elif atr_pct < 0.005:  # <0.5%
            vol_adj = (0.005 - atr_pct) / 0.002
            sl_atr -= vol_adj * 0.2
        
        # 3. 趋势强度调整（ADX）
        # 强趋势扩大止盈
        if adx > 30:
            trend_adj = min(1.0, (adx - 30) / 20)
            tp_rr += trend_adj * 0.5
        # 弱趋势降低盈亏比
        elif adx < 22:
            trend_adj = (22 - adx) / 8
            tp_rr -= trend_adj * 0.3
        
        # 4. 4H方向置信度调整
        if direction_4h_confidence > 0.8:
            tp_rr += 0.2
        
        # 限制范围
        sl_atr = max(self.config.sl_atr_min, min(self.config.sl_atr_max, sl_atr))
        tp_rr = max(self.config.tp_rr_min, min(self.config.tp_rr_max, tp_rr))
        
        return sl_atr, tp_rr
    
    def analyze_4h_direction(self, df_4h: pd.DataFrame, idx: int) -> Tuple[str, int, float]:
        """
        分析4H方向
        
        Returns:
            direction: 'bullish', 'bearish', 'neutral'
            conditions_count: 满足的条件数量
            confidence: 置信度 0-1
        """
        if idx < 200:
            return 'neutral', 0, 0.0
        
        close = df_4h['close'].iloc[idx]
        ema21 = df_4h['ema21'].iloc[idx]
        ema55 = df_4h['ema55'].iloc[idx]
        ema200 = df_4h['ema200'].iloc[idx]
        macd_hist = df_4h['macd_hist'].iloc[idx]
        
        # 多头条件计数
        bullish_conditions = 0
        bullish_details = []
        if close > ema21:
            bullish_conditions += 1
            bullish_details.append('close>ema21')
        if ema21 > ema55:
            bullish_conditions += 1
            bullish_details.append('ema21>ema55')
        if close > ema200:
            bullish_conditions += 1
            bullish_details.append('close>ema200')
        if macd_hist > 0:
            bullish_conditions += 1
            bullish_details.append('macd_hist>0')
        
        # 空头条件计数
        bearish_conditions = 0
        bearish_details = []
        if close < ema21:
            bearish_conditions += 1
            bearish_details.append('close<ema21')
        if ema21 < ema55:
            bearish_conditions += 1
            bearish_details.append('ema21<ema55')
        if close < ema200:
            bearish_conditions += 1
            bearish_details.append('close<ema200')
        if macd_hist < 0:
            bearish_conditions += 1
            bearish_details.append('macd_hist<0')
        
        # 放宽条件：>=2即可判定方向
        min_conditions = self.config.min_direction_conditions
        if bullish_conditions >= min_conditions:
            confidence = bullish_conditions / 4.0
            return 'bullish', bullish_conditions, confidence
        elif bearish_conditions >= min_conditions:
            confidence = bearish_conditions / 4.0
            return 'bearish', bearish_conditions, confidence
        else:
            return 'neutral', 0, 0.0
    
    def calculate_signal_score(
        self,
        df_1h: pd.DataFrame,
        idx_1h: int,
        direction: str,
        direction_4h_confidence: float
    ) -> Tuple[float, Dict]:
        """
        计算信号评分（类似日志中的评分系统）
        
        评分维度：
        - 趋势一致性: 0.3
        - 动量强度: 0.25
        - 量价配合: 0.2
        - 技术形态: 0.15
        - 时间结构: 0.1
        
        Returns:
            score: 综合评分 0-1
            details: 评分详情
        """
        close = df_1h['close'].iloc[idx_1h]
        ema21 = df_1h['ema21'].iloc[idx_1h]
        ema55 = df_1h['ema55'].iloc[idx_1h]
        ema200 = df_1h['ema200'].iloc[idx_1h]
        macd = df_1h['macd'].iloc[idx_1h]
        macd_signal = df_1h['macd_signal'].iloc[idx_1h]
        macd_hist = df_1h['macd_hist'].iloc[idx_1h]
        volume = df_1h['volume'].iloc[idx_1h]
        
        # 计算平均成交量
        vol_lookback = min(idx_1h, 20)
        avg_volume = df_1h['volume'].iloc[idx_1h-vol_lookback:idx_1h].mean() if vol_lookback > 0 else volume
        
        details = {}
        total_score = 0.0
        
        is_long = direction == 'long'
        
        # 1. 趋势一致性 (0.3)
        trend_score = 0.0
        if is_long:
            if close > ema21:
                trend_score += 0.1
            if ema21 > ema55:
                trend_score += 0.1
            if close > ema200:
                trend_score += 0.05
            # 4H方向置信度加权
            trend_score += direction_4h_confidence * 0.05
        else:
            if close < ema21:
                trend_score += 0.1
            if ema21 < ema55:
                trend_score += 0.1
            if close < ema200:
                trend_score += 0.05
            trend_score += direction_4h_confidence * 0.05
        
        details['trend_score'] = trend_score
        total_score += trend_score
        
        # 2. 动量强度 (0.25)
        momentum_score = 0.0
        if is_long:
            if macd_hist > 0:
                momentum_score += 0.1
            if macd > macd_signal:
                momentum_score += 0.1
            # MACD柱状图递增
            if idx_1h >= 2:
                hist_prev = df_1h['macd_hist'].iloc[idx_1h-1]
                if macd_hist > hist_prev:
                    momentum_score += 0.05
        else:
            if macd_hist < 0:
                momentum_score += 0.1
            if macd < macd_signal:
                momentum_score += 0.1
            if idx_1h >= 2:
                hist_prev = df_1h['macd_hist'].iloc[idx_1h-1]
                if macd_hist < hist_prev:
                    momentum_score += 0.05
        
        details['momentum_score'] = momentum_score
        total_score += momentum_score
        
        # 3. 量价配合 (0.2)
        volume_score = 0.0
        vol_ratio = volume / avg_volume if avg_volume > 0 else 1.0
        
        if is_long:
            # 上涨放量
            if vol_ratio > 1.0:
                volume_score += 0.1
            if vol_ratio > 1.5:
                volume_score += 0.05
            # 量价齐升
            if idx_1h >= 1:
                close_prev = df_1h['close'].iloc[idx_1h-1]
                if close > close_prev and volume > avg_volume:
                    volume_score += 0.05
        else:
            # 下跌放量
            if vol_ratio > 1.0:
                volume_score += 0.1
            if vol_ratio > 1.5:
                volume_score += 0.05
            if idx_1h >= 1:
                close_prev = df_1h['close'].iloc[idx_1h-1]
                if close < close_prev and volume > avg_volume:
                    volume_score += 0.05
        
        details['volume_score'] = volume_score
        details['vol_ratio'] = vol_ratio
        total_score += volume_score
        
        # 4. 技术形态 (0.15)
        pattern_score = 0.0
        high = df_1h['high'].iloc[idx_1h]
        low = df_1h['low'].iloc[idx_1h]
        open_price = df_1h['open'].iloc[idx_1h]
        
        if is_long:
            # 看涨K线形态
            body = close - open_price
            upper_wick = high - max(close, open_price)
            lower_wick = min(close, open_price) - low
            
            # 阳线
            if close > open_price:
                pattern_score += 0.05
            # 下影线较长（支撑有效）
            if lower_wick > body * 0.5:
                pattern_score += 0.05
            # 突破前高
            if idx_1h >= 5:
                prev_high = df_1h['high'].iloc[idx_1h-5:idx_1h].max()
                if close > prev_high:
                    pattern_score += 0.05
        else:
            body = open_price - close
            upper_wick = high - max(close, open_price)
            lower_wick = min(close, open_price) - low
            
            # 阴线
            if close < open_price:
                pattern_score += 0.05
            # 上影线较长（阻力有效）
            if upper_wick > body * 0.5:
                pattern_score += 0.05
            # 跌破前低
            if idx_1h >= 5:
                prev_low = df_1h['low'].iloc[idx_1h-5:idx_1h].min()
                if close < prev_low:
                    pattern_score += 0.05
        
        details['pattern_score'] = pattern_score
        total_score += pattern_score
        
        # 5. 时间结构 (0.1)
        time_score = 0.0
        # 趋势持续性
        if idx_1h >= 3:
            ema21_prev = df_1h['ema21'].iloc[idx_1h-3]
            if is_long and ema21 > ema21_prev:
                time_score += 0.05
            elif not is_long and ema21 < ema21_prev:
                time_score += 0.05
        
        # 价格在EMA55正确一侧
        if is_long and close > ema55:
            time_score += 0.05
        elif not is_long and close < ema55:
            time_score += 0.05
        
        details['time_score'] = time_score
        total_score += time_score
        
        return total_score, details
    
    def check_entry_signal_15m(
        self,
        symbol: str,
        df_15m: pd.DataFrame,
        df_1h: pd.DataFrame,
        df_4h: pd.DataFrame,
        idx_15m: int
    ) -> Optional[Dict]:
        """
        检查入场信号（简化版 - 基于MACD方向一致性）
        
        入场条件：
        1. MACD_15M、MACD_1H(聚合)、MACD_4H(聚合)三个方向相同
        2. 信号评分 >= 最低阈值
        
        Args:
            symbol: 交易对
            df_15m: 15分钟K线数据
            df_1h: 1小时K线数据
            df_4h: 4小时K线数据
            idx_15m: 当前15分钟K线索引
        
        Returns:
            入场信号字典或None
        """
        # 检查MACD方向一致性
        direction = self.check_macd_alignment(df_15m, df_1h, df_4h, idx_15m)
        if direction is None:
            return None
        
        # 避免同时开多仓
        current_long_positions = sum(1 for pos in self.positions.values() if pos.side == 'long')
        current_short_positions = sum(1 for pos in self.positions.values() if pos.side == 'short')
        
        if direction == 'long' and current_long_positions >= self.config.max_same_direction_entry:
            return None
        elif direction == 'short' and current_short_positions >= self.config.max_same_direction_entry:
            return None
        
        # 计算信号评分
        signal_score, score_details = self.calculate_entry_signal_score(
            df_15m, df_1h, df_4h, idx_15m, direction
        )
        
        # 最低评分过滤（降低阈值以增加交易机会）
        min_score = 0.35  # 简化版降低阈值
        if signal_score < min_score:
            return None
        
        # 获取当前价格和指标
        close_15m = df_15m['close'].iloc[idx_15m]
        atr_15m = df_15m['atr'].iloc[idx_15m] if 'atr' in df_15m.columns else 0
        
        # 如果15M没有ATR，使用1H的ATR
        if atr_15m == 0:
            current_time = df_15m['timestamp'].iloc[idx_15m]
            mask_1h = (df_1h['timestamp'] <= current_time)
            if mask_1h.any():
                idx_1h = mask_1h[mask_1h].index[-1]
                atr_15m = df_1h['atr'].iloc[idx_1h]
        
        # 获取ADX
        adx = 25.0  # 默认值
        if 'adx' in df_15m.columns:
            adx = df_15m['adx'].iloc[idx_15m]
        
        # 计算止损止盈
        atr_pct = atr_15m / close_15m if close_15m > 0 else 0.01
        
        # 动态ATR倍数
        sl_atr_mult = 1.5  # 简化版固定值
        tp_rr = 2.0  # 简化版固定盈亏比
        
        # 根据信号评分调整
        if signal_score >= 0.6:
            sl_atr_mult = 1.3
            tp_rr = 2.5
        elif signal_score >= 0.5:
            sl_atr_mult = 1.4
            tp_rr = 2.2
        
        # 计算止损止盈价格
        if direction == 'long':
            stop_loss = close_15m - atr_15m * sl_atr_mult
            take_profit = close_15m + atr_15m * sl_atr_mult * tp_rr
        else:
            stop_loss = close_15m + atr_15m * sl_atr_mult
            take_profit = close_15m - atr_15m * sl_atr_mult * tp_rr
        
        # 计算动态杠杆
        leverage = self.calculate_dynamic_leverage(signal_score)
        
        # 计算仓位大小
        base_risk_pct = 0.01
        risk_multiplier = min(1.5, 0.5 + signal_score)
        risk_pct = base_risk_pct * risk_multiplier
        
        risk_amount = self.available_capital * risk_pct
        risk_per_unit = abs(close_15m - stop_loss)
        position_size = risk_amount / risk_per_unit if risk_per_unit > 0 else 0
        
        # 限制仓位大小
        max_position = self.available_capital * self.config.max_position_pct / leverage
        min_position = self.available_capital * self.config.min_position_pct / leverage
        position_size = max(min(position_size * close_15m, max_position), min_position)
        
        # 单笔最大亏损限制
        expected_loss = position_size * abs(close_15m - stop_loss) / close_15m * leverage
        max_allowed_loss = self.available_capital * self.config.max_single_loss_pct
        
        if expected_loss > max_allowed_loss:
            position_size = max_allowed_loss * close_15m / (abs(close_15m - stop_loss) * leverage)
            position_size = max(min(position_size, max_position), min_position)
        
        return {
            'symbol': symbol,
            'direction': direction,
            'entry_price': close_15m,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'position_size': position_size,
            'leverage': leverage,
            'entry_score': int(signal_score * 100),
            'signal_score': signal_score,
            'score_details': score_details,
            'conflict_level': '',
            'direction_4h_conditions': 0,
            'direction_4h_confidence': 0.0,
            'atr': atr_15m,
            'atr_pct': atr_pct,
            'adx': adx,
            'sl_atr_mult': sl_atr_mult,
            'tp_rr': tp_rr,
            'vol_details': {},
            'timestamp': df_15m['timestamp'].iloc[idx_15m]
        }
    
    def check_entry_signal(
        self,
        symbol: str,
        df_1h: pd.DataFrame,
        df_4h: pd.DataFrame,
        idx_1h: int,
        idx_4h: int
    ) -> Optional[Dict]:
        """检查入场信号（优化版 - 放宽条件）"""
        
        # 获取4H方向（放宽条件）
        direction_4h, conditions_4h, confidence_4h = self.analyze_4h_direction(df_4h, idx_4h)
        if direction_4h == 'neutral':
            return None
        
        # 1H指标
        close_1h = df_1h['close'].iloc[idx_1h]
        ema55_1h = df_1h['ema55'].iloc[idx_1h]
        macd_hist_1h = df_1h['macd_hist'].iloc[idx_1h]
        
        # 1H方向判断
        is_1h_bullish = close_1h > ema55_1h  # 放宽：不再要求MACD
        is_1h_bearish = close_1h < ema55_1h
        
        # 冲突检测（优化：允许弱冲突）
        conflict_level = None
        conflict_penalty = 0
        
        if direction_4h == 'bullish' and is_1h_bearish:
            # 4H多头 vs 1H空头
            if self.config.allow_weak_conflict and macd_hist_1h > -abs(macd_hist_1h) * 0.5:
                # 弱冲突：1H MACD没有强烈看空
                conflict_level = 'weak'
                conflict_penalty = self.config.conflict_score_penalty
            else:
                # 强冲突：跳过
                return None
        elif direction_4h == 'bearish' and is_1h_bullish:
            # 4H空头 vs 1H多头
            if self.config.allow_weak_conflict and macd_hist_1h < abs(macd_hist_1h) * 0.5:
                conflict_level = 'weak'
                conflict_penalty = self.config.conflict_score_penalty
            else:
                return None
        
        direction = 'long' if direction_4h == 'bullish' else 'short'
        
        # 检查1H企稳（降低门槛）
        lookback = min(idx_1h, 50)
        close_window = df_1h['close'].iloc[idx_1h-lookback:idx_1h+1].values
        high_window = df_1h['high'].iloc[idx_1h-lookback:idx_1h+1].values
        low_window = df_1h['low'].iloc[idx_1h-lookback:idx_1h+1].values
        volume_window = df_1h['volume'].iloc[idx_1h-lookback:idx_1h+1].values
        
        stabilization = self.scorer.analyze(
            close_window, high_window, low_window, volume_window,
            direction=direction
        )
        
        # 降低企稳门槛
        effective_threshold = self.config.min_stabilization_score
        if conflict_level == 'weak':
            effective_threshold += 10  # 冲突时提高门槛
        
        if stabilization.total_score < effective_threshold:
            return None
        
        # 计算信号评分
        signal_score, score_details = self.calculate_signal_score(
            df_1h, idx_1h, direction, confidence_4h
        )
        
        # 冲突扣分
        if conflict_level == 'weak':
            signal_score = max(0, signal_score - conflict_penalty / 100.0)
        
        # 最低评分过滤
        if signal_score < self.config.min_signal_score:
            return None
        
        # ========== 避免同时开多仓 ==========
        current_long_positions = sum(1 for pos in self.positions.values() if pos.side == 'long')
        current_short_positions = sum(1 for pos in self.positions.values() if pos.side == 'short')
        
        if direction == 'long' and current_long_positions >= self.config.max_same_direction_entry:
            return None
        elif direction == 'short' and current_short_positions >= self.config.max_same_direction_entry:
            return None
        
        # ========== 波动性过滤 ==========
        vol_passed, vol_details = self.check_volatility_filter(df_1h, idx_1h)
        if not vol_passed:
            return None
        
        # ========== K线形态检查 ==========
        pattern_passed, pattern_details = self.check_candle_pattern(df_1h, idx_1h, direction)
        if not pattern_passed:
            return None
        
        # ========== 价格位置过滤（避免追高杀跌）==========
        lookback = min(idx_1h, getattr(self.config, 'lookback_period', 24))
        if lookback >= 10:
            high_window = df_1h['high'].iloc[idx_1h-lookback:idx_1h+1].values
            low_window = df_1h['low'].iloc[idx_1h-lookback:idx_1h+1].values
            period_high = high_window.max()
            period_low = low_window.min()
            period_range = period_high - period_low
            
            if period_range > 0:
                price_position = (close_1h - period_low) / period_range
                
                if direction == 'long':
                    # 做多时，价格不能在区间顶部（避免追高）
                    if price_position > getattr(self.config, 'price_position_max', 0.7):
                        return None
                else:
                    # 做空时，价格不能在区间底部（避免追空）
                    if price_position < getattr(self.config, 'price_position_min', 0.3):
                        return None
        
        # ========== RSI过滤 ==========
        rsi = df_1h['rsi'].iloc[idx_1h] if 'rsi' in df_1h.columns else 50
        if direction == 'long':
            # 做多时RSI不能太低（超卖可能继续跌）或太高（超买会回调）
            min_rsi = getattr(self.config, 'long_min_rsi', 40.0)
            max_rsi = getattr(self.config, 'long_max_rsi', 70.0)
            if rsi < min_rsi or rsi > max_rsi:
                return None
        else:
            # 做空时RSI不能太高
            max_rsi = getattr(self.config, 'short_max_rsi', 60.0)
            if rsi > max_rsi:
                return None
        
        # ========== 突破确认检查 ==========
        breakout_confirmed, breakout_details = self.check_breakout_confirmation(df_1h, idx_1h, direction)
        
        # 如果刚突破未回踩，降低信号评分
        if breakout_details.get('breakout_fresh') or breakout_details.get('breakdown_fresh'):
            signal_score *= 0.85  # 扣分15%
            if signal_score < self.config.min_signal_score:
                return None
        
        # ========== 动态ATR止盈止损 ==========
        atr = df_1h['atr'].iloc[idx_1h]
        adx = df_1h['adx'].iloc[idx_1h]
        atr_pct = atr / close_1h if close_1h > 0 else 0
        
        # 计算动态ATR倍数
        sl_atr_mult, tp_rr = self.calculate_dynamic_atr_mult(
            signal_score, atr_pct, adx, confidence_4h
        )
        
        # 做多使用更宽的止损（避免被轻易击穿）
        if direction == 'long':
            sl_atr_mult *= getattr(self.config, 'sl_atr_long_mult', 1.2)
        
        if direction == 'long':
            stop_loss = close_1h - atr * sl_atr_mult
            take_profit = close_1h + atr * sl_atr_mult * tp_rr
        else:
            stop_loss = close_1h + atr * sl_atr_mult
            take_profit = close_1h - atr * sl_atr_mult * tp_rr
        
        # ========== 动态杠杆计算 ==========
        leverage = self.calculate_dynamic_leverage(signal_score, direction)
        
        # 计算仓位大小（根据信号评分和杠杆调整）
        # 基础风险：每笔交易风险1%（杠杆下实际风险 = 名义风险 * 杠杆）
        base_risk_pct = 0.01
        # 评分越高，风险比例越大
        risk_multiplier = min(1.5, 0.5 + signal_score)
        # 实际风险 = 基础风险 * 杠杆倍数（因为杠杆放大了盈亏）
        # 为保持实际风险不变，需要调整仓位
        risk_pct = base_risk_pct * risk_multiplier
        
        risk_amount = self.available_capital * risk_pct
        risk_per_unit = abs(close_1h - stop_loss)
        position_size = risk_amount / risk_per_unit if risk_per_unit > 0 else 0
        
        # 限制仓位大小（考虑杠杆：实际仓位 = 名义仓位 / 杠杆）
        max_position = self.available_capital * self.config.max_position_pct / leverage
        min_position = self.available_capital * self.config.min_position_pct / leverage
        position_size = max(min(position_size * close_1h, max_position), min_position)
        
        # ========== 单笔最大亏损限制 ==========
        # 计算预期最大亏损（考虑杠杆）
        expected_loss = position_size * abs(close_1h - stop_loss) / close_1h * leverage
        max_allowed_loss = self.available_capital * self.config.max_single_loss_pct
        
        if expected_loss > max_allowed_loss:
            # 缩小仓位以满足最大亏损限制
            position_size = max_allowed_loss * close_1h / (abs(close_1h - stop_loss) * leverage)
            position_size = max(min(position_size, max_position), min_position)
        
        return {
            'symbol': symbol,
            'direction': direction,
            'entry_price': close_1h,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'position_size': position_size,
            'leverage': leverage,  # 新增杠杆字段
            'entry_score': stabilization.total_score,
            'signal_score': signal_score,
            'score_details': score_details,
            'conflict_level': conflict_level or '',
            'direction_4h_conditions': conditions_4h,
            'direction_4h_confidence': confidence_4h,
            'atr': atr,
            'atr_pct': atr_pct,
            'adx': adx,
            'sl_atr_mult': sl_atr_mult,
            'tp_rr': tp_rr,
            'vol_details': vol_details,
            'timestamp': df_1h['timestamp'].iloc[idx_1h]
        }
    
    def check_exit_signal_15m(
        self,
        position: Position,
        df_15m: pd.DataFrame,
        idx: int
    ) -> Optional[str]:
        """检查出场信号（15M版本）"""
        # 边界检查
        if idx >= len(df_15m) or idx < 0:
            return None
            
        close = df_15m['close'].iloc[idx]
        high = df_15m['high'].iloc[idx]
        low = df_15m['low'].iloc[idx]
        
        # 更新最大盈利和回撤
        if position.side == 'long':
            profit_pct = (high - position.entry_price) / position.entry_price
            drawdown_pct = (position.entry_price - low) / position.entry_price
            
            if profit_pct > position.max_profit_pct:
                position.max_profit_pct = profit_pct
            if drawdown_pct > position.max_drawdown_pct:
                position.max_drawdown_pct = drawdown_pct
            
            # 止盈
            if high >= position.take_profit:
                return 'take_profit'
            # 止损
            if low <= position.stop_loss:
                return 'stop_loss'
            # MACD方向反转（15M）
            macd_hist = df_15m['macd_hist'].iloc[idx]
            if macd_hist < 0 and position.bars_held >= 4:  # 持仓至少1小时
                # 检查MACD是否持续为负
                if idx >= 2 and idx < len(df_15m):
                    prev_hist = df_15m['macd_hist'].iloc[idx-1]
                    if prev_hist < 0:
                        return 'macd_reversal'
                
        else:  # short
            profit_pct = (position.entry_price - low) / position.entry_price
            drawdown_pct = (high - position.entry_price) / position.entry_price
            
            if profit_pct > position.max_profit_pct:
                position.max_profit_pct = profit_pct
            if drawdown_pct > position.max_drawdown_pct:
                position.max_drawdown_pct = drawdown_pct
            
            # 止盈
            if low <= position.take_profit:
                return 'take_profit'
            # 止损
            if high >= position.stop_loss:
                return 'stop_loss'
            # MACD方向反转
            macd_hist = df_15m['macd_hist'].iloc[idx]
            if macd_hist > 0 and position.bars_held >= 4:
                if idx >= 2 and idx < len(df_15m):
                    prev_hist = df_15m['macd_hist'].iloc[idx-1]
                    if prev_hist > 0:
                        return 'macd_reversal'
        
        # 动态时间止损（15M版本：40根15M K线 = 10小时）
        dynamic_time_stop = 40  # 固定40根15M K线
        if position.bars_held >= dynamic_time_stop:
            if position.side == 'long' and close <= position.entry_price:
                return 'time_stop'
            elif position.side == 'short' and close >= position.entry_price:
                return 'time_stop'
        
        return None
    
    def check_exit_signal(
        self,
        position: Position,
        df_1h: pd.DataFrame,
        idx: int
    ) -> Optional[str]:
        """检查出场信号"""
        close = df_1h['close'].iloc[idx]
        high = df_1h['high'].iloc[idx]
        low = df_1h['low'].iloc[idx]
        
        # 更新最大盈利和回撤
        if position.side == 'long':
            profit_pct = (high - position.entry_price) / position.entry_price
            drawdown_pct = (position.entry_price - low) / position.entry_price
            
            if profit_pct > position.max_profit_pct:
                position.max_profit_pct = profit_pct
            if drawdown_pct > position.max_drawdown_pct:
                position.max_drawdown_pct = drawdown_pct
            
            # 止盈
            if high >= position.take_profit:
                return 'take_profit'
            # 止损
            if low <= position.stop_loss:
                return 'stop_loss'
            # 跌破EMA55
            ema55 = df_1h['ema55'].iloc[idx]
            if close < ema55 and position.entry_price > ema55:
                return 'trend_reversal'
                
        else:  # short
            profit_pct = (position.entry_price - low) / position.entry_price
            drawdown_pct = (high - position.entry_price) / position.entry_price
            
            if profit_pct > position.max_profit_pct:
                position.max_profit_pct = profit_pct
            if drawdown_pct > position.max_drawdown_pct:
                position.max_drawdown_pct = drawdown_pct
            
            # 止盈
            if low <= position.take_profit:
                return 'take_profit'
            # 止损
            if high >= position.stop_loss:
                return 'stop_loss'
            # 突破EMA55
            ema55 = df_1h['ema55'].iloc[idx]
            if close > ema55 and position.entry_price < ema55:
                return 'trend_reversal'
        
        # 动态时间止损（根据市场状态调整持仓时间）
        dynamic_time_stop = self.calculate_dynamic_time_stop(position, df_1h, idx)
        if position.bars_held >= dynamic_time_stop:
            if position.side == 'long' and close <= position.entry_price:
                return 'time_stop'
            elif position.side == 'short' and close >= position.entry_price:
                return 'time_stop'
        
        return None
    
    def execute_entry(self, signal: Dict) -> None:
        """执行入场"""
        symbol = signal['symbol']
        
        # 检查是否已有持仓
        if symbol in self.positions:
            return
        
        # 检查持仓数量限制
        if len(self.positions) >= self.config.max_positions:
            return
        
        # 检查资金是否足够
        if signal['position_size'] > self.available_capital:
            return
        
        # 创建持仓
        position = Position(
            symbol=symbol,
            side=signal['direction'],
            entry_price=signal['entry_price'],
            size=signal['position_size'],
            stop_loss=signal['stop_loss'],
            take_profit=signal['take_profit'],
            entry_time=signal['timestamp'],
            entry_score=signal['entry_score'],
            signal_score=signal['signal_score'],
            conflict_level=signal['conflict_level'],
            direction_4h_confidence=signal['direction_4h_confidence'],
            leverage=signal['leverage'],
            sl_atr_mult=signal['sl_atr_mult'],
            tp_rr=signal['tp_rr'],
            adx=signal['adx'],
            atr_pct=signal['atr_pct']
        )
        
        self.positions[symbol] = position
        self.available_capital -= signal['position_size']
        
        conflict_str = f" [冲突:{signal['conflict_level']}]" if signal['conflict_level'] else ""
        print(f"  [ENTRY] {symbol} {signal['direction']} @ {signal['entry_price']:.4f}, "
              f"仓位: {signal['position_size']:.2f} USDT, 信号分: {signal['signal_score']:.2f}, "
              f"ADX: {signal['adx']:.1f}, SL: {signal['sl_atr_mult']:.2f}ATR, RR: {signal['tp_rr']:.1f}{conflict_str}")
    
    def execute_exit(
        self,
        position: Position,
        exit_price: float,
        exit_reason: str,
        exit_time: datetime
    ) -> None:
        """执行出场"""
        # 计算盈亏（加入杠杆）
        # 盈亏 = 价格变动% * 仓位 * 杠杆
        if position.side == 'long':
            price_change_pct = (exit_price - position.entry_price) / position.entry_price
        else:
            price_change_pct = (position.entry_price - exit_price) / position.entry_price
        
        # 杠杆放大盈亏
        pnl = price_change_pct * position.size * position.leverage
        pnl_pct = price_change_pct * position.leverage
        
        # 扣除手续费（手续费也受杠杆影响）
        fee = position.size * self.config.fee_rate * 2  # 开仓+平仓
        pnl -= fee
        
        # 创建交易记录
        trade = Trade(
            symbol=position.symbol,
            side=position.side,
            entry_price=position.entry_price,
            exit_price=exit_price,
            size=position.size,
            pnl=pnl,
            pnl_pct=pnl_pct,
            entry_time=position.entry_time,
            exit_time=exit_time,
            bars_held=position.bars_held,
            exit_reason=exit_reason,
            entry_score=position.entry_score,
            signal_score=position.signal_score,
            conflict_level=position.conflict_level,
            max_profit_pct=position.max_profit_pct,
            max_drawdown_pct=position.max_drawdown_pct,
            leverage=position.leverage,
            sl_atr_mult=position.sl_atr_mult,
            tp_rr=position.tp_rr,
            adx=position.adx,
            atr_pct=position.atr_pct
        )
        
        self.trades.append(trade)
        
        # 更新资金
        self.available_capital += position.size + pnl
        self.capital += pnl
        
        # 移除持仓
        del self.positions[position.symbol]
        
        pnl_sign = "+" if pnl >= 0 else ""
        print(f"  [EXIT] {position.symbol} {exit_reason} @ {exit_price:.2f}, "
              f"PnL: {pnl_sign}{pnl:.2f} USDT ({pnl_sign}{pnl_pct*100:.2f}%)")
    
    def run(self) -> BacktestResult:
        """运行回测"""
        print(f"\n{'='*60}")
        print("开始回测")
        print(f"{'='*60}")
        print(f"币种: {', '.join(self.config.symbols)}")
        print(f"天数: {self.config.days}")
        print(f"最大持仓: {self.config.max_positions}")
        print(f"初始资金: {self.config.initial_capital:.2f} USDT")
        
        # 获取数据
        self.fetch_all_data()
        
        if not self.data:
            print("[ERROR] 没有获取到任何数据")
            return None
        
        # 计算指标
        print(f"\n{'='*60}")
        print("计算技术指标")
        print(f"{'='*60}")
        
        for symbol, tfs in self.data.items():
            for tf, df in tfs.items():
                self.data[symbol][tf] = self.calculate_indicators(df)
            print(f"  [OK] {symbol} 指标计算完成")
        
        # 对齐时间（使用15M时间序列）
        start_date = max(
            tfs[self.config.entry_timeframe]['timestamp'].min() 
            for tfs in self.data.values()
        )
        end_date = min(
            tfs[self.config.entry_timeframe]['timestamp'].max() 
            for tfs in self.data.values()
        )
        
        # 只回测配置的天数
        if (end_date - start_date).days > self.config.days:
            start_date = end_date - timedelta(days=self.config.days)
        
        print(f"\n回测区间: {start_date.strftime('%Y-%m-%d %H:%M')} ~ {end_date.strftime('%Y-%m-%d %H:%M')}")
        print(f"入场周期: {self.config.entry_timeframe} (MACD方向一致性)")
        
        # 主回测循环
        print(f"\n{'='*60}")
        print("执行回测")
        print(f"{'='*60}")
        
        # 获取15M时间序列
        entry_tf = self.config.entry_timeframe
        first_symbol = list(self.data.keys())[0]
        timestamps = self.data[first_symbol][entry_tf]['timestamp']
        timestamps = timestamps[(timestamps >= start_date) & (timestamps <= end_date)]
        
        for i, ts in enumerate(timestamps):
            if i % 200 == 0:
                print(f"  处理进度: {i}/{len(timestamps)} ({i/len(timestamps)*100:.1f}%)")
            
            # 更新持仓（每15分钟更新一次）
            for symbol, pos in list(self.positions.items()):
                pos.bars_held += 1
            
            # 检查每个币种
            for symbol in self.data.keys():
                df_15m = self.data[symbol].get(self.config.entry_timeframe)
                df_1h = self.data[symbol].get(self.config.primary_timeframe)
                df_4h = self.data[symbol].get(self.config.higher_timeframe)
                
                if df_15m is None or df_1h is None or df_4h is None:
                    continue
                
                # 找到当前15M时间点的索引
                mask_15m = df_15m['timestamp'] == ts
                if not mask_15m.any():
                    continue
                
                idx_15m = mask_15m.idxmax()
                
                # 找到对应的1H K线
                ts_1h = ts.floor('h')
                mask_1h = df_1h['timestamp'] == ts_1h
                if not mask_1h.any():
                    mask_1h = df_1h['timestamp'] <= ts_1h
                    if not mask_1h.any():
                        continue
                    idx_1h = mask_1h.idxmax()
                else:
                    idx_1h = mask_1h.idxmax()
                
                # 找到对应的4H K线
                ts_4h = ts.floor('4h')
                mask_4h = df_4h['timestamp'] == ts_4h
                if not mask_4h.any():
                    mask_4h = df_4h['timestamp'] <= ts_4h
                    if not mask_4h.any():
                        continue
                    idx_4h = mask_4h.idxmax()
                else:
                    idx_4h = mask_4h.idxmax()
                
                # 检查出场（使用15M价格）
                if symbol in self.positions:
                    exit_reason = self.check_exit_signal_15m(
                        self.positions[symbol], df_15m, idx_15m
                    )
                    if exit_reason:
                        close = df_15m['close'].iloc[idx_15m]
                        self.execute_exit(self.positions[symbol], close, exit_reason, ts)
                
                # 检查入场（使用新的15M入场信号）
                if symbol not in self.positions and len(self.positions) < self.config.max_positions:
                    signal = self.check_entry_signal_15m(
                        symbol, df_15m, df_1h, df_4h, idx_15m
                    )
                    if signal:
                        self.execute_entry(signal)
            
            # 记录权益（每小时记录一次，减少数据量）
            if ts.minute == 0:  # 只在整点记录
                equity = self.capital
                for pos in self.positions.values():
                    symbol = pos.symbol
                    df = self.data[symbol][self.config.entry_timeframe]
                    mask = df['timestamp'] == ts
                    if mask.any():
                        current_price = df.loc[mask, 'close'].iloc[0]
                        if pos.side == 'long':
                            equity += (current_price - pos.entry_price) / pos.entry_price * pos.size * pos.leverage
                        else:
                            equity += (pos.entry_price - current_price) / pos.entry_price * pos.size * pos.leverage
                
                self.equity_curve.append({
                    'timestamp': ts,
                    'equity': equity,
                    'positions': len(self.positions)
                })
        
        # 平掉所有剩余持仓
        print(f"\n平仓剩余持仓...")
        for symbol, pos in list(self.positions.items()):
            df = self.data[symbol][self.config.primary_timeframe]
            close = df['close'].iloc[-1]
            self.execute_exit(pos, close, 'end_of_backtest', df['timestamp'].iloc[-1])
        
        # 计算结果
        return self._calculate_result(start_date, end_date)
    
    def _calculate_result(
        self,
        start_date: datetime,
        end_date: datetime
    ) -> BacktestResult:
        """计算回测结果"""
        result = BacktestResult(
            start_date=start_date,
            end_date=end_date,
            initial_capital=self.config.initial_capital,
            final_capital=self.capital
        )
        
        if not self.trades:
            print("\n[WARN] 没有交易记录")
            return result
        
        # 基本统计
        result.total_trades = len(self.trades)
        result.winning_trades = sum(1 for t in self.trades if t.pnl > 0)
        result.losing_trades = sum(1 for t in self.trades if t.pnl <= 0)
        result.win_rate = result.winning_trades / result.total_trades
        
        # 盈亏统计
        result.total_pnl = sum(t.pnl for t in self.trades)
        result.total_pnl_pct = result.total_pnl / self.config.initial_capital
        
        wins = [t.pnl for t in self.trades if t.pnl > 0]
        losses = [abs(t.pnl) for t in self.trades if t.pnl <= 0]
        
        result.avg_win = np.mean(wins) if wins else 0
        result.avg_loss = np.mean(losses) if losses else 0
        
        total_wins = sum(wins)
        total_losses = sum(losses)
        result.profit_factor = total_wins / total_losses if total_losses > 0 else float('inf')
        
        # 计算Sharpe比率
        if self.equity_curve:
            returns = []
            for i in range(1, len(self.equity_curve)):
                ret = (self.equity_curve[i]['equity'] - self.equity_curve[i-1]['equity']) / self.equity_curve[i-1]['equity']
                returns.append(ret)
            
            if returns:
                result.sharpe_ratio = np.mean(returns) / np.std(returns) * np.sqrt(24 * 365) if np.std(returns) > 0 else 0
        
        # 计算最大回撤
        if self.equity_curve:
            peak = self.config.initial_capital
            max_dd = 0
            max_dd_pct = 0
            
            for eq in self.equity_curve:
                if eq['equity'] > peak:
                    peak = eq['equity']
                dd = peak - eq['equity']
                dd_pct = dd / peak
                if dd > max_dd:
                    max_dd = dd
                    max_dd_pct = dd_pct
            
            result.max_drawdown = max_dd
            result.max_drawdown_pct = max_dd_pct
        
        # 按币种统计
        for symbol in self.config.symbols:
            symbol_trades = [t for t in self.trades if t.symbol == symbol]
            if symbol_trades:
                result.symbol_stats[symbol] = {
                    'trades': len(symbol_trades),
                    'wins': sum(1 for t in symbol_trades if t.pnl > 0),
                    'win_rate': sum(1 for t in symbol_trades if t.pnl > 0) / len(symbol_trades),
                    'total_pnl': sum(t.pnl for t in symbol_trades),
                    'avg_pnl': np.mean([t.pnl for t in symbol_trades])
                }
        
        # 按冲突等级统计（如果有）
        for level in ['weak', 'medium', 'strong', '']:
            level_trades = [t for t in self.trades if t.conflict_level == level]
            if level_trades:
                label = level if level else 'normal'
                result.conflict_level_stats[label] = {
                    'trades': len(level_trades),
                    'wins': sum(1 for t in level_trades if t.pnl > 0),
                    'win_rate': sum(1 for t in level_trades if t.pnl > 0) / len(level_trades),
                    'total_pnl': sum(t.pnl for t in level_trades),
                    'avg_signal_score': np.mean([t.signal_score for t in level_trades])
                }
        
        # 按信号评分分组统计
        score_ranges = [('high', 0.6, 1.0), ('medium', 0.4, 0.6), ('low', 0.3, 0.4)]
        for label, low, high in score_ranges:
            score_trades = [t for t in self.trades if low <= t.signal_score < high]
            if score_trades:
                result.conflict_level_stats[f'score_{label}'] = {
                    'trades': len(score_trades),
                    'wins': sum(1 for t in score_trades if t.pnl > 0),
                    'win_rate': sum(1 for t in score_trades if t.pnl > 0) / len(score_trades),
                    'total_pnl': sum(t.pnl for t in score_trades),
                    'avg_signal_score': np.mean([t.signal_score for t in score_trades])
                }
        
        result.trades = self.trades
        result.equity_curve = self.equity_curve
        
        return result
    
    def generate_report(self, result: BacktestResult) -> str:
        """生成回测报告"""
        lines = []
        lines.append("=" * 70)
        lines.append("多币种回测报告")
        lines.append("=" * 70)
        lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")
        
        # 基本信息
        lines.append("## 一、回测配置")
        lines.append("-" * 40)
        lines.append(f"币种数量: {len(self.config.symbols)} 个")
        lines.append(f"回测区间: {result.start_date.strftime('%Y-%m-%d')} ~ {result.end_date.strftime('%Y-%m-%d')}")
        lines.append(f"初始资金: {result.initial_capital:,.2f} USDT")
        lines.append(f"最大持仓: {self.config.max_positions} 个")
        lines.append("")
        lines.append("入场条件（优化后）:")
        lines.append(f"  - 企稳门槛: {self.config.min_stabilization_score}分")
        lines.append(f"  - 4H方向条件: >= {self.config.min_direction_conditions}项")
        lines.append(f"  - 允许弱冲突: {self.config.allow_weak_conflict}")
        lines.append(f"  - 最低信号评分: {self.config.min_signal_score}")
        lines.append("")
        lines.append("波动性过滤（避免震荡）:")
        lines.append(f"  - 启用: {self.config.use_volatility_filter}")
        lines.append(f"  - ADX范围: {self.config.min_adx} ~ {self.config.max_adx} (趋势强度)")
        lines.append(f"  - ATR%范围: {self.config.min_atr_pct*100:.1f}% ~ {self.config.max_atr_pct*100:.1f}%")
        lines.append("")
        lines.append("K线形态过滤（避免追高/假突破）:")
        lines.append(f"  - 启用: {self.config.check_candle_pattern}")
        lines.append(f"  - 最大实体占比: {self.config.max_body_ratio:.0%}")
        lines.append(f"  - 最小下影线占比: {self.config.min_lower_wick_ratio:.0%}")
        lines.append(f"  - 突破回踩确认: {self.config.require_pullback}")
        lines.append("")
        lines.append("动态ATR止盈止损:")
        lines.append(f"  - 止损ATR倍数: {self.config.sl_atr_min} ~ {self.config.sl_atr_max}")
        lines.append(f"  - 盈亏比范围: {self.config.tp_rr_min} ~ {self.config.tp_rr_max}")
        lines.append(f"  - 动态时间止损: 基础{self.config.base_time_stop}小时(根据ADX/ATR调整)")
        lines.append(f"  - 单笔最大亏损: {self.config.max_single_loss_pct*100:.1f}%")
        lines.append("")
        lines.append("动态杠杆配置:")
        lines.append(f"  信号分 >= 0.75: 8倍杠杆")
        lines.append(f"  信号分 >= 0.60: 6倍杠杆")
        lines.append(f"  信号分 >= 0.45: 4倍杠杆")
        lines.append(f"  同向最大持仓数: {self.config.max_same_direction_entry}")
        lines.append(f"  本地数据缓存: {self.config.local_data_dir}")
        lines.append("")
        
        # 总体表现
        lines.append("## 二、总体表现")
        lines.append("-" * 40)
        lines.append(f"最终资金: {result.final_capital:,.2f} USDT")
        lines.append(f"总盈亏: {result.total_pnl:+,.2f} USDT ({result.total_pnl_pct*100:+.2f}%)")
        lines.append(f"最大回撤: {result.max_drawdown:,.2f} USDT ({result.max_drawdown_pct*100:.2f}%)")
        lines.append(f"夏普比率: {result.sharpe_ratio:.2f}")
        lines.append("")
        
        # 交易统计
        lines.append("## 三、交易统计")
        lines.append("-" * 40)
        lines.append(f"总交易数: {result.total_trades}")
        lines.append(f"盈利交易: {result.winning_trades}")
        lines.append(f"亏损交易: {result.losing_trades}")
        lines.append(f"胜率: {result.win_rate*100:.2f}%")
        lines.append(f"平均盈利: {result.avg_win:+,.2f} USDT")
        lines.append(f"平均亏损: {result.avg_loss:,.2f} USDT")
        lines.append(f"盈亏比: {result.profit_factor:.2f}")
        lines.append("")
        
        # 按币种统计
        if result.symbol_stats:
            lines.append("## 四、分币种表现")
            lines.append("-" * 40)
            for symbol, stats in result.symbol_stats.items():
                pnl_sign = "+" if stats['total_pnl'] >= 0 else ""
                lines.append(f"{symbol}:")
                lines.append(f"  交易次数: {stats['trades']}")
                lines.append(f"  胜率: {stats['win_rate']*100:.1f}%")
                lines.append(f"  总盈亏: {pnl_sign}{stats['total_pnl']:,.2f} USDT")
                lines.append(f"  平均盈亏: {stats['avg_pnl']:+,.2f} USDT")
            lines.append("")
        
        # 交易记录（最近15笔）
        if result.trades:
            lines.append("## 五、最近交易记录")
            lines.append("-" * 40)
            lines.append(f"{'时间':^12} | {'币种':^10} | {'方向':^5} | {'信号分':^5} | {'ADX':^5} | {'SL':^5} | {'RR':^4} | {'出场原因':^12} | {'PnL':^10}")
            lines.append("-" * 95)
            for trade in result.trades[-15:]:
                pnl_sign = "+" if trade.pnl >= 0 else ""
                conflict_str = f"[{trade.conflict_level}]" if trade.conflict_level else ""
                lines.append(
                    f"{trade.entry_time.strftime('%m-%d %H:%M'):^12} | {trade.symbol:^10} | "
                    f"{trade.side:^5} | {trade.signal_score:^5.2f} | {trade.adx:^5.1f} | "
                    f"{trade.sl_atr_mult:^5.2f} | {trade.tp_rr:^4.1f} | {trade.exit_reason:^12} | "
                    f"{pnl_sign}{trade.pnl:^+8.2f} {conflict_str}"
                )
            lines.append("")
            
            # 按ADX分组统计
            lines.append("## 六、按ADX趋势强度统计")
            lines.append("-" * 40)
            adx_high = [t for t in result.trades if t.adx >= 30]
            adx_mid = [t for t in result.trades if 20 <= t.adx < 30]
            adx_low = [t for t in result.trades if t.adx < 20]
            
            if adx_high:
                wins = sum(1 for t in adx_high if t.pnl > 0)
                lines.append(f"强趋势(ADX>=30): {len(adx_high)}笔, 胜率{wins/len(adx_high)*100:.1f}%, "
                           f"盈亏{sum(t.pnl for t in adx_high):+.2f}")
            if adx_mid:
                wins = sum(1 for t in adx_mid if t.pnl > 0)
                lines.append(f"中等趋势(20<=ADX<30): {len(adx_mid)}笔, 胜率{wins/len(adx_mid)*100:.1f}%, "
                           f"盈亏{sum(t.pnl for t in adx_mid):+.2f}")
            if adx_low:
                wins = sum(1 for t in adx_low if t.pnl > 0)
                lines.append(f"弱趋势(ADX<20): {len(adx_low)}笔, 胜率{wins/len(adx_low)*100:.1f}%, "
                           f"盈亏{sum(t.pnl for t in adx_low):+.2f}")
            lines.append("")
        
        # 结论
        lines.append("## 七、结论与建议")
        lines.append("-" * 40)
        
        if result.win_rate >= 0.6 and result.profit_factor >= 1.5:
            lines.append("[PASS] 策略表现良好，胜率和盈亏比达标")
        elif result.win_rate >= 0.5:
            lines.append("[WARN] 策略表现一般，建议优化参数")
        else:
            lines.append("[FAIL] 策略表现不佳，需要重新审视入场条件")
        
        if result.max_drawdown_pct > 0.15:
            lines.append("[WARN] 最大回撤较高，建议降低仓位或优化止损")
        
        if result.sharpe_ratio < 1.0:
            lines.append("[WARN] 夏普比率偏低，风险调整后收益不足")
        
        lines.append("")
        lines.append("=" * 70)
        
        return "\n".join(lines)
    
    def save_results(self, result: BacktestResult, output_dir: str = "output/backtest") -> None:
        """保存回测结果"""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # 保存报告
        report = self.generate_report(result)
        report_file = output_path / f"backtest_report_{timestamp}.txt"
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write(report)
        print(f"\n[SAVE] 报告已保存: {report_file}")
        
        # 保存交易记录
        if result.trades:
            trades_df = pd.DataFrame([
                {
                    'symbol': t.symbol,
                    'side': t.side,
                    'entry_price': t.entry_price,
                    'exit_price': t.exit_price,
                    'size': t.size,
                    'pnl': t.pnl,
                    'pnl_pct': t.pnl_pct,
                    'entry_time': t.entry_time,
                    'exit_time': t.exit_time,
                    'bars_held': t.bars_held,
                    'exit_reason': t.exit_reason,
                    'entry_score': t.entry_score,
                    'max_profit_pct': t.max_profit_pct,
                    'max_drawdown_pct': t.max_drawdown_pct
                }
                for t in result.trades
            ])
            trades_file = output_path / f"backtest_trades_{timestamp}.csv"
            trades_df.to_csv(trades_file, index=False)
            print(f"[SAVE] 交易记录已保存: {trades_file}")
        
        # 保存权益曲线
        if result.equity_curve:
            equity_df = pd.DataFrame(result.equity_curve)
            equity_file = output_path / f"backtest_equity_{timestamp}.csv"
            equity_df.to_csv(equity_file, index=False)
            print(f"[SAVE] 权益曲线已保存: {equity_file}")


# ==================== 主函数 ====================

def main():
    """主函数"""
    print("=" * 70)
    print("多币种回测系统")
    print("=" * 70)
    
    # 加载配置
    config_path = Path(__file__).resolve().parents[2] / "config" / "trading_config_fund_flow.json"
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            config_data = json.load(f)
        
        symbols = config_data.get('trading', {}).get('symbols', [
            "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT",
            "TRXUSDT", "LINKUSDT", "DOTUSDT", "SHIBUSDT", "LTCUSDT",
            "BCHUSDT", "UNIUSDT", "NEARUSDT", "ATOMUSDT", "XLMUSDT"
        ])
        print(f"从配置文件加载币种: {symbols}")
    else:
        # 虚拟货币市值5-50名（排除稳定币和BTC/ETH/BNB）
        symbols = [
            "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT",
            "TRXUSDT", "LINKUSDT", "DOTUSDT", "SHIBUSDT", "LTCUSDT",
            "BCHUSDT", "UNIUSDT", "NEARUSDT", "ATOMUSDT", "XLMUSDT",
            "APTUSDT", "ICPUSDT", "HBARUSDT", "VETUSDT", "FILUSDT",
            "OPUSDT", "ARBUSDT", "MKRUSDT", "SUIUSDT", "INJUSDT",
            "TIAUSDT", "SEIUSDT", "IMXUSDT", "WLDUSDT", "AAVEUSDT",
            "GRTUSDT", "RUNEUSDT", "STXUSDT", "ALGOUSDT", "MINAUSDT",
            "ENSUSDT", "THETAUSDT", "FLOWUSDT", "FETUSDT", "RNDRUSDT",
            "AKTUSDT", "CFXUSDT", "AGIXUSDT", "WOOUSDT", "PEPEUSDT"
        ]
        print(f"使用默认币种: {symbols}")
    
    # 创建回测配置
    config = BacktestConfig(
        symbols=symbols,
        days=30,
        max_positions=2,
        initial_capital=10000.0
    )
    
    # 创建回测器
    backtester = MultiSymbolBacktester(config)
    
    # 运行回测
    result = backtester.run()
    
    if result:
        # 生成报告
        report = backtester.generate_report(result)
        print("\n" + report)
        
        # 保存结果
        backtester.save_results(result)
    else:
        print("\n[ERROR] 回测失败")


if __name__ == "__main__":
    main()

