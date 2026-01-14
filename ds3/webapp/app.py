import functools
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, Any, Iterable

from flask import Flask, Response, jsonify, render_template, request

BASE_DIR = Path(__file__).resolve().parent
SCRIPT_PATH = Path(os.getenv("TRADE_SCRIPT_PATH", "/root/ds3/ds3.py"))
CONFIG_OVERRIDE_PATH = Path(os.getenv("TRADE_CONFIG_OVERRIDE", "/root/ds3/config_override.json"))
LOG_PATH = Path(os.getenv("TRADE_LOG_PATH", BASE_DIR / "runtime.log"))
API_TOKEN = os.getenv("WEB_API_TOKEN")

DEFAULT_CONFIG: Dict[str, Any] = {
    "symbol": "ETH/USDT:USDT",
    "leverage": 50,
    "timeframe": "15m",
    "position_management": {
        "position_usage_pct": 80.0,
        "base_usdt_amount": 100,
        "high_confidence_multiplier": 1.5,
        "medium_confidence_multiplier": 1.0,
        "low_confidence_multiplier": 0.5,
        "enable_intelligent_position": True,
        "trend_strength_multiplier": 1.2,
        "enable_pyramiding": False,
        "pyramid_max_layers": 3,
        "pyramid_step_gain_pct": 0.6,
        "pyramid_size_multiplier": 0.5,
    },
    "risk_control": {
        "max_daily_loss_pct": 5.0,
        "max_single_loss_pct": 1.1,
        "max_position_pct": 80.0,
        "stop_loss_default_pct": 1.6,
        "take_profit_default_pct": 5.5,
        "max_consecutive_losses": 3,
        "max_daily_trades": 10,
        "circuit_breaker_enabled": True,
        "circuit_breaker_cooldown": 300,
    },
    "trailing_stop": {
        "enable": True,
        "trigger_pct": 0.5,
        "callback_pct": 0.25,
    },
    "signal_filters": {
        "min_confidence": "HIGH",
        "scale_with_confidence": True,
    },
}

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"), static_folder=str(BASE_DIR / "static"))

process: subprocess.Popen | None = None
process_lock = threading.Lock()


def _get_token_from_request() -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    token = request.headers.get("X-API-Key")
    if token:
        return token
    return request.args.get("token", "")


def require_auth(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if API_TOKEN:
            token = _get_token_from_request()
            if token != API_TOKEN:
                return jsonify({"error": "unauthorized"}), 401
        return func(*args, **kwargs)

    return wrapper


def read_config() -> Dict[str, Any]:
    if CONFIG_OVERRIDE_PATH.exists():
        try:
            with open(CONFIG_OVERRIDE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return DEFAULT_CONFIG.copy()
    return DEFAULT_CONFIG.copy()


def write_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    config = DEFAULT_CONFIG.copy()
    # merge shallow top-level and nested dictionaries
    for k, v in payload.items():
        if k in {"position_management", "risk_control", "trailing_stop", "signal_filters"} and isinstance(v, dict):
            config[k].update(v)
        else:
            config[k] = v
    CONFIG_OVERRIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_OVERRIDE_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    return config


def start_bot():
    global process
    with process_lock:
        if process and process.poll() is None:
            return False, "already running"
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(LOG_PATH, "a", buffering=1, encoding="utf-8")
        env = os.environ.copy()
        env["TRADE_CONFIG_OVERRIDE"] = str(CONFIG_OVERRIDE_PATH)
        try:
            process = subprocess.Popen(
                ["python", str(SCRIPT_PATH)],
                stdout=log_file,
                stderr=log_file,
                env=env,
            )
        except Exception as e:
            return False, str(e)
        return True, "started"


def stop_bot():
    global process
    with process_lock:
        if not process or process.poll() is not None:
            return False, "not running"
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
        return True, "stopped"


@app.route("/")
def index():
    return render_template("index.html", require_token=bool(API_TOKEN))


@app.route("/api/status")
@require_auth
def api_status():
    running = process is not None and process.poll() is None
    pid = process.pid if running and process is not None else None
    return jsonify({"running": running, "pid": pid, "script": str(SCRIPT_PATH)})


@app.route("/api/start", methods=["POST"])
@require_auth
def api_start():
    ok, msg = start_bot()
    status_code = 200 if ok else 400
    return jsonify({"ok": ok, "message": msg}), status_code


@app.route("/api/stop", methods=["POST"])
@require_auth
def api_stop():
    ok, msg = stop_bot()
    status_code = 200 if ok else 400
    return jsonify({"ok": ok, "message": msg}), status_code


@app.route("/api/logs")
@require_auth
def api_logs():
    lines = int(request.args.get("lines", 200))
    if LOG_PATH.exists():
        try:
            with open(LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
                content = f.readlines()[-lines:]
            return jsonify({"lines": content})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    return jsonify({"lines": []})


@app.route("/api/config", methods=["GET", "POST"])
@require_auth
def api_config():
    if request.method == "GET":
        return jsonify(read_config())
    payload = request.get_json(force=True)
    updated = write_config(payload)
    return jsonify(updated)


def _stream_log_lines(poll_interval: float = 1.0) -> Iterable[str]:
    last_size = LOG_PATH.stat().st_size if LOG_PATH.exists() else 0
    while True:
        if LOG_PATH.exists():
            with open(LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
                f.seek(last_size)
                chunk = f.read()
                if chunk:
                    last_size = f.tell()
                    for line in chunk.splitlines():
                        yield f"data: {line}\n\n"
        time.sleep(poll_interval)


@app.route("/api/logs/stream")
@require_auth
def api_logs_stream():
    return Response(_stream_log_lines(), mimetype="text/event-stream")


@app.route("/api/pnl")
@require_auth
def api_pnl():
    points = []
    pattern = re.compile(r"(?:pnl|equity|balance)[^\d-]*([-]?\d+(?:\.\d+)?)", re.IGNORECASE)
    if LOG_PATH.exists():
        try:
            with open(LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
                for idx, line in enumerate(f):
                    for match in pattern.finditer(line):
                        value = float(match.group(1))
                        label = line[:19] if len(line) >= 19 else f"#{idx+1}"
                        points.append({"t": label, "v": value})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    return jsonify({"points": points[-500:]})


if __name__ == "__main__":
    host = os.getenv("WEB_HOST", "0.0.0.0")
    port = int(os.getenv("WEB_PORT", "8000"))
    app.run(host=host, port=port, debug=False)
