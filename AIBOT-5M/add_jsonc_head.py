import json

# 读取JSON文件
with open('config/trading_config_fund_flow.jsonc', 'r', encoding='utf-8') as f:
    data = json.load(f)

# 写入带头部注释的JSONC文件
with open('config/trading_config_fund_flow.jsonc', 'w', encoding='utf-8') as f:
    f.write('{\n')
    f.write('  // ========================================\n')
    f.write('  // 资金流交易配置文件 (JSONC格式)\n')
    f.write('  // 支持 // 单行注释\n')
    f.write('  // 主程序自动支持 .json 和 .jsonc 后缀\n')
    f.write('  // ========================================\n\n')
    json.dump(data, f, indent=2, ensure_ascii=False)

print('OK: JSONC文件已创建')
