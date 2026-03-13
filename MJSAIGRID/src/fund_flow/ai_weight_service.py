"""
DeepSeek AI Weight Service - AI动态权重调度服务

核心功能:
1. 调用 DeepSeek API 生成因子权重
2. 严格的 JSON 输出校验
3. 失败降级策略
4. 智能缓存

约束:
- 不输出交易方向
- 不输出阈值/仓位/杠杆
- 只输出权重和置信度
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import time
import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


def _safe_print(message: str) -> None:
    try:
        print(message)
    except UnicodeEncodeError:
        import sys

        stream = getattr(sys, "stdout", None)
        if stream is None:
            return
        try:
            buffer = getattr(stream, "buffer", None)
            if buffer is not None:
                buffer.write(f"{message}\n".encode("utf-8", errors="replace"))
                buffer.flush()
            else:
                safe_message = str(message).encode("ascii", errors="replace").decode("ascii")
                stream.write(f"{safe_message}\n")
                stream.flush()
        except Exception:
            pass

# 权重键列表
REQUIRED_WEIGHT_KEYS = [
    "cvd", "cvd_momentum", "oi_delta", "funding",
    "depth_ratio", "imbalance", "liquidity_delta", "micro_delta"
]

REQUIRED_REGIME_VIEW_KEYS = ["name", "bias", "notes"]
REQUIRED_RISK_FLAG_KEYS = ["trap", "phantom", "wide_spread", "data_stale"]
ALLOWED_TOP_LEVEL_KEYS = {
    "version",
    "weights",
    "confidence",
    "fallback_used",
    "regime_view",
    "risk_flags",
    "reasoning_bullets",
}
STRICT_TOOL_NAME = "emit_weight_router_payload"

# 系统提示词 - 固定不变 (优化后节省TOKEN)
SYSTEM_PROMPT = """你是Weight Router。仅输出权重&置信度JSON，禁:方向/动作/阈值/仓位/杠杆/价格。
本地负责15m过滤/5m触发/3m确认/MACD/KDJ/执行。你只调权重&confidence。
tech/capture仅调confidence，不覆盖本地规则。
必须返回且只返回这些字段: version, weights, confidence, fallback_used, regime_view, risk_flags, reasoning_bullets。
weights含8因子且和=1。regime_view含name/bias/notes。risk_flags含trap/phantom/wide_spread/data_stale。
sample_ok=false或stale>30或missing非空 → fallback=true+默认权。
TREND偏: cvd/oi_delta/funding/depth_ratio/liquidity_delta。RANGE偏: imbalance/micro_delta，低: cvd_momentum/oi_delta。
trap/phantom/wide_spread/high_vol → 低动量权+confidence。"""

# 用户提示词模板 (优化后节省TOKEN)
USER_PROMPT_TEMPLATE = """输入:{request_json}
输出仅脚本消费字段:
1. version=weight-router-v1
2. weights含8因子(cvd,cvd_momentum,oi_delta,funding,depth_ratio,imbalance,liquidity_delta,micro_delta),各[0,1],和=1
3. confidence[0,1]
4. fallback_used:true/false
5. regime_view含name/bias/notes
6. risk_flags含trap/phantom/wide_spread/data_stale
7. reasoning_bullets为字符串数组
禁:分析文/买卖/markdown/额外字段"""

# 禁止输出的词列表
FORBIDDEN_WORDS = [
    "BUY", "SELL", "LONG", "SHORT",
    "close_threshold", "leverage", "position",
    "开多", "开空", "平仓", "买入", "卖出",
    "threshold", "stop_loss", "take_profit"
]

STRICT_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "version": {
            "type": "string",
            "enum": ["weight-router-v1"],
        },
        "weights": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                key: {"type": "number", "minimum": 0.0, "maximum": 1.0}
                for key in REQUIRED_WEIGHT_KEYS
            },
            "required": REQUIRED_WEIGHT_KEYS,
        },
        "confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
        },
        "fallback_used": {"type": "boolean"},
        "regime_view": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string"},
                "bias": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": REQUIRED_REGIME_VIEW_KEYS,
        },
        "risk_flags": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "trap": {"type": "boolean"},
                "phantom": {"type": "boolean"},
                "wide_spread": {"type": "boolean"},
                "data_stale": {"type": "boolean"},
            },
            "required": REQUIRED_RISK_FLAG_KEYS,
        },
        "reasoning_bullets": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "version",
        "weights",
        "confidence",
        "fallback_used",
        "regime_view",
        "risk_flags",
        "reasoning_bullets",
    ],
}


@dataclass
class AIWeightResponse:
    """AI 权重响应"""
    version: str = "weight-router-v1"
    symbol: str = ""
    timestamp_utc: str = ""
    regime_view: Dict[str, Any] = field(default_factory=dict)
    risk_flags: Dict[str, bool] = field(default_factory=dict)
    weights: Dict[str, float] = field(default_factory=dict)
    confidence: float = 0.5
    reasoning_bullets: List[str] = field(default_factory=list)
    fallback_used: bool = False
    api_mode: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    raw_response: str = ""
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "symbol": self.symbol,
            "timestamp_utc": self.timestamp_utc,
            "regime_view": self.regime_view,
            "risk_flags": self.risk_flags,
            "weights": self.weights,
            "confidence": self.confidence,
            "reasoning_bullets": self.reasoning_bullets,
            "fallback_used": self.fallback_used,
            "api_mode": self.api_mode,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "error": self.error,
        }


@dataclass
class DefaultWeights:
    """默认权重配置"""
    trend_cvd: float = 0.24
    trend_cvd_momentum: float = 0.14
    trend_oi_delta: float = 0.22
    trend_funding: float = 0.10
    trend_depth_ratio: float = 0.15
    trend_imbalance: float = 0.10
    trend_liquidity_delta: float = 0.08
    trend_micro_delta: float = 0.06
    
    range_cvd: float = 0.10
    range_cvd_momentum: float = 0.15
    range_oi_delta: float = 0.05
    range_funding: float = 0.05
    range_depth_ratio: float = 0.10
    range_imbalance: float = 0.35
    range_liquidity_delta: float = 0.12
    range_micro_delta: float = 0.18


class DeepSeekAIService:
    """
    DeepSeek AI 权重调度服务
    
    功能:
    1. 调用 DeepSeek API 生成权重
    2. 严格校验输出
    3. 失败降级
    4. 智能缓存
    """
    
    # API 配置
    DEFAULT_API_URL = "https://api.deepseek.com/v1/chat/completions"
    DEFAULT_MODEL = "deepseek-chat"
    DEFAULT_TIMEOUT = 15
    DEFAULT_MAX_RETRIES = 2
    DEFAULT_PROMPT_COST_PER_1K_USD = 0.00028
    DEFAULT_COMPLETION_COST_PER_1K_USD = 0.00042
    
    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config = config or {}
        ai_cfg = self.config.get("deepseek_ai", {})
        
        # API 配置
        self.api_key = os.environ.get("DEEPSEEK_API_KEY") or ai_cfg.get("api_key", "")
        self.api_url = ai_cfg.get("api_url", self.DEFAULT_API_URL)
        self.model = ai_cfg.get("model", self.DEFAULT_MODEL)
        self.timeout = int(ai_cfg.get("timeout", self.DEFAULT_TIMEOUT))
        self.max_retries = int(ai_cfg.get("max_retries", self.DEFAULT_MAX_RETRIES))
        self.strict_schema_enabled = bool(ai_cfg.get("strict_schema_enabled", True))
        self.strict_schema_fallback_enabled = bool(ai_cfg.get("strict_schema_fallback_enabled", True))
        self.strict_api_url = str(ai_cfg.get("strict_api_url", "") or "").strip() or self._derive_strict_api_url(self.api_url)
        self._project_root = Path(__file__).resolve().parents[2]
        self._usage_jsonl_name = "deepseek_usage_utc.jsonl"
        self._usage_csv_name = "deepseek_usage_utc.csv"
        pricing_cfg = ai_cfg.get("pricing", {}) if isinstance(ai_cfg.get("pricing"), dict) else {}
        self.prompt_cost_per_1k_usd = max(
            0.0,
            self._to_float(pricing_cfg.get("prompt_per_1k_usd"), self.DEFAULT_PROMPT_COST_PER_1K_USD),
        )
        self.completion_cost_per_1k_usd = max(
            0.0,
            self._to_float(pricing_cfg.get("completion_per_1k_usd"), self.DEFAULT_COMPLETION_COST_PER_1K_USD),
        )
        self.pricing_basis = str(pricing_cfg.get("basis", "official_input_cache_miss")).strip() or "official_input_cache_miss"
        
        # 功能开关
        self.enabled = bool(ai_cfg.get("enabled", False)) and bool(self.api_key)
        
        # 默认权重
        dw_cfg = ai_cfg.get("default_weights", {})
        self.default_weights = DefaultWeights(
            trend_cvd=float(dw_cfg.get("trend_cvd", 0.24)),
            trend_cvd_momentum=float(dw_cfg.get("trend_cvd_momentum", 0.14)),
            trend_oi_delta=float(dw_cfg.get("trend_oi_delta", 0.22)),
            trend_funding=float(dw_cfg.get("trend_funding", 0.10)),
            trend_depth_ratio=float(dw_cfg.get("trend_depth_ratio", 0.15)),
            trend_imbalance=float(dw_cfg.get("trend_imbalance", 0.10)),
            trend_liquidity_delta=float(dw_cfg.get("trend_liquidity_delta", 0.08)),
            trend_micro_delta=float(dw_cfg.get("trend_micro_delta", 0.06)),
            range_cvd=float(dw_cfg.get("range_cvd", 0.10)),
            range_cvd_momentum=float(dw_cfg.get("range_cvd_momentum", 0.15)),
            range_oi_delta=float(dw_cfg.get("range_oi_delta", 0.05)),
            range_funding=float(dw_cfg.get("range_funding", 0.05)),
            range_depth_ratio=float(dw_cfg.get("range_depth_ratio", 0.10)),
            range_imbalance=float(dw_cfg.get("range_imbalance", 0.35)),
            range_liquidity_delta=float(dw_cfg.get("range_liquidity_delta", 0.12)),
            range_micro_delta=float(dw_cfg.get("range_micro_delta", 0.18)),
        )
        
        # 缓存配置
        self.cache_ttl = int(ai_cfg.get("cache_ttl", 300))  # 5分钟
        self._cache: Dict[str, Tuple[AIWeightResponse, float]] = {}
        
        # 统计
        self._stats = {
            "total_requests": 0,
            "api_calls": 0,
            "cache_hits": 0,
            "fallbacks": 0,
            "errors": 0,
        }
        
        # HTTP 客户端 (延迟初始化)
        self._http_client = None

    @staticmethod
    def _derive_strict_api_url(api_url: str) -> str:
        raw = str(api_url or "").strip()
        if not raw:
            return "https://api.deepseek.com/beta/chat/completions"
        if "/v1/chat/completions" in raw:
            return raw.replace("/v1/chat/completions", "/beta/chat/completions")
        if "/chat/completions" in raw and "/beta/" not in raw:
            return raw.replace("/chat/completions", "/beta/chat/completions")
        return raw

    def _resolve_usage_log_root_dir(self) -> Path:
        log_cfg = self.config.get("logging", {}) if isinstance(self.config.get("logging"), dict) else {}
        root_hint = (
            log_cfg.get("bucket_root_dir")
            or log_cfg.get("runtime_root_dir")
            or "logs"
        )
        root = Path(root_hint)
        if not root.is_absolute():
            root = self._project_root / root
        return root

    def _resolve_usage_day_dir_utc(self, now_utc: Optional[datetime] = None) -> Path:
        ts = now_utc or datetime.now(timezone.utc)
        return self._resolve_usage_log_root_dir() / ts.strftime("%Y-%m") / ts.strftime("%Y-%m-%d") / "fund_flow"

    def _resolve_usage_jsonl_path_utc(self, now_utc: Optional[datetime] = None) -> Path:
        return self._resolve_usage_day_dir_utc(now_utc) / self._usage_jsonl_name

    def _resolve_usage_csv_path_utc(self, now_utc: Optional[datetime] = None) -> Path:
        return self._resolve_usage_day_dir_utc(now_utc) / self._usage_csv_name
    
    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return default
    
    def _get_http_client(self):
        """延迟初始化 HTTP 客户端"""
        if self._http_client is None:
            try:
                import requests
                self._http_client = requests
            except ImportError:
                logger.warning("requests not installed, AI calls will fail")
        return self._http_client
    
    def _build_user_prompt(self, context: Dict[str, Any]) -> str:
        """构建用户提示词"""
        request_payload = self._build_request_payload(context)
        request_json = json.dumps(request_payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return USER_PROMPT_TEMPLATE.format(request_json=request_json)

    @staticmethod
    def _request_profile(request_mode: str) -> Dict[str, Any]:
        mode = str(request_mode or "generic").strip().lower()
        if mode == "position_review":
            return {
                "mode": mode,
                "freshness_timeframe": "5m",
                "min_history_bars": 4,
                "preferred_zscore_bars": 20,
            }
        if mode == "entry_review":
            return {
                "mode": mode,
                "freshness_timeframe": "5m",
                "min_history_bars": 4,
                "preferred_zscore_bars": 20,
            }
        return {
            "mode": mode,
            "freshness_timeframe": "15m",
            "min_history_bars": 4,
            "preferred_zscore_bars": 20,
        }

    def _default_weight_payload(self, regime: str, dw: "DefaultWeights") -> Dict[str, Any]:
        def _f(x: Any, default: float = 0.0, ndigits: int = 4) -> float:
            try:
                v = float(x)
            except Exception:
                v = float(default)
            if v != v or v == float("inf") or v == float("-inf"):
                v = float(default)
            return round(v, ndigits)

        regime_up = str(regime or "TREND").upper()
        if regime_up == "RANGE":
            return {
                "cvd": _f(dw.range_cvd),
                "cvd_momentum": _f(dw.range_cvd_momentum),
                "oi_delta": _f(dw.range_oi_delta),
                "funding": _f(dw.range_funding),
                "depth_ratio": _f(dw.range_depth_ratio),
                "imbalance": _f(dw.range_imbalance),
                "liquidity_delta": _f(dw.range_liquidity_delta),
                "micro_delta": _f(dw.range_micro_delta),
            }

        return {
            "cvd": _f(dw.trend_cvd),
            "cvd_momentum": _f(dw.trend_cvd_momentum),
            "oi_delta": _f(dw.trend_oi_delta),
            "funding": _f(dw.trend_funding),
            "depth_ratio": _f(dw.trend_depth_ratio),
            "imbalance": _f(dw.trend_imbalance),
            "liquidity_delta": _f(dw.trend_liquidity_delta),
            "micro_delta": _f(dw.trend_micro_delta),
        }

    def _build_request_payload(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """构建紧凑、稳定、低 token 的 AI 输入载荷"""
        dw = self.default_weights
        missing = context.get("missing_fields", [])
        if not isinstance(missing, list):
            missing = []
        rf = context.get("risk_flags", {})
        if not isinstance(rf, dict):
            rf = {}

        tech_context = context.get("tech_context", {})
        if not isinstance(tech_context, dict):
            tech_context = {}

        def _f(x: Any, default: float = 0.0, ndigits: int = 4) -> float:
            try:
                v = float(x)
            except Exception:
                v = float(default)
            if v != v or v == float("inf") or v == float("-inf"):
                v = float(default)
            return round(v, ndigits)

        mode = str(context.get("request_mode") or "generic").strip().lower()
        regime = str(context.get("regime", "NO_TRADE"))
        common_payload = {
            "mode": mode,
            "symbol": str(context.get("symbol", "UNKNOWN")),
            "regime": regime,
            "flow": {
                "confirm": bool(context.get("flow_confirm", False)),
                "c3": int(self._to_float(context.get("consistency_3bars"), 0)),
                "cvd": _f(context.get("cvd_z"), 0.0),
                "cvdm": _f(context.get("cvd_mom_z"), 0.0),
                "oi": _f(context.get("oi_delta_z"), 0.0),
                "fund": _f(context.get("funding_z"), 0.0, 6),
                "depth": _f(context.get("depth_ratio_z"), 0.0),
                "imb": _f(context.get("imbalance_z"), 0.0),
                "liq": _f(context.get("liquidity_delta_z"), 0.0),
                "micro": _f(context.get("micro_delta_z"), 0.0),
            },
            "micro": {
                "spread": _f(context.get("spread_z"), 0.0),
                "trap": _f(context.get("trap_score"), 0.0),
                "phantom": _f(context.get("phantom_score"), 0.0),
                "trap_ok": bool(context.get("trap_confirmed", False)),
                "vol_cool": bool(context.get("extreme_vol_cooldown", False)),
            },
            "tech": {
                "ma": str(tech_context.get("ma10_bias_1h", "FLAT")),
                "mc": str(tech_context.get("macd_cross_5m", "NONE")),
                "mz": str(tech_context.get("macd_zone_5m", "NEAR_ZERO")),
                "mh": bool(tech_context.get("macd_hist_expand_5m", False)),
                "kc": str(tech_context.get("kdj_cross_5m", "NONE")),
                "kz": str(tech_context.get("kdj_zone_5m", "MID")),
            },
            "capture": {
                "r3": _f(context.get("ret_3m"), 0.0),
                "side": str(context.get("capture_confirm_3m_side", "NONE")),
                "mcl": bool(context.get("micro_confirm_3m_long", False)),
                "mcs": bool(context.get("micro_confirm_3m_short", False)),
                "c3l": bool(context.get("capture_confirm_3m_long", False)),
                "c3s": bool(context.get("capture_confirm_3m_short", False)),
            },
            "risk": {
                "trap": bool(rf.get("trap", False)),
                "phantom": bool(rf.get("phantom", False)),
                "wide": bool(rf.get("wide_spread", False)),
                "stale": bool(rf.get("data_stale", False)),
            },
            "dq": {
                "miss": [str(x) for x in missing[:6]],
                "stale": int(self._to_float(context.get("stale_seconds"), 0)),
                "ok": bool(context.get("sample_ok", True)),
                "hist": int(self._to_float(context.get("history_bars"), 0)),
                "cold": bool(context.get("cold_start", False)),
                "fresh_tf": str(context.get("freshness_timeframe") or ""),
            },
            "dw": self._default_weight_payload(regime, dw),
        }

        if mode == "entry_review":
            common_payload["regime_info"] = {
                "trend": _f(context.get("trend_strength"), 0.0),
                "adx": _f(context.get("adx"), 0.0),
                "atr": _f(context.get("atr_pct"), 0.0, 6),
                "ema": str(context.get("ema_bias", "FLAT")),
            }
            return common_payload

        if mode == "position_review":
            common_payload["position_regime"] = {
                "trend": _f(context.get("trend_strength"), 0.0),
                "ema": str(context.get("ema_bias", "FLAT")),
            }
            return common_payload

        common_payload["regime_info"] = {
            "trend": _f(context.get("trend_strength"), 0.0),
            "adx": _f(context.get("adx"), 0.0),
            "atr": _f(context.get("atr_pct"), 0.0, 6),
            "ema": str(context.get("ema_bias", "FLAT")),
        }
        return common_payload

    @staticmethod
    def _build_response_log_payload(response: "AIWeightResponse") -> Dict[str, Any]:
        """AI 决策日志摘要，不包含分析文本"""
        return {
            "symbol": str(response.symbol or ""),
            "timestamp_utc": str(response.timestamp_utc or ""),
            "api_mode": str(response.api_mode or ""),
            "confidence": round(float(response.confidence or 0.0), 4),
            "fallback_used": bool(response.fallback_used),
            "usage": {
                "prompt_tokens": int(response.prompt_tokens or 0),
                "completion_tokens": int(response.completion_tokens or 0),
                "total_tokens": int(response.total_tokens or 0),
            },
            "weights": dict(response.weights or {}),
            "regime_view": dict(response.regime_view or {}),
            "risk_flags": dict(response.risk_flags or {}),
            "error": response.error,
        }
    
    def _make_structured_cache_key(self, context: Dict[str, Any]) -> str:
        """
        基于“决策结构”构建稳定 cache key
        避免 prompt 文本微小变化导致缓存失效
        """
        symbol = str(context.get("symbol", "UNKNOWN")).upper()
        regime = str(context.get("regime_name") or context.get("regime") or "NO_TRADE").upper()
        request_mode = str(context.get("request_mode") or "generic").strip().lower()

        trend_strength = self._to_float(context.get("trend_strength"), 0.0)
        spread_z = self._to_float(context.get("spread_z"), 0.0)
        consistency = int(self._to_float(context.get("consistency_3bars"), 0))

        rf = context.get("risk_flags", {})
        if not isinstance(rf, dict):
            rf = {}

        flow_confirm = bool(context.get("flow_confirm", False))
        trap_flag = bool(context.get("trap_flag", rf.get("trap", False)))
        phantom_flag = bool(context.get("phantom_flag", rf.get("phantom", False)))
        wide_spread = bool(context.get("wide_spread", rf.get("wide_spread", False)))
        sample_ok = bool(context.get("sample_ok", True))

        # === 桶化 ===
        trend_bucket = round(trend_strength, 1)
        if spread_z >= 2.5:
            spread_bucket = 3
        elif spread_z >= 1.5:
            spread_bucket = 2
        elif spread_z >= 0.8:
            spread_bucket = 1
        else:
            spread_bucket = 0

        # consistency 只取 0~3
        consistency_bucket = max(0, min(3, consistency))

        raw_key = (
            f"{symbol}|"
            f"{regime}|"
            f"m{request_mode}|"
            f"ts{trend_bucket}|"
            f"sp{spread_bucket}|"
            f"cf{int(flow_confirm)}|"
            f"c3{consistency_bucket}|"
            f"tp{int(trap_flag)}|"
            f"ph{int(phantom_flag)}|"
            f"ws{int(wide_spread)}|"
            f"ok{int(sample_ok)}"
        )

        return hashlib.md5(raw_key.encode("utf-8")).hexdigest()

    def _cache_key(self, context: Dict[str, Any]) -> str:
        """兼容旧调用：转到结构化 cache key"""
        return self._make_structured_cache_key(context)
    
    def _get_cached(self, cache_key: str) -> Optional[AIWeightResponse]:
        """从缓存获取"""
        entry = self._cache.get(cache_key)
        if entry is None:
            return None
        response, expires_at = entry
        if time.time() > expires_at:
            del self._cache[cache_key]
            return None
        self._stats["cache_hits"] += 1
        return response

    def _get_cache_ttl_for_context(self, context: Dict[str, Any]) -> int:
        """按 regime 返回动态 TTL（秒）: TREND 15m / RANGE 10m / 其他 5m"""
        regime = str(context.get("regime_name") or context.get("regime") or "NO_TRADE").upper()
        if regime == "TREND":
            return 15 * 60
        if regime == "RANGE":
            return 10 * 60
        return 5 * 60

    def _set_cache(
        self,
        cache_key: str,
        response: AIWeightResponse,
        ttl_seconds: Optional[int] = None,
    ) -> None:
        """设置缓存"""
        ttl = int(ttl_seconds) if ttl_seconds is not None else int(self.cache_ttl)
        ttl = max(1, ttl)
        expires_at = time.time() + ttl
        self._cache[cache_key] = (response, expires_at)
        
        # 清理过期缓存
        now = time.time()
        expired_keys = [k for k, v in self._cache.items() if v[1] < now]
        for k in expired_keys:
            del self._cache[k]
    
    def _should_fallback(self, context: Dict[str, Any]) -> Tuple[bool, str]:
        """判断是否应该直接使用降级策略"""
        # 数据质量检查
        sample_ok = context.get("sample_ok", True)
        stale_seconds = self._to_float(context.get("stale_seconds"), 0)
        missing_fields = context.get("missing_fields", [])
        
        if not sample_ok:
            return True, "sample_not_ok"
        if stale_seconds > 90:
            return True, f"data_stale({stale_seconds}s)"
        if missing_fields and isinstance(missing_fields, list) and len(missing_fields) > 0:
            return True, f"missing_fields:{','.join(missing_fields[:3])}"
        
        return False, ""

    def _build_sample_quality_log_payload(
        self,
        context: Dict[str, Any],
        reason: str,
    ) -> Dict[str, Any]:
        """构建 AI 样本质量日志，便于定位 sample_not_ok 的具体来源"""
        missing_fields = context.get("missing_fields", [])
        if not isinstance(missing_fields, list):
            missing_fields = []

        stale_seconds = int(self._to_float(context.get("stale_seconds"), 0))
        history_bars = int(self._to_float(context.get("history_bars"), 0))
        history_min_bars = int(self._to_float(context.get("history_min_bars"), 0))
        request_mode = str(context.get("request_mode") or "generic")
        freshness_timeframe = str(context.get("freshness_timeframe") or "")
        sample_ok = bool(context.get("sample_ok", True))

        details: List[str] = []
        if missing_fields:
            details.append(f"missing:{','.join(str(x) for x in missing_fields[:6])}")
        if history_min_bars > 0 and history_bars < history_min_bars:
            details.append(f"history:{history_bars}/{history_min_bars}")
        if stale_seconds > 90:
            details.append(f"stale:{stale_seconds}s")
        if not details and not sample_ok:
            details.append("sample_ok_false_without_expanded_detail")

        return {
            "symbol": str(context.get("symbol", "")),
            "regime": str(context.get("regime", "")),
            "request_mode": request_mode,
            "reason": reason,
            "detail": details,
            "sample_ok": sample_ok,
            "missing_fields": [str(x) for x in missing_fields[:6]],
            "stale_seconds": stale_seconds,
            "freshness_timeframe": freshness_timeframe,
            "history_bars": history_bars,
            "history_min_bars": history_min_bars,
            "cold_start": bool(context.get("cold_start", False)),
        }
    
    def _get_default_weights(self, regime: str) -> Dict[str, float]:
        """获取默认权重并归一化"""
        dw = self.default_weights
        
        if regime == "TREND":
            weights = {
                "cvd": dw.trend_cvd,
                "cvd_momentum": dw.trend_cvd_momentum,
                "oi_delta": dw.trend_oi_delta,
                "funding": dw.trend_funding,
                "depth_ratio": dw.trend_depth_ratio,
                "imbalance": dw.trend_imbalance,
                "liquidity_delta": dw.trend_liquidity_delta,
                "micro_delta": dw.trend_micro_delta,
            }
        elif regime == "RANGE":
            weights = {
                "cvd": dw.range_cvd,
                "cvd_momentum": dw.range_cvd_momentum,
                "oi_delta": dw.range_oi_delta,
                "funding": dw.range_funding,
                "depth_ratio": dw.range_depth_ratio,
                "imbalance": dw.range_imbalance,
                "liquidity_delta": dw.range_liquidity_delta,
                "micro_delta": dw.range_micro_delta,
            }
        else:
            # NO_TRADE 或其他情况使用趋势权重
            weights = {
                "cvd": dw.trend_cvd,
                "cvd_momentum": dw.trend_cvd_momentum,
                "oi_delta": dw.trend_oi_delta,
                "funding": dw.trend_funding,
                "depth_ratio": dw.trend_depth_ratio,
                "imbalance": dw.trend_imbalance,
                "liquidity_delta": dw.trend_liquidity_delta,
                "micro_delta": dw.trend_micro_delta,
            }
        
        # 归一化
        total = sum(weights.values())
        if total > 0:
            weights = {k: v / total for k, v in weights.items()}
        
        return weights
    
    def _usage_dict(self, usage: Optional[Dict[str, Any]]) -> Dict[str, int]:
        usage_dict = usage if isinstance(usage, dict) else {}
        return {
            "prompt_tokens": int(self._to_float(usage_dict.get("prompt_tokens"), 0)),
            "completion_tokens": int(self._to_float(usage_dict.get("completion_tokens"), 0)),
            "total_tokens": int(self._to_float(usage_dict.get("total_tokens"), 0)),
        }

    def _estimate_cost_usd(self, usage: Optional[Dict[str, Any]]) -> float:
        usage_payload = self._usage_dict(usage)
        prompt_cost = (usage_payload["prompt_tokens"] / 1000.0) * self.prompt_cost_per_1k_usd
        completion_cost = (usage_payload["completion_tokens"] / 1000.0) * self.completion_cost_per_1k_usd
        return round(prompt_cost + completion_cost, 8)

    def _append_usage_summary_log(
        self,
        *,
        symbol: str,
        regime: str,
        request_mode: str,
        api_mode: str,
        status: str,
        usage: Optional[Dict[str, Any]],
        error: str = "",
        fallback_used: bool = False,
    ) -> None:
        usage_payload = self._usage_dict(usage)
        now_utc = datetime.now(timezone.utc)
        day_dir = self._resolve_usage_day_dir_utc(now_utc)
        day_dir.mkdir(parents=True, exist_ok=True)
        cost_usd = self._estimate_cost_usd(usage_payload)
        row = {
            "ts_utc": now_utc.isoformat(),
            "date_utc": now_utc.strftime("%Y-%m-%d"),
            "symbol": str(symbol or ""),
            "regime": str(regime or ""),
            "request_mode": str(request_mode or ""),
            "model": str(self.model or ""),
            "api_mode": str(api_mode or ""),
            "status": str(status or ""),
            "prompt_tokens": usage_payload["prompt_tokens"],
            "completion_tokens": usage_payload["completion_tokens"],
            "total_tokens": usage_payload["total_tokens"],
            "pricing_basis": str(self.pricing_basis or ""),
            "prompt_cost_per_1k_usd": round(self.prompt_cost_per_1k_usd, 8),
            "completion_cost_per_1k_usd": round(self.completion_cost_per_1k_usd, 8),
            "estimated_cost_usd": cost_usd,
            "fallback_used": bool(fallback_used),
            "error": str(error or ""),
        }

        jsonl_path = self._resolve_usage_jsonl_path_utc(now_utc)
        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

        csv_path = self._resolve_usage_csv_path_utc(now_utc)
        headers = [
            "ts_utc",
            "date_utc",
            "symbol",
            "regime",
            "request_mode",
            "model",
            "api_mode",
            "status",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "pricing_basis",
            "prompt_cost_per_1k_usd",
            "completion_cost_per_1k_usd",
            "estimated_cost_usd",
            "fallback_used",
            "error",
        ]
        file_exists = csv_path.exists() and csv_path.stat().st_size > 0
        with open(csv_path, "a", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)

    def _create_fallback_response(
        self,
        context: Dict[str, Any],
        reason: str,
        *,
        usage: Optional[Dict[str, Any]] = None,
        api_mode: str = "fallback",
    ) -> AIWeightResponse:
        """创建降级响应"""
        regime = context.get("regime", "TREND")
        weights = self._get_default_weights(regime)
        usage_payload = self._usage_dict(usage)
        
        return AIWeightResponse(
            version="weight-router-v1",
            symbol=str(context.get("symbol", "")),
            timestamp_utc=context.get("timestamp_utc", datetime.now(timezone.utc).isoformat()),
            regime_view={
                "name": regime,
                "bias": str(context.get("ema_bias", "FLAT")),
                "notes": f"fallback:{reason}",
            },
            risk_flags={
                "trap": context.get("trap_confirmed", False),
                "phantom": self._to_float(context.get("phantom_score"), 0) > 0.5,
                "wide_spread": self._to_float(context.get("spread_z"), 0) > 2.0,
                "data_stale": reason.startswith("data_stale") or reason.startswith("missing"),
            },
            weights=weights,
            confidence=0.25,
            reasoning_bullets=[f"降级原因:{reason}"],
            fallback_used=True,
            api_mode=api_mode,
            prompt_tokens=usage_payload["prompt_tokens"],
            completion_tokens=usage_payload["completion_tokens"],
            total_tokens=usage_payload["total_tokens"],
            error=reason,
        )
    
    def _validate_response(self, response_text: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """
        校验 AI 响应 (增强JSON严格性)
        
        返回: (is_valid, parsed_json, error_message)
        """
        # 1. 尝试解析 JSON
        try:
            # 清理可能的 markdown 标记
            cleaned = response_text.strip()
            if cleaned.startswith("```"):
                # 移除 markdown 代码块
                lines = cleaned.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                cleaned = "\n".join(lines)
            
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as e:
            return False, None, f"json_parse_error:{str(e)}"
        
        if not isinstance(parsed, dict):
            return False, None, "response_not_dict"

        missing_top_level = [k for k in ALLOWED_TOP_LEVEL_KEYS if k not in parsed]
        if missing_top_level:
            return False, parsed, f"missing_top_level_keys:{','.join(sorted(missing_top_level))}"

        extra_top_level = [k for k in parsed.keys() if k not in ALLOWED_TOP_LEVEL_KEYS]
        if extra_top_level:
            return False, parsed, f"extra_top_level_keys:{','.join(sorted(extra_top_level))}"
        
        # 2. 检查禁词 (严格)
        response_lower = response_text.lower()
        for word in FORBIDDEN_WORDS:
            if word.lower() in response_lower:
                return False, parsed, f"forbidden_word:{word}"
        
        # 3. 检查必需字段 (严格)
        weights = parsed.get("weights")
        if not isinstance(weights, dict):
            return False, parsed, "missing_weights"
        
        # 4. 检查权重键 (严格)
        missing_keys = [k for k in REQUIRED_WEIGHT_KEYS if k not in weights]
        if missing_keys:
            return False, parsed, f"missing_weight_keys:{','.join(missing_keys)}"
        extra_weight_keys = [k for k in weights.keys() if k not in REQUIRED_WEIGHT_KEYS]
        if extra_weight_keys:
            return False, parsed, f"extra_weight_keys:{','.join(sorted(extra_weight_keys))}"

        # 5. 检查权重值范围 (严格 + NaN/Inf检查)
        for k, v in weights.items():
            if not isinstance(v, (int, float)):
                return False, parsed, f"weight_not_number:{k}"
            # 检查NaN和Inf
            if v != v or v == float("inf") or v == float("-inf"):
                return False, parsed, f"weight_invalid:{k}={v}"
            if v < 0 or v > 1:
                return False, parsed, f"weight_out_of_range:{k}={v}"
        
        # 6. 检查权重和 (严格,允许1e-3误差)
        weight_sum = sum(weights.values())
        if abs(weight_sum - 1.0) > 0.001:
            # 尝试归一化
            if weight_sum > 0:
                parsed["weights"] = {k: v / weight_sum for k, v in weights.items()}
            else:
                return False, parsed, f"weight_sum_invalid:{weight_sum}"

        regime_view = parsed.get("regime_view")
        if not isinstance(regime_view, dict):
            return False, parsed, "regime_view_not_dict"
        missing_regime_view_keys = [k for k in REQUIRED_REGIME_VIEW_KEYS if k not in regime_view]
        if missing_regime_view_keys:
            return False, parsed, f"missing_regime_view_keys:{','.join(sorted(missing_regime_view_keys))}"
        extra_regime_view_keys = [k for k in regime_view.keys() if k not in REQUIRED_REGIME_VIEW_KEYS]
        if extra_regime_view_keys:
            return False, parsed, f"extra_regime_view_keys:{','.join(sorted(extra_regime_view_keys))}"
        for key in REQUIRED_REGIME_VIEW_KEYS:
            if not isinstance(regime_view.get(key), str):
                return False, parsed, f"regime_view_not_string:{key}"

        risk_flags = parsed.get("risk_flags")
        if not isinstance(risk_flags, dict):
            return False, parsed, "risk_flags_not_dict"
        missing_risk_flag_keys = [k for k in REQUIRED_RISK_FLAG_KEYS if k not in risk_flags]
        if missing_risk_flag_keys:
            return False, parsed, f"missing_risk_flag_keys:{','.join(sorted(missing_risk_flag_keys))}"
        extra_risk_flag_keys = [k for k in risk_flags.keys() if k not in REQUIRED_RISK_FLAG_KEYS]
        if extra_risk_flag_keys:
            return False, parsed, f"extra_risk_flag_keys:{','.join(sorted(extra_risk_flag_keys))}"
        for key in REQUIRED_RISK_FLAG_KEYS:
            if not isinstance(risk_flags.get(key), bool):
                return False, parsed, f"risk_flag_not_bool:{key}"

        bullets = parsed.get("reasoning_bullets")
        if not isinstance(bullets, list):
            return False, parsed, "reasoning_bullets_not_list"
        if any(not isinstance(item, str) for item in bullets):
            return False, parsed, "reasoning_bullets_item_not_string"

        # 7. 检查 confidence (严格)
        confidence = parsed.get("confidence")
        if confidence is None:
            return False, parsed, "confidence_missing"
        if not isinstance(confidence, (int, float)):
            return False, parsed, "confidence_not_number"
        if confidence != confidence or confidence == float("inf") or confidence == float("-inf"):
            return False, parsed, "confidence_invalid"
        if confidence < 0 or confidence > 1:
            return False, parsed, f"confidence_out_of_range:{confidence}"
        
        if not isinstance(parsed.get("version"), str):
            return False, parsed, "version_not_string"
        if parsed.get("version") != "weight-router-v1":
            return False, parsed, f"version_invalid:{parsed.get('version')}"

        parsed["reasoning_bullets"] = [str(x)[:20] for x in bullets[:3]]

        # 8. 最终验证: 确保所有字段类型正确
        if not isinstance(parsed.get("fallback_used"), bool):
            return False, parsed, "fallback_used_not_bool"
        
        return True, parsed, ""
    
    def _build_legacy_payload(self, user_prompt: str) -> Dict[str, Any]:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.0,
            "max_tokens": 180,
            "response_format": {"type": "json_object"},
        }

    def _build_strict_payload(self, user_prompt: str) -> Dict[str, Any]:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.0,
            "max_tokens": 180,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": STRICT_TOOL_NAME,
                        "description": "Return only the strict weight router payload for local execution.",
                        "strict": True,
                        "parameters": STRICT_RESPONSE_SCHEMA,
                    },
                }
            ],
            "tool_choice": {
                "type": "function",
                "function": {"name": STRICT_TOOL_NAME},
            },
        }

    def _extract_usage(self, data: Dict[str, Any]) -> Dict[str, int]:
        usage = data.get("usage", {}) if isinstance(data, dict) else {}
        return self._usage_dict(usage if isinstance(usage, dict) else None)

    def _extract_response_content(
        self,
        data: Dict[str, Any],
        *,
        strict_schema: bool,
    ) -> Tuple[bool, str, str]:
        choices = data.get("choices", [])
        if not isinstance(choices, list) or not choices:
            return False, "", "empty_response"
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message", {}) if isinstance(first, dict) else {}
        if not isinstance(message, dict):
            return False, "", "message_missing"

        if strict_schema:
            tool_calls = message.get("tool_calls", [])
            if not isinstance(tool_calls, list) or not tool_calls:
                return False, "", "missing_tool_call"
            for item in tool_calls:
                if not isinstance(item, dict):
                    continue
                fn = item.get("function", {})
                if not isinstance(fn, dict):
                    continue
                if str(fn.get("name") or "") != STRICT_TOOL_NAME:
                    continue
                arguments = fn.get("arguments")
                if isinstance(arguments, str) and arguments.strip():
                    return True, arguments, ""
            return False, "", "tool_arguments_missing"

        content = message.get("content", "")
        if isinstance(content, str) and content.strip():
            return True, content, ""
        return False, "", "empty_response"

    def _call_api_once(
        self,
        *,
        http: Any,
        headers: Dict[str, str],
        api_url: str,
        payload: Dict[str, Any],
        strict_schema: bool,
        api_mode: str,
    ) -> Tuple[bool, str, Dict[str, int], str, str, Dict[str, Any]]:
        last_usage = self._usage_dict(None)
        for attempt in range(self.max_retries):
            try:
                response = http.post(
                    api_url,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout,
                )

                if response.status_code == 200:
                    data = response.json()
                    last_usage = self._extract_usage(data if isinstance(data, dict) else {})
                    ok, content, parse_error = self._extract_response_content(
                        data if isinstance(data, dict) else {},
                        strict_schema=strict_schema,
                    )
                    if ok:
                        self._stats["api_calls"] += 1
                        return True, content, last_usage, "", api_mode, {
                            "api_mode": api_mode,
                            "status": "success",
                            "usage": dict(last_usage),
                            "error": "",
                        }
                    return False, "", last_usage, parse_error, api_mode, {
                        "api_mode": api_mode,
                        "status": "response_error",
                        "usage": dict(last_usage),
                        "error": parse_error,
                    }

                if response.status_code == 429:
                    time.sleep(1 + attempt)
                    continue

                error_text = f"http_error:{response.status_code}"
                try:
                    body = response.json()
                    if isinstance(body, dict):
                        msg = body.get("error", {}).get("message") if isinstance(body.get("error"), dict) else body.get("message")
                        if msg:
                            error_text = f"{error_text}:{msg}"
                except Exception:
                    pass
                return False, "", last_usage, error_text, api_mode, {
                    "api_mode": api_mode,
                    "status": "http_error",
                    "usage": dict(last_usage),
                    "error": error_text,
                }

            except Exception as e:
                logger.warning(f"DeepSeek API call failed ({api_mode}, attempt {attempt + 1}): {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(0.5 + attempt)
                else:
                    final_error = f"exception:{str(e)}"
                    return False, "", last_usage, final_error, api_mode, {
                        "api_mode": api_mode,
                        "status": "exception",
                        "usage": dict(last_usage),
                        "error": final_error,
                    }

        final_error = "max_retries_exceeded"
        return False, "", last_usage, final_error, api_mode, {
            "api_mode": api_mode,
            "status": "retry_exhausted",
            "usage": dict(last_usage),
            "error": final_error,
        }

    def _call_api(self, user_prompt: str) -> Tuple[bool, str, Dict[str, int], str, str, List[Dict[str, Any]]]:
        """
        调用 DeepSeek API
        
        返回: (success, response_text, usage, error_message, api_mode)
        """
        http = self._get_http_client()
        if http is None:
            return False, "", self._usage_dict(None), "http_client_not_available", "disabled", []
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        call_records: List[Dict[str, Any]] = []

        if self.strict_schema_enabled:
            strict_result = self._call_api_once(
                http=http,
                headers=headers,
                api_url=self.strict_api_url,
                payload=self._build_strict_payload(user_prompt),
                strict_schema=True,
                api_mode="strict_tool_schema",
            )
            call_records.append(strict_result[5])
            if strict_result[0]:
                return strict_result[0], strict_result[1], strict_result[2], strict_result[3], strict_result[4], call_records
            if not self.strict_schema_fallback_enabled:
                return strict_result[0], strict_result[1], strict_result[2], strict_result[3], strict_result[4], call_records
            logger.warning("DeepSeek strict schema failed, fallback to json_object: %s", strict_result[3])

        legacy_result = self._call_api_once(
            http=http,
            headers=headers,
            api_url=self.api_url,
            payload=self._build_legacy_payload(user_prompt),
            strict_schema=False,
            api_mode="json_object_fallback",
        )
        call_records.append(legacy_result[5])
        return legacy_result[0], legacy_result[1], legacy_result[2], legacy_result[3], legacy_result[4], call_records
    
    def get_weights(
        self,
        symbol: str,
        regime: str,
        market_flow_context: Dict[str, Any],
        quantile_context: Optional[Dict[str, Any]] = None,
        request_mode: str = "generic",
    ) -> AIWeightResponse:
        """
        获取动态权重
        
        Args:
            symbol: 交易标的
            regime: 市场状态
            market_flow_context: 市场资金流上下文
            quantile_context: 分位数上下文
        
        Returns:
            AIWeightResponse: 权重响应
        """
        self._stats["total_requests"] += 1
        request_mode_norm = str(request_mode or "generic").strip().lower()
        
        # 构建上下文
        context = self._build_context(
            symbol,
            regime,
            market_flow_context,
            quantile_context,
            request_mode=request_mode_norm,
        )
        context["request_mode"] = request_mode_norm
        request_payload = self._build_request_payload(context)
        
        # 检查是否应该直接降级
        should_fallback, fallback_reason = self._should_fallback(context)
        if should_fallback:
            self._stats["fallbacks"] += 1
            _safe_print(
                "🤖 AI权重跳过: "
                f"{json.dumps(self._build_sample_quality_log_payload(context, fallback_reason), ensure_ascii=False, separators=(',', ':'))}"
            )
            return self._create_fallback_response(context, fallback_reason, api_mode="precheck_fallback")
        
        # 检查缓存
        cache_key = self._make_structured_cache_key(context)
        cached = self._get_cached(cache_key)
        if cached is not None:
            _safe_print(
                "🤖 AI权重缓存命中: "
                f"{json.dumps(self._build_response_log_payload(cached), ensure_ascii=False, separators=(',', ':'))}"
            )
            return cached
        
        # 如果未启用 AI，直接返回默认权重
        if not self.enabled:
            self._stats["fallbacks"] += 1
            _safe_print(
                "🤖 AI权重禁用: "
                f"{json.dumps({'symbol': symbol, 'regime': regime, 'reason': 'ai_disabled'}, ensure_ascii=False, separators=(',', ':'))}"
            )
            return self._create_fallback_response(context, "ai_disabled", api_mode="disabled")
        
        # 调用 AI
        user_prompt = self._build_user_prompt(context)
        _safe_print(
            "🤖 AI权重请求: "
            f"{json.dumps(request_payload, ensure_ascii=False, separators=(',', ':'))}"
        )
        success, response_text, usage, error, api_mode, call_records = self._call_api(user_prompt)
        for record in call_records:
            if not isinstance(record, dict):
                continue
            record_mode = str(record.get("api_mode", ""))
            self._append_usage_summary_log(
                symbol=symbol,
                regime=regime,
                request_mode=request_mode_norm,
                api_mode=record_mode,
                status=str(record.get("status", "")),
                usage=record.get("usage"),
                error=str(record.get("error", "")),
                fallback_used=(len(call_records) > 1) or (record_mode == "json_object_fallback") or (not success),
            )
        
        if not success:
            self._stats["errors"] += 1
            self._stats["fallbacks"] += 1
            _safe_print(
                "🤖 AI权重失败: "
                f"{json.dumps({'symbol': symbol, 'regime': regime, 'api_mode': api_mode, 'usage': self._usage_dict(usage), 'error': error}, ensure_ascii=False, separators=(',', ':'))}"
            )
            return self._create_fallback_response(
                context,
                f"api_error:{error}",
                usage=usage,
                api_mode=api_mode,
            )
        
        # 校验响应
        is_valid, parsed, validation_error = self._validate_response(response_text)
        
        if not is_valid:
            self._stats["errors"] += 1
            self._stats["fallbacks"] += 1
            logger.warning(f"AI response validation failed: {validation_error}")
            _safe_print(
                "🤖 AI权重校验失败: "
                f"{json.dumps({'symbol': symbol, 'regime': regime, 'api_mode': api_mode, 'usage': self._usage_dict(usage), 'error': validation_error}, ensure_ascii=False, separators=(',', ':'))}"
            )
            return self._create_fallback_response(
                context,
                f"validation_error:{validation_error}",
                usage=usage,
                api_mode=api_mode,
            )
        
        # parsed 已验证通过，必定是 dict
        parsed_dict: Dict[str, Any] = parsed if isinstance(parsed, dict) else {}
        
        # 构建成功响应
        response = AIWeightResponse(
            version=parsed_dict.get("version", "weight-router-v1"),
            symbol=symbol,
            timestamp_utc=context.get("timestamp_utc", datetime.now(timezone.utc).isoformat()),
            regime_view=parsed_dict.get("regime_view", {}),
            risk_flags=parsed_dict.get("risk_flags", {}),
            weights=parsed_dict.get("weights", {}),
            confidence=float(parsed_dict.get("confidence", 0.5)),
            reasoning_bullets=parsed_dict.get("reasoning_bullets", []),
            fallback_used=bool(parsed_dict.get("fallback_used", False)),
            api_mode=api_mode,
            prompt_tokens=int(self._usage_dict(usage).get("prompt_tokens", 0)),
            completion_tokens=int(self._usage_dict(usage).get("completion_tokens", 0)),
            total_tokens=int(self._usage_dict(usage).get("total_tokens", 0)),
            raw_response=response_text,
        )
        _safe_print(
            "🤖 AI权重决策: "
            f"{json.dumps(self._build_response_log_payload(response), ensure_ascii=False, separators=(',', ':'))}"
        )
        
        # 缓存结果
        self._set_cache(
            cache_key,
            response,
            ttl_seconds=self._get_cache_ttl_for_context(context),
        )
        
        return response
    
    def _build_context(
        self,
        symbol: str,
        regime: str,
        market_flow_context: Dict[str, Any],
        quantile_context: Optional[Dict[str, Any]] = None,
        request_mode: str = "generic",
    ) -> Dict[str, Any]:
        """构建完整上下文（V3.0 增强：语义正确的输入）"""
        request_profile = self._request_profile(request_mode)
        timeframes = market_flow_context.get("timeframes", {})
        tf_15m = timeframes.get("15m", {}) if isinstance(timeframes, dict) else {}
        tf_5m = timeframes.get("5m", {}) if isinstance(timeframes, dict) else {}
        tf_3m = timeframes.get("3m", {}) if isinstance(timeframes, dict) else {}
        
        # 优先从结构化输出取值
        ms = market_flow_context.get("microstructure_features", {})
        if not isinstance(ms, dict):
            ms = {}
        ff = market_flow_context.get("fund_flow_features", {})
        if not isinstance(ff, dict):
            ff = {}
        
        # 统一字段（优先 fund_flow_features，否则用旧字段兜底）
        cvd = self._to_float(ff.get("cvd"), self._to_float(market_flow_context.get("cvd_ratio"), 0.0))
        cvd_mom = self._to_float(ff.get("cvd_momentum"), self._to_float(market_flow_context.get("cvd_momentum"), 0.0))
        oi_delta = self._to_float(ff.get("oi_delta"), self._to_float(market_flow_context.get("oi_delta_ratio"), 0.0))
        funding = self._to_float(ff.get("funding"), self._to_float(market_flow_context.get("funding_rate"), 0.0))
        depth = self._to_float(ff.get("depth_ratio"), self._to_float(market_flow_context.get("depth_ratio"), 1.0))
        imbalance = self._to_float(market_flow_context.get("imbalance"), 0.0)
        liq_delta = self._to_float(ff.get("liquidity_delta"), self._to_float(market_flow_context.get("liquidity_delta_norm"), 0.0))
        
        # 微结构指标（优先 microstructure_features）
        trap_score = self._to_float(ms.get("trap_score"), self._to_float(tf_5m.get("trap_last"), 
                               self._to_float(market_flow_context.get("trap_score"), 0.0)))
        phantom_score = self._to_float(ms.get("phantom_score"), self._to_float(tf_5m.get("phantom_mean"),
                                 self._to_float(market_flow_context.get("phantom"), 0.0)))
        spread_bps = self._to_float(ms.get("spread_bps"), self._to_float(tf_5m.get("spread_bps_last"), 0.0))
        
        # 获取 15m 历史用于 z-score 计算
        hist15 = tf_15m.get("history", [])
        if not isinstance(hist15, list):
            hist15 = []
        history_bars = len(hist15)
        min_history_bars = max(3, int(request_profile.get("min_history_bars", 4)))
        preferred_zscore_bars = max(min_history_bars, int(request_profile.get("preferred_zscore_bars", 20)))
        
        # z-score 计算函数
        def _zscore_from_hist(key: str, x: float, hist: list, eps: float = 1e-9) -> float:
            if not isinstance(hist, list) or len(hist) < min_history_bars:
                return 0.0
            window_n = min(len(hist), preferred_zscore_bars)
            vals = []
            for row in hist[-window_n:]:
                if isinstance(row, dict):
                    raw_v = row.get(key)
                    if raw_v is not None:
                        try:
                            vals.append(float(raw_v))
                        except (TypeError, ValueError):
                            pass
            if len(vals) < min_history_bars:
                return 0.0
            mu = sum(vals) / len(vals)
            var = sum((v - mu) ** 2 for v in vals) / max(1, (len(vals) - 1))
            sd = (var ** 0.5) + eps
            z = (x - mu) / sd
            # winsor clip，防止极端值
            return float(max(-5.0, min(5.0, z)))
        
        # 计算 z-scores
        cvd_z = _zscore_from_hist("cvd", cvd, hist15)
        cvd_mom_z = _zscore_from_hist("cvd_momentum", cvd_mom, hist15)
        oi_delta_z = _zscore_from_hist("oi_delta", oi_delta, hist15)
        funding_z = _zscore_from_hist("funding", funding, hist15)
        depth_ratio_z = _zscore_from_hist("depth_ratio", depth, hist15)
        imbalance_z = _zscore_from_hist("imbalance", imbalance, hist15)
        liquidity_delta_z = _zscore_from_hist("liquidity_delta", liq_delta, hist15)
        micro_delta = self._to_float(ff.get("micro_delta"), self._to_float(tf_5m.get("micro_delta_last"), 0.0))
        micro_delta_z = _zscore_from_hist("micro_delta", micro_delta, hist15)
        microprice_bias = self._to_float(
            ms.get("microprice_bias"),
            self._to_float(ms.get("microprice_delta"), self._to_float(market_flow_context.get("microprice_bias"), 0.0)),
        )
        ret_3m = self._to_float(tf_3m.get("ret_period"), 0.0)
        micro_confirm_3m_long = micro_delta > 0 and microprice_bias > 0
        micro_confirm_3m_short = micro_delta < 0 and microprice_bias < 0
        capture_confirm_3m_long = ret_3m > 0 and micro_confirm_3m_long
        capture_confirm_3m_short = ret_3m < 0 and micro_confirm_3m_short
        if capture_confirm_3m_long and not capture_confirm_3m_short:
            capture_confirm_3m_side = "LONG"
        elif capture_confirm_3m_short and not capture_confirm_3m_long:
            capture_confirm_3m_side = "SHORT"
        else:
            capture_confirm_3m_side = "NONE"
        
        # spread_z：优先用 microstructure_features 输出，否则从历史计算
        spread_z = self._to_float(ms.get("spread_z"), 0.0)
        if spread_z == 0.0 and spread_bps > 0:
            # fallback：用 5m history 算
            hist_spread = tf_5m.get("history_spread_bps", [])
            if isinstance(hist_spread, list) and len(hist_spread) >= min_history_bars:
                spread_z = _zscore_from_hist("__spread__", spread_bps, 
                             [{"__spread__": v} for v in hist_spread])
        
        # 从 quantile_context 获取额外信息
        trap_confirmed = False
        if quantile_context:
            trap_guard = self._to_float(quantile_context.get("trap_guard"), 0.7)
            trap_confirmed = trap_score > trap_guard

        # 额外：极端缺口情况（spread_z 很高但 trap_guard 未提供时，也可强制 trap_confirmed）
        # 这不是 hard gate，只是给 DeepSeek 一个“风险提示”语义
        if (not trap_confirmed) and spread_z >= 3.0 and trap_score >= 0.8:
            trap_confirmed = True
        
        # flow_confirm：必须引入价格方向
        def _sgn(x: float) -> int:
            return 1 if x > 0 else (-1 if x < 0 else 0)
        
        ret_period = self._to_float(ff.get("ret_period"), self._to_float(tf_15m.get("ret_period"), 0.0))
        cvd_s = _sgn(cvd)
        oi_s = _sgn(oi_delta)
        ret_s = _sgn(ret_period)
        
        # 资金一致性：CVD、OI、价格方向至少"两两一致"，且不能全为0
        flow_confirm = (ret_s != 0) and (
            (cvd_s == ret_s and oi_s == ret_s) or
            (cvd_s == ret_s and oi_s == 0) or
            (oi_s == ret_s and cvd_s == 0)
        )
        
        # consistency_3bars：从 15m 历史计算
        def _consistency_3bars(hist: list) -> int:
            if not isinstance(hist, list) or len(hist) < 3:
                return 0
            cnt = 0
            for row in hist[-3:]:
                if not isinstance(row, dict):
                    continue
                r = _sgn(self._to_float(row.get("ret_period"), 0.0))
                c = _sgn(self._to_float(row.get("cvd"), 0.0))
                if r != 0 and c != 0 and r == c:
                    cnt += 1
            return cnt
        
        consistency_3bars = _consistency_3bars(hist15)
        
        # EMA 偏向
        ema_fast = self._to_float(tf_15m.get("ema_fast"), 0)
        ema_slow = self._to_float(tf_15m.get("ema_slow"), 0)
        if ema_fast > ema_slow * 1.001:
            ema_bias = "UP"
        elif ema_fast < ema_slow * 0.999:
            ema_bias = "DOWN"
        else:
            ema_bias = "FLAT"
        
        # 趋势强度
        adx = self._to_float(tf_15m.get("adx"), 0)
        trend_strength = min(1.0, max(0.0, (adx - 15) / 25)) if adx > 15 else 0
        
        # risk_flags 自动推断
        wide_spread = spread_z >= 2.5
        trap_flag = bool(trap_confirmed) or (trap_score >= 0.8)
        phantom_flag = phantom_score >= 0.8

        # 数据质量检查
        missing_fields: List[str] = []
        # 重要：0.0 不等于缺失（很多时候真实值就是0）
        # 缺失判定应基于“字段不存在/None/样本不足”而不是数值为0
        # 这里用最小集合：关键历史不足也算“不可用”
        if not isinstance(hist15, list) or history_bars < min_history_bars:
            missing_fields.append("hist15_insufficient")
        # 若 fund_flow_features/microstructure_features 块缺失关键键，才算缺失
        if "cvd" not in ff and "cvd_ratio" not in market_flow_context:
            missing_fields.append("cvd")
        if "oi_delta" not in ff and "oi_delta_ratio" not in market_flow_context:
            missing_fields.append("oi_delta")
        if "adx" not in tf_15m:
            missing_fields.append("adx")
        
        # stale_seconds：用当前时间与 tf_15m close 时间差
        stale_seconds = 0
        freshness_timeframe = str(request_profile.get("freshness_timeframe", "15m"))
        freshness_ctx = tf_5m if freshness_timeframe == "5m" else tf_15m
        freshness_ts = None
        if isinstance(freshness_ctx, dict):
            freshness_ts = freshness_ctx.get("bucket_ts") or freshness_ctx.get("timestamp_close_utc")
        if freshness_ts:
            try:
                if isinstance(freshness_ts, str):
                    ts_close = datetime.fromisoformat(freshness_ts.replace("Z", "+00:00"))
                elif isinstance(freshness_ts, (int, float)):
                    ts_close = datetime.fromtimestamp(freshness_ts, tz=timezone.utc)
                else:
                    ts_close = freshness_ts
                stale_seconds = int((datetime.now(timezone.utc) - ts_close).total_seconds())
            except Exception:
                pass

        # 防御：如果解析失败/未来时间戳导致负数，归零
        if stale_seconds < 0:
            stale_seconds = 0

        # sample_ok：数据新鲜 + 关键字段存在 + 历史足够做 zscore
        # 注意：adx==0 可能是指标尚未形成，不强制失败；但缺失 adx 键则失败
        sample_ok = (len(missing_fields) == 0) and (stale_seconds <= 90)
        cold_start = history_bars < 12
        
        # 极端波动冷却
        atr_pct = self._to_float(tf_15m.get("atr_pct"), 0)
        extreme_vol_cooldown = atr_pct > 0.02

        ma10_bias_raw = market_flow_context.get("ma10_1h_bias")
        ma10_bias_num = int(self._to_float(ma10_bias_raw, 0))
        if ma10_bias_num > 0:
            ma10_bias_1h = "UP"
        elif ma10_bias_num < 0:
            ma10_bias_1h = "DOWN"
        else:
            ma10_bias_1h = "FLAT"
        macd_cross_5m = str(
            tf_5m.get("macd_cross", market_flow_context.get("macd_5m_cross", "NONE"))
        ).upper()
        macd_zone_5m = str(
            tf_5m.get("macd_zone", market_flow_context.get("macd_5m_zone", "NEAR_ZERO"))
        ).upper()
        macd_hist_expand_5m = bool(
            tf_5m.get(
                "macd_5m_hist_expand",
                market_flow_context.get(
                    "macd_5m_hist_expand",
                    bool(
                        tf_5m.get("macd_5m_hist_expand_up", False)
                        or tf_5m.get("macd_5m_hist_expand_down", False)
                    ),
                ),
            )
        )
        kdj_cross_5m = str(
            tf_5m.get("kdj_cross", market_flow_context.get("kdj_cross", "NONE"))
        ).upper()
        kdj_zone_5m = str(
            tf_5m.get("kdj_zone", market_flow_context.get("kdj_zone", "MID"))
        ).upper()

        return {
            "symbol": symbol,
            "regime": regime,
            "regime_name": regime,  # prompt 兼容
            "timestamp_utc": (
                freshness_ctx.get("timestamp_close_utc")
                if isinstance(freshness_ctx, dict) and freshness_ctx.get("timestamp_close_utc")
                else tf_15m.get("timestamp_close_utc")
                or datetime.now(timezone.utc).isoformat()
            ),
            "trend_strength": trend_strength,
            "adx": adx,
            "atr_pct": atr_pct,
            "ema_bias": ema_bias,
            "flow_confirm": flow_confirm,
            "consistency_3bars": consistency_3bars,
            "history_bars": history_bars,
            "history_min_bars": min_history_bars,
            "cold_start": cold_start,
            "freshness_timeframe": freshness_timeframe,
            # 真正的 z-scores
            "cvd_z": cvd_z,
            "cvd_mom_z": cvd_mom_z,
            "oi_delta_z": oi_delta_z,
            "funding_z": funding_z,
            "depth_ratio_z": depth_ratio_z,
            "imbalance_z": imbalance_z,
            "liquidity_delta_z": liquidity_delta_z,
            "micro_delta_z": micro_delta_z,
            # 微结构风险
            "spread_z": spread_z,
            "spread_bps": spread_bps,
            "trap_score": trap_score,
            "phantom_score": phantom_score,
            "trap_confirmed": trap_confirmed,
            "extreme_vol_cooldown": extreme_vol_cooldown,
            # risk_flags（自动推断）
            "wide_spread": wide_spread,
            "trap_flag": trap_flag,
            "phantom_flag": phantom_flag,
            # 额外：按 prompt 输出风格提供 risk_flags dict（不破坏你已有平铺字段）
            "risk_flags": {
                "trap": bool(trap_flag),
                "phantom": bool(phantom_flag),
                "wide_spread": bool(wide_spread),
                "data_stale": bool(stale_seconds > 30),
            },
            "tech_context": {
                "ma10_bias_1h": ma10_bias_1h,
                "macd_cross_5m": macd_cross_5m,
                "macd_zone_5m": macd_zone_5m,
                "macd_hist_expand_5m": bool(macd_hist_expand_5m),
                "kdj_cross_5m": kdj_cross_5m,
                "kdj_zone_5m": kdj_zone_5m,
            },
            "ret_3m": ret_3m,
            "micro_confirm_3m_long": bool(micro_confirm_3m_long),
            "micro_confirm_3m_short": bool(micro_confirm_3m_short),
            "capture_confirm_3m_long": bool(capture_confirm_3m_long),
            "capture_confirm_3m_short": bool(capture_confirm_3m_short),
            "capture_confirm_3m_side": capture_confirm_3m_side,
            # 数据质量（不再写死）
            "missing_fields": missing_fields,
            "stale_seconds": stale_seconds,
            "sample_ok": sample_ok,
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        total = self._stats["total_requests"]
        hits = self._stats["cache_hits"]
        fallbacks = self._stats["fallbacks"]
        
        return {
            **self._stats,
            "cache_hit_rate": hits / total if total > 0 else 0,
            "fallback_rate": fallbacks / total if total > 0 else 0,
            "cache_size": len(self._cache),
            "enabled": self.enabled,
        }
    
    def clear_cache(self) -> int:
        """清空缓存"""
        count = len(self._cache)
        self._cache.clear()
        return count


# 导出
__all__ = [
    "AIWeightResponse",
    "DefaultWeights",
    "DeepSeekAIService",
    "SYSTEM_PROMPT",
    "USER_PROMPT_TEMPLATE",
]
