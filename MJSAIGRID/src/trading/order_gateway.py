import requests  # type: ignore

import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional


class OrderGateway:
    """
    负责：Binance 订单指令的格式化、参数映射、终端选择及实际发送。
    """

    def __init__(self, broker: Any) -> None:
        self.broker = broker
        # 🔒 L1: symbol + side 时间锁（20秒内禁止重复 OPEN）
        self._open_locks: Dict[str, float] = {}

    def _is_fatal_auth_error(self, err: Any) -> bool:
        """检测致命权限错误（401 / -2015 / -2014）- 不可重试"""
        if isinstance(err, dict):
            return err.get("code") in (-2015, -2014)
        msg = str(err)
        checks = ["401", "Unauthorized", "-2015", "-2014"]
        return any(s in msg for s in checks)

    def _log_order_reject(
        self,
        symbol: str,
        side: str,
        params: Dict[str, Any],
        error: Any,
    ) -> None:
        """记录订单拒绝告警到日志文件（可选）"""
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            now = datetime.now()
            month = now.strftime("%Y-%m")
            date = now.strftime("%Y-%m-%d")
            logs_dir = os.path.join(project_root, "logs", month, date)
            os.makedirs(logs_dir, exist_ok=True)
            path = os.path.join(logs_dir, "order_rejects.log")
            ts = now.isoformat()
            line = f"{ts} symbol={symbol} side={side} params={params} error={error}\n"
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass

    def has_open_position(self, symbol: str, side: Optional[str] = None) -> bool:
        """🔥 L2: 统一的「是否已有仓位」判断（支持方向 LONG/SHORT/BOTH 和 BUY/SELL）

        接受的 side 可以是 'LONG'/'SHORT' 或者 'BUY'/'SELL'，也可以为 None (等同于 BOTH)。
        """
        if side:
            s = side.upper()
            if s == "BUY":
                query_side = "LONG"
            elif s == "SELL":
                query_side = "SHORT"
            elif s in ("LONG", "SHORT"):
                query_side = s
            else:
                query_side = "BOTH"
        else:
            query_side = "BOTH"

        pos = self.broker.position.get_position(symbol, side=query_side)
        if not pos:
            return False
        try:
            return abs(float(pos.get("positionAmt", 0))) > 0
        except Exception:
            return False

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return default

    def _resolve_close_leg_state(
        self,
        symbol: str,
        side: str,
        final_params: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        在平仓请求下，解析目标腿并回读交易所实时仓位，避免 reduce-only 在无仓位/错腿时触发 -2022。
        """
        side_up = str(side or "").upper()
        target_side = str(final_params.get("positionSide") or "").upper()
        if target_side not in ("LONG", "SHORT"):
            try:
                if bool(self.broker.get_hedge_mode()):
                    calc = str(self.broker.calculate_position_side(side_up, True) or "").upper()
                    if calc in ("LONG", "SHORT"):
                        target_side = calc
            except Exception:
                target_side = ""
        if target_side not in ("LONG", "SHORT"):
            if side_up == "SELL":
                target_side = "LONG"
            elif side_up == "BUY":
                target_side = "SHORT"

        matched_position: Optional[Dict[str, Any]] = None
        matched_qty = 0.0
        inferred_side = ""

        if target_side in ("LONG", "SHORT"):
            try:
                pos_target = self.broker.position.get_position(symbol, side=target_side)
            except Exception:
                pos_target = None
            amt_target = self._to_float((pos_target or {}).get("positionAmt"), 0.0) if isinstance(pos_target, dict) else 0.0
            if abs(amt_target) > 0:
                matched_position = pos_target
                matched_qty = abs(amt_target)
                inferred_side = "LONG" if amt_target > 0 else "SHORT"

        if matched_qty <= 0:
            try:
                pos_any = self.broker.position.get_position(symbol)
            except Exception:
                pos_any = None
            amt_any = self._to_float((pos_any or {}).get("positionAmt"), 0.0) if isinstance(pos_any, dict) else 0.0
            if abs(amt_any) > 0:
                side_any = "LONG" if amt_any > 0 else "SHORT"
                if target_side in ("LONG", "SHORT") and side_any != target_side:
                    return {
                        "target_side": target_side,
                        "inferred_side": side_any,
                        "quantity": 0.0,
                        "position": pos_any if isinstance(pos_any, dict) else None,
                    }
                matched_position = pos_any if isinstance(pos_any, dict) else None
                matched_qty = abs(amt_any)
                inferred_side = side_any

        return {
            "target_side": target_side,
            "inferred_side": inferred_side,
            "quantity": matched_qty,
            "position": matched_position,
        }

    def place_standard_order(
        self,
        symbol: str,
        side: str,
        params: Dict[str, Any],
        reduce_only: bool = False,
        delay: int = 20,
    ) -> Dict[str, Any]:
        """
        执行标准订单（开仓、平仓）

        🔒 三层防护机制：
        - L1: 时间锁（同symbol+side 20秒内禁止重复）
        - L2: 真实仓位检查（不是openOrders）
        - L3: 失败后再次检查仓位（防止已成交）
        """
        now = time.time()
        lock_key = f"{symbol}:{side}"
        allow_position_increase = bool(
            params.get("_allow_position_increase") or params.get("allow_position_increase")
        )

        # 判断是否为全仓平仓（closePosition）——对平仓不应触发开仓锁/开仓检查
        is_close_position = bool(params.get("closePosition"))

        # 🔒 L1: 时间锁（20秒内禁止重复开仓）
        # 仅在非平仓且非 reduce_only 的情况下生效
        if not reduce_only and not is_close_position and not allow_position_increase:
            last_ts = self._open_locks.get(lock_key)
            if last_ts and now - last_ts < delay:
                msg = "[OPEN BLOCKED] " + symbol + " " + side + " within " + str(delay) + "s lock"
                raise RuntimeError(msg)

        # 计算用于仓位检查的 position side（兼容 BUY/SELL 和 LONG/SHORT）
        s_up = side.upper() if isinstance(side, str) else ""
        if s_up in ("BUY", "LONG"):
            pos_check_side = "LONG"
        elif s_up in ("SELL", "SHORT"):
            pos_check_side = "SHORT"
        else:
            pos_check_side = "BOTH"

        # 🔒 L2: 真实仓位检查（不是openOrders），按方向检查避免重复开仓
        # 对于平仓请求（closePosition）应跳过此检查
        cond_skip_l2 = not reduce_only and not is_close_position and not allow_position_increase
        if cond_skip_l2 and self.has_open_position(symbol, pos_check_side):
            msg = "[OPEN BLOCKED] " + symbol + " already has open position (real check via positionAmt)"
            raise RuntimeError(msg)

        # 记录锁（先锁，防并发）
        if not reduce_only and not is_close_position and not allow_position_increase:
            self._open_locks[lock_key] = now

        final = self._finalize_params(params, side, reduce_only)
        is_close_request = bool(
            reduce_only
            or final.get("closePosition") is True
            or str(final.get("closePosition")).lower() == "true"
        )

        if is_close_request:
            close_state = self._resolve_close_leg_state(symbol=symbol, side=side, final_params=final)
            live_close_qty = self._to_float(close_state.get("quantity"), 0.0)
            if live_close_qty <= 0:
                return {
                    "status": "noop",
                    "message": "no position to close",
                    "symbol": symbol,
                    "side": side,
                    "target_side": close_state.get("target_side"),
                    "live_side": close_state.get("inferred_side"),
                }

            req_qty = self._to_float(final.get("quantity"), 0.0)
            if req_qty > 0 and req_qty > live_close_qty + 1e-12:
                try:
                    adjusted_qty = float(self.broker.format_quantity(symbol, live_close_qty))
                except Exception:
                    adjusted_qty = live_close_qty
                if adjusted_qty <= 0:
                    return {
                        "status": "noop",
                        "message": "close quantity rounded to zero after live sync",
                        "symbol": symbol,
                        "side": side,
                        "target_side": close_state.get("target_side"),
                    }
                final["quantity"] = adjusted_qty

        # 确保下单满足交易所最小名义(notional)要求，避免 -4164 错误
        try:
            qty = final.get("quantity")
            price = final.get("price")
            if qty and (not price or float(price) <= 0):
                # 尝试从行情获取当前价格
                try:
                    ticker = self.broker.request(
                        "GET",
                        f"{self.broker.MARKET_BASE}/fapi/v1/ticker/24hr",
                        params={"symbol": symbol},
                        allow_error=True,
                    )
                    ticker_data = ticker.json() if ticker is not None else {}
                    price = float((ticker_data or {}).get("lastPrice", 0) or 0)
                except Exception:
                    price = None

            if qty and price and float(price) > 0:
                try:
                    qty_now = float(qty)
                    adjusted = float(self.broker.ensure_min_notional_quantity(symbol, qty_now, float(price)))
                    if is_close_request and live_close_qty > 0 and adjusted > live_close_qty + 1e-12:
                        adjusted = float(self.broker.format_quantity(symbol, live_close_qty))
                    if adjusted > qty_now + 1e-12:
                        # 更新最终参数为符合最小名义量的数量
                        final["quantity"] = adjusted
                        print(
                            "[INFO] Adjusted quantity for min_notional: "
                            f"{qty_now} -> {adjusted} (price={price}, "
                            f"notional={qty_now * float(price):.4f}->{adjusted * float(price):.4f})"
                        )
                except Exception:
                    # 容错：如果检查失败，继续按原参数下单（上层会捕获并处理错误）
                    pass
        except Exception:
            pass

        try:
            def _retry_on_min_notional(err_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
                """处理 -4164 最小名义额错误：按交易所规则放大数量并自动重试一次。"""
                if not isinstance(err_data, dict) or err_data.get("code") != -4164:
                    return None
                try:
                    qty_now = float(final.get("quantity") or 0)
                    if qty_now <= 0:
                        return None

                    price = final.get("price")
                    if not price or float(price) <= 0:
                        try:
                            t = self.broker.request(
                                "GET",
                                f"{self.broker.MARKET_BASE}/fapi/v1/ticker/24hr",
                                params={"symbol": symbol},
                                allow_error=True,
                            )
                            t_data = t.json() if t is not None else {}
                            price = float((t_data or {}).get("lastPrice", 0) or 0)
                        except Exception:
                            price = None
                    if not price or float(price) <= 0:
                        return None
                    px = float(price)

                    info = self.broker.get_symbol_info(symbol) or {}
                    min_notional = float(info.get("min_notional", 0) or 0)
                    step_size = float(info.get("step_size", 0) or 0)

                    required_qty = float(self.broker.ensure_min_notional_quantity(symbol, qty_now, px))
                    # 若目标数量未提升，至少抬高一个 step，避免重复 -4164 原地重试。
                    if required_qty <= qty_now + 1e-12 and step_size > 0:
                        required_qty = float(self.broker.format_quantity(symbol, qty_now + step_size))
                    if is_close_request and live_close_qty > 0 and required_qty > live_close_qty + 1e-12:
                        required_qty = float(self.broker.format_quantity(symbol, live_close_qty))
                    if required_qty <= qty_now + 1e-12:
                        return None

                    print(
                        "❗ -4164 最小名义额限制: "
                        f"symbol={symbol} min_notional={min_notional or 'unknown'} "
                        f"price={px} qty={qty_now} -> required_qty~={required_qty}"
                    )

                    final_retry = dict(final)
                    final_retry["quantity"] = required_qty
                    print(
                        "🔁 尝试 -4164 自动重试: "
                        f"quantity -> {required_qty}, target_notional~={required_qty * px:.4f}"
                    )
                    resp2 = self.broker.request(
                        method="POST",
                        url=self._order_endpoint(),
                        params=final_retry,
                        signed=True,
                        allow_error=True,
                        close_request=is_close_request,
                    )
                    try:
                        data2 = resp2.json()
                    except Exception:
                        data2 = {
                            "code": -1,
                            "msg": f"HTTP {getattr(resp2, 'status_code', 'unknown')} {str(getattr(resp2, 'text', '') or '')[:500]}",
                        }
                    if not isinstance(data2, dict):
                        data2 = {"code": -1, "msg": str(data2)}
                    if ("code" in data2 and data2["code"] < 0) or int(getattr(resp2, "status_code", 200) or 200) >= 400:
                        self._log_order_reject(symbol, side, final_retry, data2)
                        return None
                    return data2
                except Exception:
                    # 容错：计算过程中出错，放弃自动重试路径
                    return None

            response = self.broker.request(
                method="POST",
                url=self._order_endpoint(),
                params=final,
                signed=True,
                allow_error=True,
                close_request=is_close_request,
            )
            try:
                data = response.json()
            except Exception:
                data = {
                    "code": -1,
                    "msg": f"HTTP {getattr(response, 'status_code', 'unknown')} {str(getattr(response, 'text', '') or '')[:500]}",
                }
            if not isinstance(data, dict):
                data = {"code": -1, "msg": str(data)}

            # Binance 返回错误
            if ("code" in data and data["code"] < 0) or int(getattr(response, "status_code", 200) or 200) >= 400:
                # 记录订单拒绝（可选告警日志）
                self._log_order_reject(symbol, side, final, data)

                # 🚫 致命权限错误：直接抛出，禁止 retry
                if self._is_fatal_auth_error(data):
                    msg = "[FATAL AUTH ERROR] API key has no futures permission or invalid IP: " + str(data)
                    raise RuntimeError(msg)

                # -1021 时间戳偏差：同步时间后重试一次
                if data.get("code") == -1021:
                    try:
                        self.broker._sync_time_offset(force=True)
                        response_retry = self.broker.request(
                            method="POST",
                            url=self._order_endpoint(),
                            params=final,
                            signed=True,
                            allow_error=True,
                            close_request=is_close_request,
                        )
                        try:
                            data_retry = response_retry.json()
                        except Exception:
                            data_retry = {
                                "code": -1,
                                "msg": f"HTTP {getattr(response_retry, 'status_code', 'unknown')} {str(getattr(response_retry, 'text', '') or '')[:500]}",
                            }
                        if isinstance(data_retry, dict) and (
                            ("code" not in data_retry or data_retry.get("code", 0) >= 0)
                            and int(getattr(response_retry, "status_code", 200) or 200) < 400
                        ):
                            return data_retry
                        if isinstance(data_retry, dict):
                            data = data_retry
                            self._log_order_reject(symbol, side, final, data)
                    except Exception:
                        pass

                retry_ok = _retry_on_min_notional(data)
                if retry_ok is not None:
                    return retry_ok

                # 🚫 -1116 Invalid orderType: 检查仓位（按方向），若已变则直接返回 warning
                if data.get("code") == -1116:
                    pos = self.broker.position.get_position(symbol, side=pos_check_side)
                    if pos and abs(float(pos.get("positionAmt", 0))) > 0:
                        print("[WARN] -1116: position exists")
                        print(data)
                        return {
                            "warning": "order_failed_but_position_exists",
                            "symbol": symbol,
                            "side": side,
                            "error": data,
                            "position_exists": True,
                        }

                # 🔥 L3: 失败后 → 再查一次仓位（防止已成交）
                cond_l3 = not reduce_only and self.has_open_position(symbol, pos_check_side)
                if cond_l3:
                    print("[WARN] Order failed but position exists")
                    print(data)
                    # 返回特殊状态，避免上层误判
                    return {
                        "warning": "order_failed_but_position_exists",
                        "symbol": symbol,
                        "side": side,
                        "error": data,
                        "position_exists": True,
                    }
                raise RuntimeError(f"Binance Error: {data}")

            return data

        except Exception as e:
            # 🚫 致命权限错误：直接抛出，禁止 retry
            if self._is_fatal_auth_error(e):
                raise RuntimeError(
                    "[FATAL AUTH ERROR] API key has no futures permission or invalid IP: " + str(e)
                ) from e

            # 🚫 -1116 Invalid orderType: 检查仓位，若已变則直接返回 warning
            if isinstance(e, requests.HTTPError) and getattr(e, "response", None) is not None:
                # 尝试解析交易所返回的 JSON 错误
                try:
                    err_data = e.response.json()
                except Exception:
                    err_data = None

                if err_data:
                    # 记录订单拒绝（可选告警日志）
                    self._log_order_reject(symbol, side, final, err_data)

                    retry_ok = _retry_on_min_notional(err_data)
                    if retry_ok is not None:
                        return retry_ok

                    # 处理 -1116（Invalid orderType）: 若交易所已有仓位，则返回 warning
                    if err_data.get("code") == -1116:
                        pos = self.broker.position.get_position(symbol, side=pos_check_side)
                        if pos and abs(float(pos.get("positionAmt", 0))) > 0:
                            print("[WARN] -1116: position exists")
                            print(err_data)
                            return {
                                "warning": "order_failed_but_position_exists",
                                "symbol": symbol,
                                "side": side,
                                "error": err_data,
                                "position_exists": True,
                            }
                # else: 无法解析 err_data，继续后续处理
            else:
                # 非 HTTPError 场景也记录一次
                self._log_order_reject(symbol, side, final, str(e))

            # 🔥 L3: 失败后 → 再查一次仓位（防止已成交）
            cond_l3_exc = not reduce_only and self.has_open_position(symbol, pos_check_side)
            if cond_l3_exc:
                print("[WARNING] Exception but position exists:")
                print(e)
                return {
                    "warning": "order_failed_but_position_exists",
                    "symbol": symbol,
                    "side": side,
                    "error": str(e),
                    "position_exists": True,
                }
            raise
        # end of place_standard_order

    def cancel_order(self, symbol: str, order_id: int) -> Dict[str, Any]:
        endpoint = self._order_endpoint()
        params = {"symbol": symbol, "orderId": order_id}
        return self.broker.request(
            "DELETE",
            endpoint,
            params=params,
            signed=True,
        ).json()

    def query_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        # 🔥 根据账户类型选择端点
        base = self.broker.um_base()
        if "papi" in base:
            path = "/papi/v1/um/openOrders"
        else:
            path = "/fapi/v1/openOrders"
        params = {"symbol": symbol} if symbol else {}
        url = f"{base}{path}"
        resp = self.broker.request("GET", url, params=params, signed=True)
        return resp.json()

    # --- 内部协议细节 ---

    def _order_endpoint(self) -> str:
        """
        动态选择订单端点：
        - PAPI-UM: /papi/v1/um/order
        - FAPI: /fapi/v1/order
        """
        if self.broker.is_papi_only():  # 检查是否为 PAPI_ONLY 模式
            base = self.broker.PAPI_BASE  # 使用 PAPI 基础路径
            return f"{base}/papi/v1/um/order"
        base = self.broker.FAPI_BASE  # 使用 FAPI 基础路径
        return f"{base}/fapi/v1/order"

    def place_protection_orders(
        self, symbol: str, side: str, tp: Optional[float], sl: Optional[float]
    ) -> List[Dict[str, Any]]:
        """
        下发止盈/止损保护单（MARKET 型触发单），用于在开仓后快速下保护单。

        返回包含每个创建订单的响应 JSON 列表。
        """
        results: List[Dict[str, Any]] = []
        # 计算下单方向：如果仓位方向为 LONG，则保护单为卖出 (SELL)，反之为 BUY
        order_side = "SELL" if str(side).upper() == "LONG" else "BUY"
        # 计算 positionSide（Hedge 模式适配）
        try:
            pos_side = self.broker.calculate_position_side(order_side, True)
        except Exception:
            pos_side = None

        endpoint = self._order_endpoint()

        for price, otype in [(tp, "TAKE_PROFIT_MARKET"), (sl, "STOP_MARKET")]:
            if price is None:
                continue
            # 🔥 PAPI-UM 和 FAPI 都使用 type 字段
            p: Dict[str, Any] = {
                "symbol": symbol,
                "side": order_side,
                "type": otype,
                "stopPrice": price,
                "closePosition": True,
            }
            if pos_side:
                p["positionSide"] = pos_side

            try:
                resp = self.broker.request("POST", endpoint, params=p, signed=True)
                results.append(resp.json())
            except Exception as e:
                # 记录并继续尝试下一个保护单
                try:
                    self._log_order_reject(symbol, order_side, p, str(e))
                except Exception:
                    pass

        return results

    def _finalize_params(self, params: Dict[str, Any], side: str, reduce_only: bool) -> Dict[str, Any]:
        """
        格式化订单参数，兼容 PAPI 实盘：
        - 全仓平仓必须传 closePosition=True + quantity（PAPI 要求）
        - 部分平仓使用 quantity + reduceOnly=True
        - MARKET 单不带 price
        - ONEWAY 模式禁止 positionSide
        - 🔥 PAPI UM 和 FAPI 都使用 'type' 字段（不是 orderType）
        """
        p = dict(params)
        p.pop("_allow_position_increase", None)
        p.pop("allow_position_increase", None)
        p["side"] = side.upper()
        is_hedge = self.broker.get_hedge_mode()

        # 🔥 PAPI UM 和 FAPI 都使用 type 字段
        if "type" in p:
            p["type"] = p["type"].upper()
        else:
            p["type"] = "MARKET"  # 默认值

        # 删除任何 orderType 字段（PAPI UM 不认这个）
        p.pop("orderType", None)

        # MARKET 不带 price
        if p.get("type") == "MARKET":
            p.pop("price", None)

        if not is_hedge:
            p.pop("positionSide", None)

        # 全仓平仓必须带 quantity
        if p.get("closePosition") is True or str(p.get("closePosition")).lower() == "true":
            p["closePosition"] = True
            if "quantity" not in p or not p["quantity"]:
                pos = self.broker.position.get_position(p.get("symbol"), side="BOTH")
                if pos:
                    p["quantity"] = abs(float(pos.get("positionAmt", 0)))
                else:
                    raise ValueError(f"无法获取仓位数量: {p.get('symbol')}")
            p.pop("reduceOnly", None)
            p.pop("reduce_only", None)
        else:
            # 开仓或部分平仓
            p.pop("closePosition", None)
            if reduce_only:
                # 对于 PAPI（或统一保证金）端点，部分平仓不要发送 reduceOnly（Binance 会拒绝）
                try:
                    if self.broker.is_papi_only():
                        p.pop("reduceOnly", None)
                    else:
                        p["reduceOnly"] = True
                except Exception:
                    # 若检查失败，保守行为：不删除已有字段，仍尝试设置
                    p["reduceOnly"] = True
            else:
                p.pop("reduceOnly", None)
            if is_hedge and "positionSide" not in p:
                ps = self.broker.calculate_position_side(side, reduce_only)
                if ps:
                    p["positionSide"] = ps
        return p
