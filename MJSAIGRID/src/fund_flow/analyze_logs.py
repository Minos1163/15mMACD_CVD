"""
日志分析示例脚本
演示如何使用增强K线日志系统进行数据分析
"""

import pandas as pd
import matplotlib.pyplot as plt
import json
from datetime import datetime
from pathlib import Path
import sys


def load_jsonl_file(file_path: str) -> pd.DataFrame:
    """
    加载JSON Lines文件
    
    Args:
        file_path: 文件路径
        
    Returns:
        DataFrame
    """
    logs = []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    try:
                        logs.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return pd.json_normalize(logs)
    except FileNotFoundError:
        print(f"⚠️  文件不存在: {file_path}")
        return pd.DataFrame()


def analyze_decision_distribution(df: pd.DataFrame):
    """分析决策分布"""
    if df.empty:
        print("⚠️  数据为空")
        return
    
    print("\n" + "="*80)
    print("📊 决策分布分析")
    print("="*80)
    
    # 动作分布
    action_dist = df['decision.action'].value_counts()
    print(f"\n动作分布:")
    for action, count in action_dist.items():
        pct = count / len(df) * 100
        print(f"  {action}: {count} ({pct:.1f}%)")
    
    # 市场状态分布
    regime_dist = df['ai_strategy.regime'].value_counts()
    print(f"\n市场状态分布:")
    for regime, count in regime_dist.items():
        pct = count / len(df) * 100
        print(f"  {regime}: {count} ({pct:.1f}%)")
    
    # 交易对分布
    symbol_dist = df['symbol'].value_counts().head(10)
    print(f"\n交易对分布 (Top 10):")
    for symbol, count in symbol_dist.items():
        pct = count / len(df) * 100
        print(f"  {symbol}: {count} ({pct:.1f}%)")


def analyze_signal_performance(df: pd.DataFrame):
    """分析信号性能"""
    if df.empty:
        return
    
    print("\n" + "="*80)
    print("📈 信号性能分析")
    print("="*80)
    
    # 按市场状态分组分析
    for regime in ['TREND', 'RANGE', 'NO_TRADE']:
        regime_df = df[df['ai_strategy.regime'] == regime]
        if len(regime_df) == 0:
            continue
        
        print(f"\n{regime} 状态:")
        print(f"  总记录数: {len(regime_df)}")
        
        # 信号统计
        avg_long = regime_df['ai_strategy.signal_long'].mean()
        avg_short = regime_df['ai_strategy.signal_short'].mean()
        print(f"  平均Long信号: {avg_long:.3f}")
        print(f"  平均Short信号: {avg_short:.3f}")
        
        # 置信度统计
        avg_conf = regime_df['ai_strategy.confidence'].mean()
        print(f"  平均置信度: {avg_conf:.3f}")
        
        # 决策统计
        action_counts = regime_df['decision.action'].value_counts()
        print(f"  决策分布: {dict(action_counts)}")


def analyze_risk_control(df: pd.DataFrame):
    """分析风控效果"""
    if df.empty:
        return
    
    print("\n" + "="*80)
    print("🛡️  风控效果分析")
    print("="*80)
    
    # Gate动作分布
    gate_dist = df['risk_control.gate_action'].value_counts()
    print(f"\nGate动作分布:")
    for action, count in gate_dist.items():
        pct = count / len(df) * 100
        print(f"  {action}: {count} ({pct:.1f}%)")
    
    # Gate分数统计
    gate_scores = df['risk_control.gate_score']
    print(f"\nGate分数统计:")
    print(f"  平均值: {gate_scores.mean():.3f}")
    print(f"  最小值: {gate_scores.min():.3f}")
    print(f"  最大值: {gate_scores.max():.3f}")
    print(f"  标准差: {gate_scores.std():.3f}")
    
    # 仓位统计
    position_sizes = df['risk_control.position_size']
    print(f"\n仓位统计:")
    print(f"  平均仓位: {position_sizes.mean():.2f}")
    print(f"  最大仓位: {position_sizes.max():.2f}")
    
    # 杠杆统计
    leverage_dist = df['risk_control.leverage'].value_counts()
    print(f"\n杠杆分布:")
    for lev, count in leverage_dist.items():
        print(f"  {lev}x: {count}")


def analyze_ai_weights(df: pd.DataFrame):
    """分析AI权重"""
    if df.empty or 'ai_strategy.ai_weights' not in df.columns:
        return
    
    print("\n" + "="*80)
    print("⚖️  AI权重分析")
    print("="*80)
    
    # 提取权重数据
    weights_df = df['ai_strategy.ai_weights'].dropna().apply(pd.Series)
    
    if weights_df.empty:
        print("  无权重数据")
        return
    
    print(f"\n权重平均值:")
    for col in weights_df.columns:
        avg_weight = weights_df[col].mean()
        std_weight = weights_df[col].std()
        print(f"  {col}: {avg_weight:.3f} (±{std_weight:.3f})")
    
    # 置信度分析
    fallback_used = df['ai_strategy.fallback_used'].value_counts()
    print(f"\nFallback使用情况:")
    for used, count in fallback_used.items():
        pct = count / len(df) * 100
        print(f"  {used}: {count} ({pct:.1f}%)")


def analyze_price_changes(df: pd.DataFrame):
    """分析价格变化"""
    if df.empty:
        return
    
    print("\n" + "="*80)
    print("💰 价格变化分析")
    print("="*80)
    
    # 计算价格变化
    df['price_change_pct'] = df['kline.change_pct']
    
    # 总体统计
    print(f"\n价格变化统计:")
    print(f"  平均变化: {df['price_change_pct'].mean():.2f}%")
    print(f"  最大涨幅: {df['price_change_pct'].max():.2f}%")
    print(f"  最大跌幅: {df['price_change_pct'].min():.2f}%")
    print(f"  标准差: {df['price_change_pct'].std():.2f}%")
    
    # 按决策分组分析
    for action in ['BUY', 'SELL', 'HOLD']:
        action_df = df[df['decision.action'] == action]
        if len(action_df) == 0:
            continue
        
        avg_change = action_df['price_change_pct'].mean()
        print(f"\n{action}操作后平均价格变化: {avg_change:.2f}%")


def generate_visualizations(df: pd.DataFrame, output_dir: str):
    """生成可视化图表"""
    if df.empty:
        return
    
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    # 1. 决策分布饼图
    fig, ax = plt.subplots(figsize=(8, 6))
    action_counts = df['decision.action'].value_counts()
    ax.pie(action_counts.values, labels=action_counts.index, autopct='%1.1f%%')
    ax.set_title('决策分布')
    plt.tight_layout()
    plt.savefig(output_path / 'decision_distribution.png')
    plt.close()
    print(f"✅ 已保存: decision_distribution.png")
    
    # 2. 市场状态分布饼图
    fig, ax = plt.subplots(figsize=(8, 6))
    regime_counts = df['ai_strategy.regime'].value_counts()
    ax.pie(regime_counts.values, labels=regime_counts.index, autopct='%1.1f%%')
    ax.set_title('市场状态分布')
    plt.tight_layout()
    plt.savefig(output_path / 'regime_distribution.png')
    plt.close()
    print(f"✅ 已保存: regime_distribution.png")
    
    # 3. 信号评分分布直方图
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))
    
    ax1.hist(df['ai_strategy.signal_long'], bins=30, alpha=0.7, color='green')
    ax1.set_xlabel('Long信号评分')
    ax1.set_ylabel('频次')
    ax1.set_title('Long信号评分分布')
    
    ax2.hist(df['ai_strategy.signal_short'], bins=30, alpha=0.7, color='red')
    ax2.set_xlabel('Short信号评分')
    ax2.set_ylabel('频次')
    ax2.set_title('Short信号评分分布')
    
    plt.tight_layout()
    plt.savefig(output_path / 'signal_distribution.png')
    plt.close()
    print(f"✅ 已保存: signal_distribution.png")
    
    # 4. Gate分数箱线图
    fig, ax = plt.subplots(figsize=(10, 6))
    gate_actions = df['risk_control.gate_action'].unique()
    gate_scores = [df[df['risk_control.gate_action'] == action]['risk_control.gate_score'].values 
                   for action in gate_actions]
    ax.boxplot(gate_scores, labels=gate_actions)
    ax.set_ylabel('Gate分数')
    ax.set_title('Gate动作的分数分布')
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(output_path / 'gate_score_distribution.png')
    plt.close()
    print(f"✅ 已保存: gate_score_distribution.png")
    
    # 5. 置信度分布
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.hist(df['ai_strategy.confidence'], bins=30, alpha=0.7)
    ax.set_xlabel('置信度')
    ax.set_ylabel('频次')
    ax.set_title('置信度分布')
    plt.axvline(df['ai_strategy.confidence'].mean(), color='r', linestyle='--', 
                label=f'平均值: {df["ai_strategy.confidence"].mean():.3f}')
    ax.legend()
    plt.tight_layout()
    plt.savefig(output_path / 'confidence_distribution.png')
    plt.close()
    print(f"✅ 已保存: confidence_distribution.png")
    
    print(f"\n✅ 所有图表已保存到: {output_dir}")


def export_summary_report(df: pd.DataFrame, output_path: str):
    """导出摘要报告"""
    if df.empty:
        return
    
    report_lines = [
        "# FUND_FLOW策略日志分析报告",
        f"\n生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"\n数据概览:",
        f"- 总记录数: {len(df)}",
        f"- 交易对数: {df['symbol'].nunique()}",
        f"- 时间范围: {df['timestamp'].min()} 至 {df['timestamp'].max()}",
        f"\n## 决策分布",
    ]
    
    # 决策分布
    action_dist = df['decision.action'].value_counts()
    for action, count in action_dist.items():
        pct = count / len(df) * 100
        report_lines.append(f"- {action}: {count} ({pct:.1f}%)")
    
    # 市场状态分布
    report_lines.append(f"\n## 市场状态分布")
    regime_dist = df['ai_strategy.regime'].value_counts()
    for regime, count in regime_dist.items():
        pct = count / len(df) * 100
        report_lines.append(f"- {regime}: {count} ({pct:.1f}%)")
    
    # 信号性能
    report_lines.append(f"\n## 信号性能")
    for regime in ['TREND', 'RANGE']:
        regime_df = df[df['ai_strategy.regime'] == regime]
        if len(regime_df) > 0:
            avg_long = regime_df['ai_strategy.signal_long'].mean()
            avg_short = regime_df['ai_strategy.signal_short'].mean()
            avg_conf = regime_df['ai_strategy.confidence'].mean()
            report_lines.append(f"\n{regime}:")
            report_lines.append(f"- 平均Long信号: {avg_long:.3f}")
            report_lines.append(f"- 平均Short信号: {avg_short:.3f}")
            report_lines.append(f"- 平均置信度: {avg_conf:.3f}")
    
    # 风控效果
    report_lines.append(f"\n## 风控效果")
    gate_dist = df['risk_control.gate_action'].value_counts()
    report_lines.append(f"\nGate动作分布:")
    for action, count in gate_dist.items():
        pct = count / len(df) * 100
        report_lines.append(f"- {action}: {count} ({pct:.1f}%)")
    
    gate_scores = df['risk_control.gate_score']
    report_lines.append(f"\nGate分数统计:")
    report_lines.append(f"- 平均值: {gate_scores.mean():.3f}")
    report_lines.append(f"- 最小值: {gate_scores.min():.3f}")
    report_lines.append(f"- 最大值: {gate_scores.max():.3f}")
    
    # 写入文件
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(report_lines))
    
    print(f"✅ 摘要报告已保存到: {output_path}")


def main():
    """主函数"""
    if len(sys.argv) < 2:
        print("用法: python analyze_logs.py <JSON日志文件> [输出目录]")
        print("示例: python analyze_logs.py logs/runtime.out.00_20260312.jsonl output/")
        return
    
    input_file = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "output"
    
    print(f"🔍 加载日志文件: {input_file}")
    df = load_jsonl_file(input_file)
    
    if df.empty:
        print("⚠️  未能加载数据")
        return
    
    print(f"✅ 成功加载 {len(df)} 条记录")
    
    # 分析
    analyze_decision_distribution(df)
    analyze_signal_performance(df)
    analyze_risk_control(df)
    analyze_ai_weights(df)
    analyze_price_changes(df)
    
    # 生成可视化
    print(f"\n📊 生成可视化图表...")
    generate_visualizations(df, output_dir)
    
    # 导出报告
    report_path = Path(output_dir) / "analysis_report.md"
    export_summary_report(df, str(report_path))
    
    print(f"\n✅ 分析完成! 结果已保存到: {output_dir}")


if __name__ == "__main__":
    main()
