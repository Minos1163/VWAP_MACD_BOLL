"""
1H 企稳量化框架验证脚本
用于回测和验证五维度打分模型的有效性

功能：
1. 统计不同阈值组合下的入场胜率
2. 对比不同市场状态（牛市/熊市/震荡市）下的表现
3. 生成验证报告和优化建议
"""

import json
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field, asdict

# 导入评分器
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.fund_flow.stabilization_scorer import StabilizationScorer, SignalLevel


@dataclass
class BacktestResult:
    """回测结果"""
    total_signals: int = 0
    winning_signals: int = 0
    losing_signals: int = 0
    win_rate: float = 0.0
    avg_profit: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    
    # 按等级统计
    grade_stats: Dict[str, Dict] = field(default_factory=dict)
    
    # 按市场状态统计
    market_stats: Dict[str, Dict] = field(default_factory=dict)
    
    # 维度得分分布
    dimension_distributions: Dict[str, Dict] = field(default_factory=dict)


class StabilizationBacktester:
    """
    企稳框架回测验证器
    
    验证流程：
    1. 加载历史数据
    2. 遍历每个时间点，计算企稳得分
    3. 记录入场信号和后续表现
    4. 统计胜率、盈亏比等指标
    5. 生成验证报告
    """
    
    def __init__(self, config_path: Optional[str] = None):
        self.scorer = StabilizationScorer()
        self.config = self._load_config(config_path) if config_path else {}
        
    def _load_config(self, config_path: str) -> Dict:
        """加载配置文件"""
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    # ==================== 数据加载 ====================
    
    def load_data_from_csv(self, 
                           csv_path: str,
                           timeframe: str = "1h") -> pd.DataFrame:
        """
        从CSV加载K线数据
        
        Args:
            csv_path: CSV文件路径
            timeframe: 时间周期
        
        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
        """
        df = pd.read_csv(csv_path)
        
        # 标准化列名
        column_mapping = {
            'time': 'timestamp',
            'Time': 'timestamp',
            'Open': 'open',
            'High': 'high',
            'Low': 'low',
            'Close': 'close',
            'Volume': 'volume',
            'Vol': 'volume'
        }
        df = df.rename(columns=column_mapping)
        
        # 确保必要列存在
        required_cols = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        for col in required_cols:
            if col not in df.columns:
                raise ValueError(f"Missing required column: {col}")
        
        # 转换时间戳
        if 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df = df.sort_values('timestamp')
        
        return df
    
    def generate_synthetic_data(self, 
                                n_bars: int = 1000,
                                trend: str = "up",
                                volatility: float = 0.02) -> pd.DataFrame:
        """
        生成合成数据用于测试
        
        Args:
            n_bars: K线数量
            trend: 趋势方向 ("up", "down", "sideways")
            volatility: 波动率
        """
        np.random.seed(42)
        
        # 生成基础价格序列
        if trend == "up":
            base_trend = np.linspace(95000, 105000, n_bars)
        elif trend == "down":
            base_trend = np.linspace(105000, 95000, n_bars)
        else:
            base_trend = np.ones(n_bars) * 95000
        
        # 添加噪声
        noise = np.random.randn(n_bars) * 95000 * volatility
        close = base_trend + noise
        
        # 生成OHLC
        high = close + np.random.rand(n_bars) * 500
        low = close - np.random.rand(n_bars) * 500
        open_price = close + np.random.randn(n_bars) * 100
        
        # 生成成交量
        volume = np.random.randint(1000, 5000, n_bars)
        
        # 创建DataFrame
        df = pd.DataFrame({
            'timestamp': pd.date_range(start='2024-01-01', periods=n_bars, freq='H'),
            'open': open_price,
            'high': high,
            'low': low,
            'close': close,
            'volume': volume
        })
        
        return df
    
    # ==================== 核心回测逻辑 ====================
    
    def backtest(self,
                 df: pd.DataFrame,
                 lookforward_bars: int = 12,
                 profit_threshold: float = 0.01,
                 loss_threshold: float = -0.02) -> BacktestResult:
        """
        执行回测
        
        Args:
            df: K线数据
            lookforward_bars: 向前看几根K线判断结果
            profit_threshold: 盈利阈值（相对入场价）
            loss_threshold: 亏损阈值
        
        Returns:
            BacktestResult: 回测结果
        """
        result = BacktestResult()
        
        # 转换为numpy数组
        close = df['close'].values
        high = df['high'].values
        low = df['low'].values
        volume = df['volume'].values
        
        signals = []
        
        # 遍历每个时间点
        for i in range(50, len(close) - lookforward_bars):
            # 获取历史数据窗口
            close_window = close[:i+1]
            high_window = high[:i+1]
            low_window = low[:i+1]
            volume_window = volume[:i+1]
            
            # 计算企稳得分
            stabilization = self.scorer.analyze(
                close_window, high_window, low_window, volume_window, "long"
            )
            
            # 记录信号
            if stabilization.passed:
                entry_price = close[i]
                future_high = np.max(high[i+1:i+1+lookforward_bars])
                future_low = np.min(low[i+1:i+1+lookforward_bars])
                
                profit_pct = (future_high - entry_price) / entry_price
                loss_pct = (future_low - entry_price) / entry_price
                
                signal = {
                    'timestamp': df['timestamp'].iloc[i],
                    'entry_price': entry_price,
                    'total_score': stabilization.total_score,
                    'level': stabilization.level.value,
                    'profit_pct': profit_pct,
                    'loss_pct': loss_pct,
                    'is_winner': profit_pct >= profit_threshold,
                    'macd_score': stabilization.macd_score.actual_score,
                    'price_score': stabilization.price_score.actual_score,
                    'volume_score': stabilization.volume_score.actual_score,
                    'candle_score': stabilization.candle_score.actual_score,
                    'time_score': stabilization.time_score.actual_score
                }
                signals.append(signal)
        
        # 统计结果
        if signals:
            signals_df = pd.DataFrame(signals)
            
            result.total_signals = len(signals)
            result.winning_signals = len(signals_df[signals_df['is_winner']])
            result.losing_signals = result.total_signals - result.winning_signals
            result.win_rate = result.winning_signals / result.total_signals if result.total_signals > 0 else 0
            
            result.avg_profit = signals_df['profit_pct'].mean()
            result.avg_loss = signals_df['loss_pct'].mean()
            
            # 计算盈亏比
            total_profit = signals_df[signals_df['profit_pct'] > 0]['profit_pct'].sum()
            total_loss = abs(signals_df[signals_df['loss_pct'] < 0]['loss_pct'].sum())
            result.profit_factor = total_profit / total_loss if total_loss > 0 else float('inf')
            
            # 按等级统计
            for level in ['A', 'B', 'C', 'D']:
                level_df = signals_df[signals_df['level'] == level]
                if len(level_df) > 0:
                    result.grade_stats[level] = {
                        'count': len(level_df),
                        'win_rate': (level_df['is_winner'].sum() / len(level_df)),
                        'avg_score': level_df['total_score'].mean()
                    }
            
            # 维度得分分布
            for dim in ['macd', 'price', 'volume', 'candle', 'time']:
                col = f'{dim}_score'
                result.dimension_distributions[dim] = {
                    'mean': signals_df[col].mean(),
                    'std': signals_df[col].std(),
                    'min': signals_df[col].min(),
                    'max': signals_df[col].max()
                }
        
        return result, signals
    
    # ==================== 阈值优化 ====================
    
    def optimize_thresholds(self,
                           df: pd.DataFrame,
                           param_ranges: Dict[str, List]) -> Dict:
        """
        优化阈值参数
        
        Args:
            df: K线数据
            param_ranges: 参数范围，例如：
                {
                    'macd_decay_threshold': [0.3, 0.4, 0.5, 0.6],
                    'ema21_deviation_max': [0.003, 0.005, 0.007],
                    'pullback_volume_ratio_max': [0.5, 0.6, 0.7]
                }
        
        Returns:
            最佳参数组合和对应结果
        """
        best_params = None
        best_win_rate = 0
        all_results = []
        
        # 遍历所有参数组合
        from itertools import product
        param_names = list(param_ranges.keys())
        param_values = list(param_ranges.values())
        
        for combo in product(*param_values):
            # 更新配置
            params = dict(zip(param_names, combo))
            self.scorer.config.update(params)
            
            # 回测
            result, _ = self.backtest(df)
            
            # 记录结果
            all_results.append({
                'params': params,
                'win_rate': result.win_rate,
                'total_signals': result.total_signals,
                'profit_factor': result.profit_factor
            })
            
            # 更新最佳参数
            if result.win_rate > best_win_rate and result.total_signals >= 10:
                best_win_rate = result.win_rate
                best_params = params
        
        return {
            'best_params': best_params,
            'best_win_rate': best_win_rate,
            'all_results': all_results
        }
    
    # ==================== 市场状态分析 ====================
    
    def analyze_by_market_state(self,
                                df: pd.DataFrame,
                                atr_period: int = 14,
                                adx_period: int = 14) -> Dict:
        """
        按市场状态分析表现
        
        市场状态分类：
        - 强趋势：ADX > 25 且 方向明确
        - 震荡：ADX < 20
        - 边缘：20 <= ADX <= 25
        """
        close = df['close'].values
        high = df['high'].values
        low = df['low'].values
        volume = df['volume'].values
        
        # 计算ADX（简化版）
        # TODO: 实现完整的ADX计算
        
        market_states = {
            'strong_trend': {'signals': [], 'win_rate': 0},
            'volatile': {'signals': [], 'win_rate': 0},
            'normal': {'signals': [], 'win_rate': 0}
        }
        
        # 简化分类：根据价格波动
        returns = pd.Series(close).pct_change()
        volatility = returns.rolling(20).std()
        
        for i in range(50, len(close) - 12):
            # 判断市场状态
            current_vol = volatility.iloc[i]
            avg_vol = volatility.iloc[i-20:i].mean()
            
            if current_vol > avg_vol * 1.5:
                state = 'volatile'
            elif current_vol < avg_vol * 0.7:
                state = 'strong_trend'
            else:
                state = 'normal'
            
            # 计算企稳得分
            close_window = close[:i+1]
            high_window = high[:i+1]
            low_window = low[:i+1]
            volume_window = volume[:i+1]
            
            stabilization = self.scorer.analyze(
                close_window, high_window, low_window, volume_window, "long"
            )
            
            if stabilization.passed:
                entry_price = close[i]
                future_high = np.max(high[i+1:i+13])
                profit_pct = (future_high - entry_price) / entry_price
                
                market_states[state]['signals'].append({
                    'score': stabilization.total_score,
                    'profit': profit_pct,
                    'is_winner': profit_pct >= 0.01
                })
        
        # 统计各状态胜率
        for state, data in market_states.items():
            if data['signals']:
                wins = sum(1 for s in data['signals'] if s['is_winner'])
                data['win_rate'] = wins / len(data['signals'])
                data['count'] = len(data['signals'])
        
        return market_states
    
    # ==================== 报告生成 ====================
    
    def generate_report(self,
                       result: BacktestResult,
                       signals: List[Dict],
                       output_path: Optional[str] = None) -> str:
        """
        生成验证报告
        
        Args:
            result: 回测结果
            signals: 信号列表
            output_path: 输出路径
        """
        report_lines = []
        report_lines.append("=" * 70)
        report_lines.append("1H 企稳量化框架验证报告")
        report_lines.append("=" * 70)
        report_lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report_lines.append("")
        
        # 总体统计
        report_lines.append("## 一、总体统计")
        report_lines.append("-" * 40)
        report_lines.append(f"总信号数: {result.total_signals}")
        report_lines.append(f"盈利信号: {result.winning_signals}")
        report_lines.append(f"亏损信号: {result.losing_signals}")
        report_lines.append(f"胜率: {result.win_rate*100:.2f}%")
        report_lines.append(f"平均盈利: {result.avg_profit*100:.2f}%")
        report_lines.append(f"平均亏损: {result.avg_loss*100:.2f}%")
        report_lines.append(f"盈亏比: {result.profit_factor:.2f}")
        report_lines.append("")
        
        # 按等级统计
        report_lines.append("## 二、按信号等级统计")
        report_lines.append("-" * 40)
        for level, stats in result.grade_stats.items():
            report_lines.append(f"{level}级信号:")
            report_lines.append(f"  数量: {stats['count']}")
            report_lines.append(f"  胜率: {stats['win_rate']*100:.2f}%")
            report_lines.append(f"  平均得分: {stats['avg_score']:.1f}")
        report_lines.append("")
        
        # 维度得分分布
        report_lines.append("## 三、维度得分分布")
        report_lines.append("-" * 40)
        for dim, stats in result.dimension_distributions.items():
            report_lines.append(f"{dim}维度:")
            report_lines.append(f"  均值: {stats['mean']:.1f}")
            report_lines.append(f"  标准差: {stats['std']:.1f}")
            report_lines.append(f"  范围: [{stats['min']:.0f}, {stats['max']:.0f}]")
        report_lines.append("")
        
        # 结论和建议
        report_lines.append("## 四、结论和建议")
        report_lines.append("-" * 40)
        
        if result.win_rate >= 0.6:
            report_lines.append("✅ 框架表现良好，胜率达标")
        elif result.win_rate >= 0.5:
            report_lines.append("⚠️ 框架表现一般，建议优化参数")
        else:
            report_lines.append("❌ 框架表现不佳，需要重新设计")
        
        if result.profit_factor < 1.5:
            report_lines.append("⚠️ 盈亏比偏低，建议调整止盈止损策略")
        
        report_lines.append("")
        report_lines.append("=" * 70)
        
        report = "\n".join(report_lines)
        
        # 保存报告
        if output_path:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(report)
        
        return report


# ==================== 使用示例 ====================

def main():
    """主函数：运行完整验证流程"""
    
    print("=" * 70)
    print("1H 企稳量化框架验证")
    print("=" * 70)
    
    # 创建回测器
    backtester = StabilizationBacktester()
    
    # 生成测试数据
    print("\n[1] 生成测试数据...")
    df = backtester.generate_synthetic_data(n_bars=2000, trend="up", volatility=0.015)
    print(f"  生成 {len(df)} 根K线数据")
    
    # 执行回测
    print("\n[2] 执行回测...")
    result, signals = backtester.backtest(df)
    
    # 生成报告
    print("\n[3] 生成报告...")
    report = backtester.generate_report(result, signals)
    print(report)
    
    # 阈值优化
    print("\n[4] 阈值优化...")
    param_ranges = {
        'macd_decay_threshold': [0.4, 0.5, 0.6],
        'ema21_deviation_max': [0.004, 0.005, 0.006]
    }
    # optimization = backtester.optimize_thresholds(df, param_ranges)
    # print(f"最佳参数: {optimization['best_params']}")
    # print(f"最佳胜率: {optimization['best_win_rate']*100:.2f}%")
    
    # 市场状态分析
    print("\n[5] 市场状态分析...")
    market_stats = backtester.analyze_by_market_state(df)
    for state, stats in market_stats.items():
        if stats.get('count', 0) > 0:
            print(f"  {state}: {stats['count']}个信号, 胜率{stats['win_rate']*100:.1f}%")


if __name__ == "__main__":
    main()

