from __future__ import annotations

from typing import Dict, List, Optional, Tuple
from urllib import parse, request
import json
import ssl

try:
	import certifi
except Exception:  # pragma: no cover
	certifi = None


MEXC_FUTURES_BASE_URL = "https://contract.mexc.com"
MEXC_FUTURES_KLINE_PATH = "/api/v1/contract/kline/{symbol}"
VALID_INTERVALS = {
	"Min1",
	"Min5",
	"Min15",
	"Min30",
	"Min60",
	"Hour4",
	"Hour8",
	"Day1",
	"Week1",
	"Month1",
}


def _build_ssl_context() -> ssl.SSLContext:
	if certifi is not None:
		return ssl.create_default_context(cafile=certifi.where())

	return ssl.create_default_context()


def _parse_timeperiod(timeperiod: Tuple[Optional[int], Optional[int]]) -> Tuple[Optional[int], Optional[int]]:
	if not isinstance(timeperiod, (tuple, list)) or len(timeperiod) != 2:
		raise ValueError("timeperiod must be a tuple/list: (start_time, end_time).")

	start_time, end_time = timeperiod

	if start_time is not None and (not isinstance(start_time, int) or start_time <= 0):
		raise ValueError("start_time must be a positive Unix timestamp in seconds or None.")

	if end_time is not None and (not isinstance(end_time, int) or end_time <= 0):
		raise ValueError("end_time must be a positive Unix timestamp in seconds or None.")

	if start_time is not None and end_time is not None and start_time > end_time:
		raise ValueError("start_time cannot be greater than end_time.")

	return start_time, end_time


def _merge_min5_into_min10(candles: List[Dict[str, float]]) -> List[Dict[str, float]]:
	"""Pair consecutive 5m bars into synthetic 10m OHLC (oldest-first lists)."""

	merged: List[Dict[str, float]] = []
	for i in range(0, len(candles) - 1, 2):
		a, b = candles[i], candles[i + 1]
		merged.append(
			{
				"timestamp": a["timestamp"],
				"open": a["open"],
				"high": max(a["high"], b["high"]),
				"low": min(a["low"], b["low"]),
				"close": b["close"],
			}
		)
	return merged


def _normalize_symbol(coin: str) -> str:
	symbol = coin.strip().upper().replace("-", "_").replace("/", "_")

	if not symbol:
		raise ValueError("Coin symbol cannot be empty.")

	if "_" not in symbol:
		symbol = f"{symbol}_USDT"

	return symbol


def fetch_mexc_futures_ohlc(
	timeperiod: Tuple[Optional[int], Optional[int]],
	time_frame: str,
	coin: List[str],
) -> Dict[str, List[Dict[str, float]]]:
	"""
	Fetch OHLC futures candles from MEXC for one or more coins.

	Args:
		timeperiod: Candle time range as (start_time, end_time) in Unix seconds.
			Use None for an open bound.
			Examples: (1712700000, 1712786400), (1712700000, None), (None, 1712786400)
		time_frame: Candle interval. Supported values:
			Min1, Min5, Min10 (built from consecutive Min5 bars), Min15, Min30,
			Min60, Hour4, Hour8, Day1, Week1, Month1
		coin: List of coin symbols. Examples: ["BTC", "ETH"], ["BTC_USDT"]

	Returns:
		Dictionary keyed by normalized contract symbol, where each value is a list
		of OHLC candle dictionaries ordered oldest to newest.

	Raises:
		ValueError: If inputs are invalid.
		RuntimeError: If a coin fetch fails or MEXC returns an error response.
	"""
	start_time, end_time = _parse_timeperiod(timeperiod)

	_allowed = VALID_INTERVALS | {"Min10"}
	if time_frame not in _allowed:
		raise ValueError(
			f"Invalid time_frame '{time_frame}'. Allowed values: {sorted(_allowed)}"
		)

	if not coin or not isinstance(coin, list):
		raise ValueError("coin must be a non-empty list of symbols.")

	if time_frame == "Min10":
		core = fetch_mexc_futures_ohlc(timeperiod, "Min5", coin)
		return {sym: _merge_min5_into_min10(bars) for sym, bars in core.items()}

	results: Dict[str, List[Dict[str, float]]] = {}

	for raw_coin in coin:
		symbol = _normalize_symbol(raw_coin)
		path = MEXC_FUTURES_KLINE_PATH.format(symbol=parse.quote(symbol))
		query_params = {"interval": time_frame}
		if start_time is not None:
			query_params["start"] = start_time
		if end_time is not None:
			query_params["end"] = end_time

		query = parse.urlencode(query_params)
		url = f"{MEXC_FUTURES_BASE_URL}{path}?{query}"

		try:
			with request.urlopen(url, timeout=20, context=_build_ssl_context()) as response:
				payload = json.loads(response.read().decode("utf-8"))
		except Exception as exc:
			raise RuntimeError(f"Network error while fetching {symbol}: {exc}") from exc

		if not payload.get("success"):
			raise RuntimeError(
				f"MEXC error for {symbol}: code={payload.get('code')} message={payload.get('message')}"
			)

		data = payload.get("data") or {}
		times = data.get("time", [])
		opens = data.get("open", [])
		highs = data.get("high", [])
		lows = data.get("low", [])
		closes = data.get("close", [])

		count = min(len(times), len(opens), len(highs), len(lows), len(closes))
		if count == 0:
			results[symbol] = []
			continue

		start_idx = len(times) - count
		candles: List[Dict[str, float]] = []

		for i in range(start_idx, len(times)):
			candles.append(
				{
					"timestamp": int(times[i]),
					"open": float(opens[i]),
					"high": float(highs[i]),
					"low": float(lows[i]),
					"close": float(closes[i]),
				}
			)

		results[symbol] = candles

	return results

