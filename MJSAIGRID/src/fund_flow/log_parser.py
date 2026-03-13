"""
日志解析器
解析现有的FUND_FLOW日志文件,提取K线操作信息并转换为增强格式
"""

import re
import json
from datetime import datetime
from typing import Optional, Dict, Any, List
from enhanced_kline_logger import (
    KlineOperationLog, KlineInfo, AIStrategyInfo, 
    RiskControlInfo, DecisionInfo, DecisionAction,
    MarketRegime, Direction, EnhancedKlineLogger
)


class FundFlowLogParser:
    """FUND_FLOW日志解析器"""
    
    def __init__(self):
        self.logger = EnhancedKlineLogger()
    
    def parse_log_line(self, line: str) -> Optional[Dict[str, Any]]:
        """
        解析单行日志
        
        Args:
            line: 日志行
            
        Returns:
            解析后的字典或None
        """
        # 解析周期信息
        cycle_match = re.search(r'cycle (\d+) @ (.+?) UTC ===', line)
        if cycle_match:
            return {
                'cycle': int(cycle_match.group(1)),
                'timestamp': datetime.strptime(cycle_match.group(2), '%Y-%m-%d %H:%M:%S'),
                'mode': 'MIXED_AI_REVIEW'
            }
        
        # 解析决策信息
        decision_match = re.search(r'\[([A-Z]+)\] 决策=(\w+)', line)
        if decision_match:
            return {
                'symbol': decision_match.group(1),
                'decision': decision_match.group(2)
            }
        
        # 解析K线价格
        kline_match = re.search(r'K线价格: open=([\d.]+) \| close=([\d.]+)', line)
        if kline_match:
            return {
                'open_price': float(kline_match.group(1)),
                'close_price': float(kline_match.group(2))
            }
        
        # 解析信号评分
        score_match = re.search(r'信号评分: long=([\d.]+), short=([\d.]+)', line)
        if score_match:
            return {
                'signal_long': float(score_match.group(1)),
                'signal_short': float(score_match.group(2))
            }
        
        # 解析3.0评分
        score_3m_match = re.search(r'3.0评分: score_15m\(L/S\)=([\d.]+)/([\d.]+), score_5m\(L/S\)=([\d.]+)/([\d.]+)', line)
        if score_3m_match:
            return {
                'score_15m_long': float(score_3m_match.group(1)),
                'score_15m_short': float(score_3m_match.group(2)),
                'score_5m_long': float(score_3m_match.group(3)),
                'score_5m_short': float(score_3m_match.group(4))
            }
        
        # 解析方向判断
        dir_match = re.search(r'方向判断: dir_lw=(\w+)\(([-\d.]+)\) \| dir_ev=(\w+)\(([-\d.]+)\)', line)
        if dir_match:
            direction_str = dir_match.group(1)
            if direction_str == 'SHOR':
                direction_str = 'SHORT'
            return {
                'direction': direction_str,
                'dir_lw_score': float(dir_match.group(2)),
                'dir_ev': dir_match.group(3),
                'dir_ev_score': float(dir_match.group(4))
            }
        
        # 解析引擎上下文
        engine_match = re.search(r'引擎上下文: engine=(\w+), pool=(\w+), direction=(\w+)', line)
        if engine_match:
            return {
                'regime': engine_match.group(1),
                'pool': engine_match.group(2),
                'direction': engine_match.group(3)
            }
        
        # 解析决策原因
        reason_match = re.search(r'决策原因: (.+)', line)
        if reason_match:
            reason = reason_match.group(1)
            # 提取模式
            mode_match = re.search(r'mode=(\w+)', reason)
            if mode_match:
                return {
                    'reason': reason,
                    'mode': mode_match.group(1)
                }
            return {'reason': reason}
        
        # 解析HOLD归因
        hold_match = re.search(r'HOLD归因: (.+)', line)
        if hold_match:
            return {'hold_attribution': hold_match.group(1)}
        
        # 解析DS权重
        ds_weight_match = re.search(r'DS权重快照: (\{.+\})', line)
        if ds_weight_match:
            try:
                weights = json.loads(ds_weight_match.group(1))
                return {'ai_weights': weights}
            except:
                pass
        
        # 解析前置风控Gate
        gate_match = re.search(r'前置风控Gate: action=(\w+), score=([-\d.]+)', line)
        if gate_match:
            return {
                'gate_action': gate_match.group(1),
                'gate_score': float(gate_match.group(2))
            }
        
        # 解析风控摘要
        risk_match = re.search(r'风控摘要 symbol=(\w+) engine=(\w+) side=(\w+) entry=([\d.]+)', line)
        if risk_match:
            return {
                'symbol': risk_match.group(1),
                'risk_engine': risk_match.group(2),
                'risk_side': risk_match.group(3),
                'entry_price': float(risk_match.group(4))
            }
        
        # 解析状态和占比
        state_match = re.search(r'状态=(\w+) \| 目标占比=([\d.]+) \| 当前占比=([\d.]+)', line)
        if state_match:
            return {
                'state': state_match.group(1),
                'target_size': float(state_match.group(2)),
                'position_size': float(state_match.group(3))
            }
        
        # 解析杠杆
        leverage_match = re.search(r'杠杆\(请求/实际\)=(\d+)x/(\d+)x', line)
        if leverage_match:
            return {
                'leverage': int(leverage_match.group(2))
            }
        
        return None
    
    def parse_log_file(self, file_path: str) -> List[KlineOperationLog]:
        """
        解析日志文件
        
        Args:
            file_path: 日志文件路径
            
        Returns:
            K线操作日志列表
        """
        logs = []
        
        current_log_data = {}
        current_symbol = None
        current_cycle = None
        current_timestamp = None
        
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                # 解析当前行
                parsed = self.parse_log_line(line)
                if not parsed:
                    continue
                
                # 更新当前上下文
                if 'cycle' in parsed:
                    current_cycle = parsed['cycle']
                    current_timestamp = parsed['timestamp']
                    current_log_data['cycle'] = current_cycle
                    current_log_data['timestamp'] = current_timestamp
                    current_log_data['mode'] = parsed.get('mode', 'MIXED_AI_REVIEW')
                
                if 'symbol' in parsed and 'decision' in parsed:
                    # 如果之前有未完成的日志,先保存
                    if current_log_data and current_symbol:
                        log = self._build_log_entry(current_log_data, current_symbol)
                        if log:
                            logs.append(log)
                    
                    # 开始新的日志条目
                    current_symbol = parsed['symbol']
                    current_log_data['symbol'] = current_symbol
                    current_log_data['decision'] = parsed['decision']
                
                # 更新其他字段
                for key, value in parsed.items():
                    if key not in ['cycle', 'timestamp', 'mode', 'symbol', 'decision']:
                        current_log_data[key] = value
        
        # 保存最后一个日志条目
        if current_log_data and current_symbol:
            log = self._build_log_entry(current_log_data, current_symbol)
            if log:
                logs.append(log)
        
        return logs
    
    def _build_log_entry(self, data: Dict[str, Any], symbol: str) -> Optional[KlineOperationLog]:
        """
        构建日志条目
        
        Args:
            data: 解析的数据
            symbol: 交易对
            
        Returns:
            K线操作日志或None
        """
        try:
            # 确定动作
            decision = data.get('decision', 'HOLD')
            if decision == 'BUY':
                action = DecisionAction.BUY
            elif decision == 'SELL':
                action = DecisionAction.SELL
            else:
                action = DecisionAction.HOLD
            
            # 确定市场状态
            regime_str = data.get('regime', 'TREND')
            try:
                regime = MarketRegime(regime_str)
            except:
                regime = MarketRegime.TREND
            
            # 确定方向
            direction_str = data.get('direction', 'BOTH')
            try:
                direction = Direction(direction_str)
            except:
                direction = Direction.BOTH
            
            # 构建K线信息
            kline = KlineInfo(
                symbol=symbol,
                open_time=0,
                close_time=0,
                open_price=data.get('open_price', 0.0),
                high_price=data.get('open_price', 0.0),
                low_price=data.get('open_price', 0.0),
                close_price=data.get('close_price', 0.0),
                volume=0.0,
                timeframe='5m'
            )
            
            # 构建AI策略信息
            ai_strategy = AIStrategyInfo(
                regime=regime,
                direction=direction,
                signal_long=data.get('signal_long', 0.0),
                signal_short=data.get('signal_short', 0.0),
                score_15m_long=data.get('score_15m_long', 0.0),
                score_15m_short=data.get('score_15m_short', 0.0),
                score_5m_long=data.get('score_5m_long', 0.0),
                score_5m_short=data.get('score_5m_short', 0.0),
                confidence=data.get('confidence', 0.0),
                ai_weights=data.get('ai_weights'),
                fallback_used=False,
                model_version='v1.0'
            )
            
            # 构建风控信息
            risk_control = RiskControlInfo(
                gate_score=data.get('gate_score', 0.0),
                gate_action=data.get('gate_action', 'HOLD'),
                risk_level='LOW',
                position_size=data.get('position_size', 0.0),
                target_size=data.get('target_size', 0.0),
                leverage=data.get('leverage', 1),
                margin_used=0.0,
                margin_available=0.0,
                is_protected=False
            )
            
            # 构建决策信息
            decision_info = DecisionInfo(
                action=action,
                reason=data.get('reason', ''),
                hold_attribution=data.get('hold_attribution', ''),
                signal_source='signal',
                trigger_type=data.get('mode', '')
            )
            
            # 构建完整日志条目
            return KlineOperationLog(
                timestamp=data.get('timestamp', datetime.now()),
                cycle=data.get('cycle', 0),
                mode=data.get('mode', 'MIXED_AI_REVIEW'),
                kline=kline,
                ai_strategy=ai_strategy,
                risk_control=risk_control,
                decision=decision_info,
                position_info=None,
                performance=None
            )
            
        except Exception as e:
            print(f"⚠️  构建日志条目失败: {e}")
            print(f"数据: {data}")
            return None
    
    def parse_and_export(self, input_file: str, output_dir: str = None):
        """
        解析并导出日志
        
        Args:
            input_file: 输入日志文件路径
            output_dir: 输出目录,如果为None则使用输入文件所在目录
        """
        import os
        
        if output_dir is None:
            output_dir = os.path.dirname(input_file)
        
        # 解析日志
        print(f"🔍 正在解析日志文件: {input_file}")
        logs = self.parse_log_file(input_file)
        print(f"✅ 成功解析 {len(logs)} 条日志")
        
        if not logs:
            print("⚠️  没有找到有效的日志条目")
            return
        
        # 生成输出文件路径
        base_name = os.path.splitext(os.path.basename(input_file))[0]
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        # 导出增强格式日志
        enhanced_log_path = os.path.join(output_dir, f"{base_name}_enhanced_{timestamp}.log")
        with open(enhanced_log_path, 'w', encoding='utf-8') as f:
            for log in logs:
                f.write(log.to_console_format() + "\n\n")
        print(f"✅ 增强格式日志已导出到: {enhanced_log_path}")
        
        # 导出JSON格式
        json_log_path = os.path.join(output_dir, f"{base_name}_{timestamp}.jsonl")
        with open(json_log_path, 'w', encoding='utf-8') as f:
            for log in logs:
                f.write(log.to_json() + "\n")
        print(f"✅ JSON格式日志已导出到: {json_log_path}")
        
        # 导出CSV格式
        csv_path = os.path.join(output_dir, f"{base_name}_{timestamp}.csv")
        self.logger.logs = logs
        self.logger.export_to_csv(csv_path)
        
        # 打印统计信息
        stats = self.logger.get_statistics()
        print("\n📊 日志统计:")
        print(json.dumps(stats, indent=2, ensure_ascii=False))
        
        print(f"\n✅ 所有日志已成功导出到目录: {output_dir}")
        return logs


def main():
    """主函数"""
    import sys
    
    if len(sys.argv) < 2:
        print("用法: python log_parser.py <日志文件路径> [输出目录]")
        print("示例: python log_parser.py D:\\AIDCA\\AIGRID\\logs\\2026-03\\2026-03-12\\runtime.out.00.log")
        return
    
    input_file = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else None
    
    parser = FundFlowLogParser()
    parser.parse_and_export(input_file, output_dir)


if __name__ == "__main__":
    main()
