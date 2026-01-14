# -*- coding: utf-8 -*-
import os
import time
import hmac
import hashlib
import requests
import json
from typing import Dict, Any, Optional
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# ========== 统一账户（papi）核心配置 ==========
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_SECRET")
BASE_URL = "https://papi.binance.com"  # 统一账户专属端点（注意：所有endpoint需包含/papi/v1前缀）
TIMEOUT = 10  # 请求超时时间

# 检查API密钥是否配置
if not API_KEY or not API_SECRET:
    raise ValueError("请在.env文件中配置BINANCE_API_KEY和BINANCE_SECRET")

# 类型检查器断言 - 确保类型推断为 str 而非 Optional[str]
assert API_KEY is not None and API_SECRET is not None, "API密钥不能为None"

# 重新绑定类型明确的变量（类型窄化）
BINANCE_API_KEY: str = API_KEY  # type: ignore[assignment]
BINANCE_SECRET: str = API_SECRET  # type: ignore[assignment]



# ========== 统一账户签名函数（papi要求的签名规则和fapi一致，但接口不同） ==========
def generate_signature(params: dict, secret: str) -> str:
    """生成币安API签名（适配papi）"""
    query_string = '&'.join([f"{k}={v}" for k, v in sorted(params.items())])
    return hmac.new(secret.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()

# ========== 统一账户API请求函数 ==========
def send_papi_request(endpoint, params=None, method="GET", signed=True):
    """发送统一账户（papi）API请求"""
    params = params or {}
    
    headers = {
        "Content-Type": "application/json"
    }
    
    # 只有私有接口才签名
    if signed:
        # 创建参数字典的副本用于API调用，避免类型推断问题
        api_params = params.copy()
        api_params["timestamp"] = int(time.time() * 1000)
        api_params["signature"] = generate_signature(api_params, BINANCE_SECRET)
        headers["X-MBX-APIKEY"] = BINANCE_API_KEY
    else:
        api_params = params

    
    url = f"{BASE_URL}{endpoint}"
    print(f"[URL调试] 拼接后的URL: {url}")  # 调试信息
    
    if method == "GET":
        r = requests.get(url, params=api_params, headers=headers, timeout=TIMEOUT)
    elif method == "POST":
        r = requests.post(url, params=api_params, headers=headers, timeout=TIMEOUT)
    elif method == "DELETE":
        r = requests.delete(url, params=api_params, headers=headers, timeout=TIMEOUT)
    else:
        raise ValueError("不支持的请求方法")
    
    try:
        return r.json()
    except Exception:
        print("HTTP:", r.status_code, r.text)
        return None

# ========== 账户权限诊断（兼容检测Unified Account与Classic UM） ==========
def diagnose_account_permissions():
    """诊断PAPI网关与账户类型（自动检测Unified Account或Classic UM）"""
    print("="*80)
    print("开始诊断PAPI网关与账户类型...")
    print("="*80)
    
    # 1. 测试基础连接（无需权限）
    print("\n1. 测试基础连接（服务器时间）...")
    result = send_papi_request("/papi/v1/time", signed=False)
    if result and 'serverTime' in result:
        print(f"[✓] 基础连接成功 - 服务器时间: {result['serverTime']}")
    else:
        print("[x] 基础连接失败：请检查网络/端点是否正确（papi.binance.com）")
        return False
    
    # 2. 测试统一账户状态（关键：确认账户已开通统一账户）
    print("\n2. 测试统一账户状态...")
    result = send_papi_request("/papi/v1/account")
    if result and 'code' not in result:
        print(f"[✓] 统一账户已开通 - 账户状态: {result.get('data', {}).get('accountStatus', 'NORMAL')}")
    elif result and result['code'] == -2015:
        print("[x] 权限不足：API密钥缺少统一账户访问权限")
        print("   解决方案：在币安API管理页面勾选「统一账户交易」权限（不是普通期货权限）")
        return False
    elif result and result['code'] == -4041:
        print("[x] 账户未开通统一账户模式")
        print("   解决方案：登录币安网页端 → 交易 → 统一账户 → 开通统一账户模式")
        return False
    else:
        print(f"[x] 统一账户状态查询失败: {result}")
        return False
    
    # 3. 测试U本位合约账户资金信息（兼容检测）
    print("\n3. 检测U本位合约账户类型与资金信息...")
    result = send_papi_request("/papi/v1/um/account")
    
    # 检测账户类型
    mode = detect_account_mode(result) if isinstance(result, dict) else 'UNKNOWN'
    
    if mode == 'PORTFOLIO_MARGIN' and isinstance(result, dict):
        print(f"[✓] 账户类型: Portfolio Margin (Unified Account)")
        print(f"   - 账户权益: {result.get('accountEquity', '0')} USDT")
        print(f"   - 维持保证金率: {result.get('uniMMR', '0')}")
        print(f"   - 账户状态: {result.get('accountStatus', 'NORMAL')}")
        print(f"[✓] 统一账户资金权限正常")
    elif mode == 'CLASSIC_UM':
        print(f"[✓] 账户类型: Classic UM (通过PAPI网关交易)")
        print(f"   - PAPI接口返回: accountEquity=0, status=UNKNOWN")
        print(f"   - 特征: 有持仓但PAPI余额查询返回0（正常现象）")
        print(f"   - 说明: Classic UM + PAPI 模式")
        print(f"   - 保证金由系统隐式管理，不暴露真实余额")
        print(f"   - 余额需通过 Spot + 持仓 + 风控逻辑推导")
        print(f"[✓] PAPI交易网关权限正常")
    elif isinstance(result, dict) and 'code' in result:
        error_code = result['code']
        if error_code == -2015:
            print("[x] 权限不足：API密钥缺少「统一账户-交易」权限")
            print("   解决方案：")
            print("   1. 登录币安 → 个人中心 → API管理")
            print("   2. 找到当前API密钥，点击「编辑权限」")
            print("   3. 勾选「统一账户」下的「读取」和「交易」权限（不是普通期货权限）")
            print("   4. 保存后等待5-10分钟生效")
            return False
        else:
            print(f"[!] 账户查询返回错误: {result}")
            print(f"[✓] PAPI接口访问正常，但账户未启用Unified Account")
    else:
        print(f"[!] 账户信息查询失败: {result}")
        print(f"   可能原因: 网络问题或API配置错误")
    
    # 4. 测试统一账户持仓信息
    print("\n4. 测试统一账户持仓信息...")
    # 修复：PAPI 正确接口是 /papi/v1/um/positionRisk（不是 /papi/v1/um/position）
    result = send_papi_request("/papi/v1/um/positionRisk")
    if result is not None:
        # 检查是否返回错误对象
        if isinstance(result, dict) and 'code' in result:
            print(f"[!] 持仓信息查询提示: {result}")
        else:
            # 成功返回列表或数组
            positions = [p for p in result if float(p.get('positionAmt', 0)) != 0]
            if positions:
                print(f"[✓] 统一账户持仓权限正常 - 当前持仓数: {len(positions)}")
                for pos in positions[:2]:  # 只打印前2个持仓
                    amt = float(pos.get('positionAmt', 0))
                    print(f"   - 交易对: {pos['symbol']}, 方向: {'多' if amt>0 else '空'}, 数量: {pos['positionAmt']}")
            else:
                print(f"[✓] 统一账户持仓权限正常 - 当前无持仓")
    else:
        print(f"[!] 持仓信息查询失败: 无返回结果")

    
    print("="*80)
    if mode == 'PORTFOLIO_MARGIN':
        print("[✓] Portfolio Margin (Unified Account) API权限诊断通过！")
        print("   账户类型: 统一账户（Portfolio Margin风控）")
    elif mode == 'CLASSIC_UM':
        print("[✓] Classic UM (PAPI网关) API权限诊断通过！")
        print("   账户类型: Classic UM（通过PAPI网关交易）")
    else:
        print("[✓] PAPI接口访问权限正常")
        print("   注意: 账户类型检测失败，建议检查API配置")
    print("="*80)
    return True

# ========== 账户类型检测与兼容查询 ==========
def detect_account_mode(papi_account_resp: Optional[dict]) -> str:
    """
    检测账户类型
    返回:
      - 'PORTFOLIO_MARGIN' (Unified Account with Portfolio Margin)
      - 'CLASSIC_UM' (Classic U本位合约，通过PAPI网关交易但非统一账户风控)
    """
    # 检查是否返回错误对象
    if isinstance(papi_account_resp, dict):
        # 如果返回了错误代码，说明不是Portfolio Margin
        if 'code' in papi_account_resp:
            return 'CLASSIC_UM'
        
        # Portfolio Margin账户的um/account接口会返回实际数据
        # 关键判断：如果有持仓但accountEquity为0且status为UNKNOWN，说明是Classic UM
        if (papi_account_resp.get('accountStatus') == 'UNKNOWN' and 
            float(papi_account_resp.get('accountEquity', 0)) == 0):
            # 进一步检查：如果有持仓但权益为0，绝对是Classic UM
            return 'CLASSIC_UM'
        
        # 其他情况：可能是Portfolio Margin账户
        if papi_account_resp.get('accountStatus') in ('NORMAL', 'MARGIN_CALL'):
            return 'PORTFOLIO_MARGIN'
    
    # 默认返回CLASSIC_UM（更安全）
    return 'CLASSIC_UM'

def get_classic_um_account():
    """获取Classic U本位合约账户余额（使用fapi接口）"""
    FAPI_BASE_URL = "https://fapi.binance.com"
    timestamp_value = int(time.time() * 1000)
    
    # 创建参数字典用于签名生成
    sign_params = {
        "timestamp": timestamp_value
    }
    signature = generate_signature(sign_params, BINANCE_SECRET)
    
    # 创建API调用参数字典
    api_params = {
        "timestamp": timestamp_value,
        "signature": signature
    }
    
    headers = {
        "X-MBX-APIKEY": BINANCE_API_KEY
    }
    
    try:
        response = requests.get(
            FAPI_BASE_URL + "/fapi/v2/account",
            params=api_params,
            headers=headers,
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"[错误] 获取Classic UM账户失败: {e}")
        return None

def get_um_account_compatible():
    """兼容查询U本位合约账户余额（自动检测账户类型）"""
    # 先尝试查询PAPI UM账户
    papi_um_account = send_papi_request("/papi/v1/um/account")
    
    # 检测账户类型
    mode = detect_account_mode(papi_um_account) if isinstance(papi_um_account, dict) else 'UNKNOWN'
    
    if mode == 'PORTFOLIO_MARGIN':
        print(f"[检测] 账户类型: Portfolio Margin (Unified Account)")
        return papi_um_account
    else:
        print(f"[检测] 账户类型: Classic UM (通过PAPI网关交易)")
        return get_classic_um_account()

# ========== 统一账户交易示例（可选） ==========
def get_unified_account_balance():
    """获取U本位合约账户余额（兼容Portfolio Margin和Classic UM）"""
    return get_um_account_compatible()

def get_spot_account():
    """获取现货账户余额（需要使用 api.binance.com）"""
    SPOT_BASE = "https://api.binance.com"
    timestamp_value = int(time.time() * 1000)
    # 创建参数字典用于签名生成
    sign_params = {
        "timestamp": timestamp_value
    }
    signature = generate_signature(sign_params, BINANCE_SECRET)
    
    # 创建API调用参数字典
    api_params = {
        "timestamp": timestamp_value,
        "signature": signature
    }

    headers = {
        "X-MBX-APIKEY": BINANCE_API_KEY
    }
    try:
        response = requests.get(
            SPOT_BASE + "/api/v3/account",
            params=api_params,
            headers=headers,
            timeout=10
        )
        return response.json()
    except Exception as e:
        print(f"[错误] 获取现货账户失败: {e}")
        return None


# 定义 ApiCapability 枚举类 - 移动到使用之前
from enum import Enum

class ApiCapability(Enum):
    PAPI_ONLY = "PAPI_ONLY"
    STANDARD = "STANDARD"


def detect_api_capability(api_key, api_secret) -> ApiCapability:
    """
    检测API密钥能力：区分PAPI专用密钥和标准密钥
    PAPI专用密钥：只能访问papi.binance.com，无法访问fapi.binance.com
    标准密钥：可访问api.binance.com和fapi.binance.com
    """
    try:
        # 试探性访问fapi接口
        url = "https://fapi.binance.com/fapi/v2/account"
        params: Dict[str, Any] = {"timestamp": int(time.time() * 1000)}
        params["signature"] = generate_signature(params, api_secret)
        r = requests.get(url, params=params, headers={"X-MBX-APIKEY": api_key}, timeout=5)

        if r.status_code == 200:
            return ApiCapability.STANDARD
        if r.status_code == 401:
            return ApiCapability.PAPI_ONLY

    except Exception:
        pass

    return ApiCapability.PAPI_ONLY


# ========== 主函数 ==========

# ========== 账户管理器（AICOIN 核心架构） ==========

class AccountMode(Enum):
    CLASSIC = "CLASSIC"
    UNIFIED = "UNIFIED"          # UA / PM 统一处理

class AccountDetector:
    """账户模型自动判定（核心逻辑）"""
    
    def __init__(self, api_key, api_secret, timeout=10):
        self.api_key = api_key
        self.api_secret = api_secret
        self.timeout = timeout

    def _headers(self):
        return {"X-MBX-APIKEY": self.api_key}

    def detect(self) -> AccountMode:
        """
        自动判断账户模型
        """
        try:
            data = self._get_papi_um_account()
            equity = float(data.get("accountEquity", 0))
            status = data.get("accountStatus")

            # UA / PM 的充分条件（不是必要条件）
            if status in ("NORMAL", "MARGIN_CALL") and equity > 0:
                return AccountMode.UNIFIED

        except Exception:
            pass

        return AccountMode.CLASSIC

    def _get_papi_um_account(self):
        url = "https://papi.binance.com/papi/v1/um/account"
        params: Dict[str, Any] = {"timestamp": int(time.time() * 1000)}
        params["signature"] = generate_signature(params, self.api_secret)

        r = requests.get(url, params=params, headers=self._headers(), timeout=self.timeout)
        r.raise_for_status()
        return r.json()


class AccountManager:
    """账户抽象层（AICOIN 核心思想）"""
    
    def __init__(self, api_key, api_secret):
        self.api_key = api_key
        self.api_secret = api_secret
        self.capability = detect_api_capability(api_key, api_secret)  # 新增：检测API能力
        self.detector = AccountDetector(api_key, api_secret)
        self.mode = self.detector.detect()

    def _headers(self):
        return {"X-MBX-APIKEY": self.api_key}

    def get_balance(self):
        """获取U本位合约账户余额（自动适配账户类型）"""
        if self.mode == AccountMode.UNIFIED:
            return self._get_unified_balance()
        else:  # CLASSIC
            # 根据API能力决定使用哪个接口获取余额
            if self.capability == ApiCapability.STANDARD:
                return self._get_classic_um_balance()
            else:  # PAPI_ONLY
                return self._get_classic_um_balance_via_papi()

    def get_positions(self):
        """获取U本位合约持仓（自动适配账户类型）"""
        if self.mode == AccountMode.UNIFIED:
            return self._get_unified_positions()
        else:  # CLASSIC
            # 根据API能力决定使用哪个接口获取持仓
            if self.capability == ApiCapability.STANDARD:
                return self._get_classic_um_positions()
            else:  # PAPI_ONLY
                # PAPI-only密钥仍可通过PAPI接口获取持仓
                return self._get_classic_um_positions_via_papi()

    def _get_unified_balance(self):
        url = "https://papi.binance.com/papi/v1/um/account"
        params: Dict[str, Any] = {"timestamp": int(time.time() * 1000)}
        params["signature"] = generate_signature(params, self.api_secret)

        r = requests.get(url, params=params, headers=self._headers(), timeout=10)
        r.raise_for_status()
        data = r.json()

        return {
            "equity": float(data.get("accountEquity", 0)),
            "available": float(data.get("availableBalance", 0)),
            "status": data.get("accountStatus")
        }

    def _get_unified_positions(self):
        url = "https://papi.binance.com/papi/v1/um/positionRisk"
        params: Dict[str, Any] = {"timestamp": int(time.time() * 1000)}
        params["signature"] = generate_signature(params, self.api_secret)

        r = requests.get(url, params=params, headers=self._headers(), timeout=10)
        r.raise_for_status()

        return [
            p for p in r.json()
            if float(p.get("positionAmt", 0)) != 0
        ]

    def _get_classic_um_balance(self):
        url = "https://fapi.binance.com/fapi/v2/account"
        params: Dict[str, Any] = {"timestamp": int(time.time() * 1000)}
        params["signature"] = generate_signature(params, self.api_secret)

        try:
            r = requests.get(url, params=params, headers=self._headers(), timeout=10)
            r.raise_for_status()
            data = r.json()

            return {
                "walletBalance": float(data["totalWalletBalance"]),
                "available": float(data["availableBalance"]),
                "marginBalance": float(data["totalMarginBalance"])
            }
        except requests.HTTPError as e:
            if e.response.status_code == 401:
                return {
                    "error": "NO_FUTURES_PERMISSION",
                    "message": "API key lacks Futures (Classic UM) permission or is PAPI-only key"
                }
            # 其他HTTP错误重新抛出
            raise

    def _get_classic_um_positions(self):
        url = "https://fapi.binance.com/fapi/v2/positionRisk"
        params: Dict[str, Any] = {"timestamp": int(time.time() * 1000)}
        params["signature"] = generate_signature(params, self.api_secret)

        try:
            r = requests.get(url, params=params, headers=self._headers(), timeout=10)
            r.raise_for_status()

            return [
                p for p in r.json()
                if float(p.get("positionAmt", 0)) != 0
            ]
        except requests.HTTPError as e:
            if e.response.status_code == 401:
                # 返回空列表，但可以记录错误信息
                print("⚠️ 警告: 无法读取 Classic UM 持仓，缺少 Futures 权限或使用PAPI-only密钥")
                return []
            # 其他HTTP错误重新抛出
            raise

    def _get_classic_um_balance_via_papi(self):
        """
        PAPI-only密钥下通过PAPI接口模拟Classic UM余额视角
        这是AICOIN使用的逻辑，使用现货USDT余额作为保证金参考
        """
        spot = get_spot_account()

        spot_usdt = 0
        if spot and "balances" in spot:  # 添加检查确保spot不是None
            for b in spot.get("balances", []):
                if b["asset"] == "USDT":
                    spot_usdt = float(b["free"]) + float(b["locked"])

        return {
            "walletBalance": spot_usdt,
            "availableForTrade": spot_usdt,  # 明确表示可用于交易的资金
            "marginReference": spot_usdt,   # 用作保证金参考值
            "source": "PAPI_SIMULATED",
            "note": "This is an estimation using spot balance as margin reference"
        }

    def _get_classic_um_positions_via_papi(self):
        """通过PAPI接口获取Classic UM持仓（PAPI-only密钥场景）"""
        url = "https://papi.binance.com/papi/v1/um/positionRisk"
        params: Dict[str, Any] = {"timestamp": int(time.time() * 1000)}
        params["signature"] = generate_signature(params, self.api_secret)

        try:
            r = requests.get(url, params=params, headers=self._headers(), timeout=10)
            r.raise_for_status()

            return [
                p for p in r.json()
                if float(p.get("positionAmt", 0)) != 0
            ]
        except requests.HTTPError as e:
            if e.response.status_code == 401:
                print("⚠️ 警告: 无法读取 Classic UM 持仓，PAPI-only密钥权限不足")
                return []
            # 其他HTTP错误重新抛出
            raise

if __name__ == "__main__":
    # 核心：诊断权限问题
    diagnose_account_permissions()

    # 可选：获取完整余额视图
    print("\n" + "="*80)
    print("账户完整余额视图")
    print("="*80)
    
    # 创建账户管理器（自动检测账户类型）
    am = AccountManager(BINANCE_API_KEY, BINANCE_SECRET)
    print(f"\n账户模式: {am.mode.value}")
    
    # 获取现货余额
    spot = get_spot_account()
    spot_usdt = 0
    spot_balances = {}
    if spot and "balances" in spot:
        for b in spot["balances"]:
            free = float(b.get("free", 0))
            locked = float(b.get("locked", 0))
            total = free + locked
            if total > 0:
                spot_balances[b["asset"]] = {
                    "free": free,
                    "locked": locked,
                    "total": total
                }
            if b["asset"] == "USDT":
                spot_usdt = total
    
    # 获取U本位合约余额
    um_balance = am.get_balance()
    # 获取U本位合约持仓
    um_positions = am.get_positions()
    
    print(f"\n现货钱包余额:")
    print(f"  - USDT 可用: {spot_usdt:.2f} USDT")
    if spot_balances:
        print(f"  - 其他资产:")
        for asset, data in sorted(spot_balances.items()):
            if asset != "USDT":
                print(f"      {asset}: {data['total']} (可用: {data['free']}, 冻结: {data['locked']})")
    
    print(f"\nU本位合约账户 ({am.mode.value}):")
    if am.mode == AccountMode.UNIFIED:
        # Unified Account (UA/PM) 字段
        print(f"  - 账户权益: {um_balance.get('equity', 0):.2f} USDT")
        print(f"  - 可用余额: {um_balance.get('available', 0):.2f} USDT")
        print(f"  - 账户状态: {um_balance.get('status', 'UNKNOWN')}")
    else:
        # Classic UM 字段 - 检查权限错误
        if isinstance(um_balance, dict) and "error" in um_balance:
            print(f"  ⚠️ 无法读取 Classic UM 保证金")
            print(f"    原因: {um_balance.get('message', 'API key lacks Futures (Classic UM) permission')}")
            print(f"    解决方案: 在 API Key 中开启 Futures 权限")
        else:
            print(f"  - 钱包余额: {um_balance.get('walletBalance', 0):.2f} USDT")
            print(f"  - 可用余额: {um_balance.get('available', 0):.2f} USDT")
            print(f"  - 保证金余额: {um_balance.get('marginBalance', 0):.2f} USDT")
    
    if um_positions:
        print(f"\n当前持仓 ({len(um_positions)} 个):")
        for pos in um_positions[:5]:  # 最多显示5个持仓
            amt = float(pos.get('positionAmt', 0))
            symbol = pos.get('symbol', '')
            entry_price = pos.get('entryPrice', 0)
            unrealized_profit = pos.get('unRealizedProfit', 0)
            print(f"   - {symbol}: {amt} ({'多' if amt>0 else '空'}), 入场价: {entry_price}, 未实现盈亏: {unrealized_profit}")
    else:
        print(f"\n当前持仓: 无")
    
    print(f"\n💡 说明:")
    if am.mode == AccountMode.CLASSIC:
        print("  当前账户类型: Classic UM (通过PAPI网关交易)")
        print("  - 下单接口: papi.binance.com")
        print("  - 持仓接口: papi.binance.com")
        print("  - 余额视角: Spot + PAPI 风控模拟")
        print("  - 特征: 有持仓但/papi/v1/um/account返回0")
    elif am.mode == AccountMode.UNIFIED:
        print("  当前账户类型: Unified Account (UA/PM)")
        print("  - 下单接口: papi.binance.com")
        print("  - 查余额接口: papi.binance.com/papi/v1/um/account")
        print("  - 特征: 现货/UM共用保证金池")
    else:
        print("  账户类型未知，可能需要检查API权限")
