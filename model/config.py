"""Runtime configuration and timeframe helpers.

Centralizing this prevents drift between modules and makes the system easy to
tune in financially sensitive environments.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple


# Mapping from MEXC contract intervals -> seconds per candle.
INTERVAL_SECONDS: dict[str, int] = {
	"Min1": 60,
	"Min5": 5 * 60,
	# MEXC has no native 10m futures kline; data_fetcher builds these from paired Min5 bars.
	"Min10": 10 * 60,
	"Min15": 15 * 60,
	"Min30": 30 * 60,
	"Min60": 60 * 60,
	"Hour4": 4 * 60 * 60,
	"Hour8": 8 * 60 * 60,
	"Day1": 24 * 60 * 60,
	"Week1": 7 * 24 * 60 * 60,
	"Month1": 30 * 24 * 60 * 60,
}


def interval_to_seconds(time_frame: str) -> int:
	if time_frame not in INTERVAL_SECONDS:
		raise ValueError(
			f"Unsupported time_frame '{time_frame}'. "
			f"Allowed: {sorted(INTERVAL_SECONDS)}"
		)
	return INTERVAL_SECONDS[time_frame]


@dataclass(frozen=True)
class DivergenceConfig:
	"""Tunables for the divergence detector.

	The defaults are conservative and chosen to favor accuracy over recall in
	a real-money setting; loosen them for backtesting/exploration.
	"""

	# RSI parameters.
	rsi_period: int = 14

	# MACD parameters (only used when MACD divergence is enabled).
	macd_fast: int = 12
	macd_slow: int = 26
	macd_signal: int = 9

	# Pivot detection — `right` controls confirmation lag (bars after the
	# pivot needed to confirm it). Larger = fewer false pivots, more lag.
	pivot_left: int = 3
	pivot_right: int = 3

	# Distance constraints between the two pivots being compared.
	min_pivot_distance: int = 5
	max_pivot_distance: int = 60

	# Noise filters — pivots that are essentially equal are ignored.
	min_price_diff_pct: float = 0.10  # at least 0.10% price move between pivots
	min_indicator_diff: float = 1.0  # at least 1.0 RSI point between pivots

	# Confirmation thresholds. Set to None to disable.
	rsi_overbought: float | None = 60.0  # bearish needs at least one pivot >= this
	rsi_oversold: float | None = 40.0  # bullish needs at least one pivot <= this

	# Which divergence types to surface.
	enable_regular: bool = True
	enable_hidden: bool = False

	# How many recent confirmed pivots to scan when looking for divergences.
	# Comparing only the most recent pair tends to be the most actionable, but
	# scanning a few back catches divergences that confirm a few bars later.
	pivot_scan_depth: int = 3


@dataclass(frozen=True)
class StreamingConfig:
	"""Tunables for the real-time poller."""

	symbol: str
	time_frame: str
	num_candles: int = 100

	# Seconds added on top of the candle close before polling. MEXC takes a
	# moment to publish the new candle; this avoids fetching a stale frame.
	close_buffer_seconds: float = 2.0

	# Hard upper bound on poll interval — useful when the timeframe is large
	# (e.g. Day1) but you still want intermediate refreshes.
	max_poll_interval_seconds: float = 60.0

	# Hard lower bound to avoid hammering the API.
	min_poll_interval_seconds: float = 1.0

	# Max consecutive fetch failures before the poller raises.
	max_consecutive_errors: int = 5

	# Backoff (seconds) on each consecutive error: 2^n * base, capped.
	error_backoff_base: float = 1.5
	error_backoff_cap: float = 30.0


@dataclass
class AppConfig:
	"""Top-level application config."""

	symbols: List[str] = field(default_factory=lambda: ["BTC_USDT"])
	time_frame: str = "Min10"
	num_candles: int = 100
	indicators: Tuple[str, ...] = ("RSI",)  # ("RSI",) or ("RSI", "MACD")
	divergence: DivergenceConfig = field(default_factory=DivergenceConfig)
	streaming: StreamingConfig | None = None
	log_level: str = "INFO"
