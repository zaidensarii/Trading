"""Web UI for the divergence scanner.

Run:
    python web.py          # starts on http://127.0.0.1:5000
    python web.py --port 8080
"""
from __future__ import annotations

import argparse
import json
import logging
import ssl
import time
from datetime import datetime, timezone, timedelta
from urllib import request as url_request

from flask import Flask, jsonify, render_template, request as flask_request

try:
    import certifi
except Exception:
    certifi = None

from model.config import DivergenceConfig, INTERVAL_SECONDS, interval_to_seconds
from model.data_fetcher import fetch_mexc_futures_ohlc
from model.divergence import DivergenceDetector
from model.indicators import MACDIndicator, RSIIndicator

app = Flask(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger(__name__)

# Pakistan Standard Time = UTC+5
PKT = timezone(timedelta(hours=5))


# ── helpers ──────────────────────────────────────────────────────────────

def _fmt_pkt(ts: int) -> str:
    """Format a Unix timestamp as a PKT datetime string."""
    return datetime.fromtimestamp(ts, tz=PKT).strftime("%Y-%m-%d %I:%M %p")


def _fetch_mexc_symbols() -> list[str]:
    """Fetch available USDT futures symbols from MEXC."""
    ctx = ssl.create_default_context(cafile=certifi.where()) if certifi else ssl.create_default_context()
    url = "https://api.mexc.com/api/v1/contract/detail"
    try:
        with url_request.urlopen(url, timeout=10, context=ctx) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if not payload.get("success"):
            return []
        symbols = sorted(
            d["symbol"] for d in payload.get("data", [])
            if d.get("quoteCoin") == "USDT" and d.get("state") == 0
        )
        return symbols
    except Exception:
        log.exception("Failed to fetch MEXC symbols")
        return []


def _build_detectors(indicator_names: list[str], div_cfg: DivergenceConfig):
    detectors = []
    for name in indicator_names:
        key = name.strip().upper()
        if key == "RSI":
            detectors.append(
                DivergenceDetector(RSIIndicator(period=div_cfg.rsi_period), div_cfg)
            )
        elif key == "MACD":
            detectors.append(
                DivergenceDetector(
                    MACDIndicator(
                        fast=div_cfg.macd_fast,
                        slow=div_cfg.macd_slow,
                        signal=div_cfg.macd_signal,
                    ),
                    div_cfg,
                )
            )
    return detectors


# ── routes ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    timeframes = sorted(INTERVAL_SECONDS.keys(), key=lambda k: INTERVAL_SECONDS[k])
    return render_template("index.html", timeframes=timeframes)


@app.route("/api/symbols")
def symbols():
    """Return list of MEXC futures USDT symbols."""
    syms = _fetch_mexc_symbols()
    return jsonify(syms)


@app.route("/api/scan", methods=["POST"])
def scan():
    """Run a divergence scan with the user-selected parameters."""
    body = flask_request.get_json(force=True)

    symbol = (body.get("symbol") or "TAO_USDT").strip().upper()
    time_frame = body.get("timeframe", "Min10")
    indicators = body.get("indicators", ["RSI"])
    enable_hidden = bool(body.get("hidden", False))
    num_candles = int(body.get("numCandles", 100))

    # Clamp num_candles to a safe range.
    num_candles = max(20, min(num_candles, 500))

    # Validate timeframe.
    if time_frame not in INTERVAL_SECONDS:
        return jsonify({"error": f"Invalid timeframe: {time_frame}"}), 400

    # Parse date/time range.
    start_str = body.get("startDate")  # ISO 8601 string
    end_str = body.get("endDate")

    try:
        if start_str:
            start_ts = int(datetime.fromisoformat(start_str).replace(tzinfo=PKT).timestamp())
        else:
            secs = interval_to_seconds(time_frame)
            start_ts = int(time.time()) - (num_candles + 5) * secs

        if end_str:
            end_ts = int(datetime.fromisoformat(end_str).replace(tzinfo=PKT).timestamp())
        else:
            end_ts = int(time.time())
    except (ValueError, TypeError) as exc:
        return jsonify({"error": f"Bad date value: {exc}"}), 400

    if start_ts >= end_ts:
        return jsonify({"error": "Start date must be before end date."}), 400

    # Fetch candles.
    try:
        data = fetch_mexc_futures_ohlc(
            timeperiod=(start_ts, end_ts),
            time_frame=time_frame,
            coin=[symbol],
        )
    except Exception as exc:
        log.exception("Fetch error")
        return jsonify({"error": f"Failed to fetch candles: {exc}"}), 502

    candles = data.get(symbol) or (next(iter(data.values())) if data else [])
    if not candles:
        return jsonify({"error": "No candle data returned for the selected range."}), 404

    # Trim to requested window.
    candles = candles[-num_candles:]

    # Run divergence detectors.
    div_cfg = DivergenceConfig(enable_hidden=enable_hidden)
    detectors = _build_detectors(indicators, div_cfg)

    signals = []
    for det in detectors:
        found = det.detect(candles, symbol, time_frame)
        for sig in found:
            signals.append(sig.to_dict())

    # Format candle timestamps for display.
    candles_out = []
    for c in candles:
        candles_out.append({
            "timestamp": c["timestamp"],
            "datetime": _fmt_pkt(c["timestamp"]),
            "open": c["open"],
            "high": c["high"],
            "low": c["low"],
            "close": c["close"],
        })

    # Format signal timestamps too.
    for s in signals:
        for key in ("pivot1_timestamp", "pivot2_timestamp", "confirmation_timestamp"):
            ts = s.get(key)
            if ts:
                s[key + "_fmt"] = _fmt_pkt(ts)

    return jsonify({
        "symbol": symbol,
        "timeframe": time_frame,
        "candleCount": len(candles_out),
        "candles": candles_out,
        "signals": signals,
    })


# ── main ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--debug", action="store_true")
    args = p.parse_args()
    app.run(host="127.0.0.1", port=args.port, debug=args.debug)
