"""为JSONC文件添加注释"""
import re

# 读取JSON文件
with open('config/trading_config_fund_flow.jsonc', 'r', encoding='utf-8') as f:
    content = f.read()

# 添加注释
comments = {
    '"iflow"': '// 文件访问配置',
    '"trading"': '// 交易基础配置',
    '"symbols"': '// 交易标的列表（USDT永续合约）',
    '"min_leverage"': '// 最小杠杆倍数',
    '"default_leverage"': '// 默认杠杆倍数',
    '"max_leverage"': '// 最大杠杆倍数',
    '"min_position_percent"': '// 最小仓位占比（%）',
    '"max_position_percent"': '// 最大仓位占比（%）',
    '"reserve_percent"': '// 预留资金百分比',
    '"risk"': '// 风险控制配置',
    '"account_circuit_enabled"': '// 是否启用账户熔断',
    '"max_daily_loss_percent"': '// 单日最大亏损百分比',
    '"max_consecutive_losses"': '// 最大连续亏损次数',
    '"daily_loss_cooldown_seconds"': '// 日损冷却时间（秒）',
    '"consecutive_loss_cooldown_seconds"': '// 连损冷却时间（秒）',
    '"daily_reset_timezone"': '// 每日重置时区',
    '"stop_loss_default_percent"': '// 默认止损百分比（2%）',
    '"take_profit_default_percent"': '// 默认止盈百分比',
    '"conflict_protection"': '// 冲突保护（持仓风控核心）',
    '"light_confirm_bars"': '// 轻度冲突确认K线数',
    '"hard_confirm_bars"': '// 硬冲突确认K线数',
    '"cooldown_sec"': '// 冷却时间（秒）',
    '"state_circuit_trap_bars"': '// 状态熔断陷阱K线数',
    '"state_circuit_trap_hard"': '// 状态熔断陷阱硬度阈值',
    '"state_circuit_trap_hard_bars"': '// 状态熔断陷阱硬度K线数',
    '"hard_exit_min_hold_seconds"': '// 硬平仓最短持仓时间（秒）',
    '"directional_eval_interval_seconds"': '// 方向评估间隔（秒）',
    '"strategy"': '// 策略配置',
    '"fund_flow"': '// 资金流策略配置',
    '"regime"': '// 市场状态判定参数（ADX/ATR）',
    '"entry_filters"': '// 入场过滤参数',
    '"trend_capture"': '// 趋势捕捉配置',
    '"macd"': '// MACD参数（针对加密货币优化）',
    '"kdj"': '// KDJ参数',
    '"thresholds"': '// 开平仓阈值',
    '"pretrade_risk_gate"': '// 前置风控EXIT配置',
    '"weights"': '// 权重配置',
    '"engine_params"': '// 市场状态路由配置',
    '"schedule"': '// 调度配置',
    '"network"': '// 网络配置',
    '"startup"': '// 启动配置',
}

# 替换为带注释的版本
for key, comment in comments.items():
    content = content.replace(key, f'{comment}\n    {key}')

# 添加文件头部注释
header = """{
  // ========================================
  // 资金流交易配置文件 (JSONC格式)
  // 支持 // 单行注释
  // 主程序自动支持 .json 和 .jsonc 后缀
  // ========================================

"""

content = header + content[1:]

with open('config/trading_config_fund_flow.jsonc', 'w', encoding='utf-8') as f:
    f.write(content)

print('OK: 注释添加完成')
