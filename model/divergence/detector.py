"""Divergence detection engine.

The detector consumes a list of OHLC candles (the format produced by
`model.data_fetcher.fetch_mexc_futures_ohlc`) and an `IndicatorProvider`,
and returns the set of *currently confirmed* divergences.

Divergence rules
----------------
Let p1 be the older confirmed pivot, p2 the newer confirmed pivot.
Indicator values are read at the same bar as the price pivot — this is the
standard convention and is what TradingView's built-in divergence script does.

Pivot HIGHS:
	regular bearish:  price(p2) > price(p1)  AND  ind(p2) < ind(p1)
	hidden  bearish:  price(p2) < price(p1)  AND  ind(p2) > ind(p1)

Pivot LOWS:
	regular bullish:  price(p2) < price(p1)  AND  ind(p2) > ind(p1)
	hidden  bullish:  price(p2) > price(p1)  AND  ind(p2) < ind(p1)

Filters (configurable):
	- min_pivot_distance / max_pivot_distance bars between p1 and p2
	- min_price_diff_pct  noise floor on the price leg
	- min_indicator_diff  noise floor on the indicator leg
	- rsi_overbought / rsi_oversold confirmation thresholds (RSI only)

The detector is **stateless** — call `detect()` after each new closed bar and
deduplicate results upstream (the streaming poller does this via `signal_id`).
"""
from __future__ import annotations

import logging
from typing import Dict, Iterable, List, Sequence

import numpy as np

from ..config import DivergenceConfig
from ..indicators.base import IndicatorProvider
from .pivots import Pivot, find_pivots
from .types import DivergenceSignal, DivergenceType

log = logging.getLogger(__name__)


class DivergenceDetector:
	"""Detects bullish/bearish, regular/hidden divergences between price and an indicator."""

	def __init__(
		self,
		indicator: IndicatorProvider,
		config: DivergenceConfig | None = None,
	) -> None:
		self.indicator = indicator
		self.config = config or DivergenceConfig()

	# ------------------------------------------------------------------ public

	def detect(
		self,
		candles: Sequence[Dict[str, float]],
		symbol: str,
		timeframe: str,
	) -> List[DivergenceSignal]:
		"""Return all currently confirmed divergence signals."""
		cfg = self.config
		min_required = cfg.pivot_left + cfg.pivot_right + 1 + self.indicator.warmup
		if len(candles) < min_required:
			log.debug(
				"Not enough candles for %s %s: have=%d, need>=%d",
				symbol, timeframe, len(candles), min_required,
			)
			return []

		highs = np.fromiter((c["high"] for c in candles), dtype=np.float64, count=len(candles))
		lows = np.fromiter((c["low"] for c in candles), dtype=np.float64, count=len(candles))
		closes = np.fromiter((c["close"] for c in candles), dtype=np.float64, count=len(candles))
		opens = np.fromiter((c["open"] for c in candles), dtype=np.float64, count=len(candles))
		timestamps = [int(c["timestamp"]) for c in candles]

		indicator_series = self.indicator.compute(
			closes=closes, highs=highs, lows=lows, opens=opens
		)
		if indicator_series.shape[0] != closes.shape[0]:
			raise RuntimeError(
				f"Indicator '{self.indicator.name}' returned wrong length: "
				f"{indicator_series.shape[0]} vs {closes.shape[0]}"
			)

		signals: List[DivergenceSignal] = []

		high_pivots = find_pivots(highs, left=cfg.pivot_left, right=cfg.pivot_right, mode="high")
		low_pivots = find_pivots(lows, left=cfg.pivot_left, right=cfg.pivot_right, mode="low")

		signals.extend(
			self._scan_pairs(
				pivots=high_pivots,
				indicator_series=indicator_series,
				timestamps=timestamps,
				kind="high",
				symbol=symbol,
				timeframe=timeframe,
			)
		)
		signals.extend(
			self._scan_pairs(
				pivots=low_pivots,
				indicator_series=indicator_series,
				timestamps=timestamps,
				kind="low",
				symbol=symbol,
				timeframe=timeframe,
			)
		)
		return signals

	# ----------------------------------------------------------------- internal

	def _scan_pairs(
		self,
		pivots: List[Pivot],
		indicator_series: np.ndarray,
		timestamps: List[int],
		kind: str,
		symbol: str,
		timeframe: str,
	) -> Iterable[DivergenceSignal]:
		cfg = self.config
		if len(pivots) < 2:
			return []

		# Filter pivots whose indicator value is not finite (warm-up bars).
		filtered: List[Pivot] = [
			p for p in pivots if np.isfinite(indicator_series[p.index])
		]
		if len(filtered) < 2:
			return []

		results: List[DivergenceSignal] = []
		# Compare the most recent pivot against the previous N — capturing
		# divergences that span more than just the immediately prior swing.
		recent = filtered[-cfg.pivot_scan_depth:]
		newest = recent[-1]
		candidates = recent[:-1]

		for older in candidates:
			result = self._classify_pair(
				p1=older,
				p2=newest,
				indicator_series=indicator_series,
				timestamps=timestamps,
				kind=kind,
				symbol=symbol,
				timeframe=timeframe,
			)
			if result is not None:
				results.append(result)

		return results

	def _classify_pair(
		self,
		p1: Pivot,
		p2: Pivot,
		indicator_series: np.ndarray,
		timestamps: List[int],
		kind: str,
		symbol: str,
		timeframe: str,
	) -> DivergenceSignal | None:
		cfg = self.config

		bar_distance = p2.index - p1.index
		if bar_distance < cfg.min_pivot_distance or bar_distance > cfg.max_pivot_distance:
			return None

		ind1 = float(indicator_series[p1.index])
		ind2 = float(indicator_series[p2.index])
		if not (np.isfinite(ind1) and np.isfinite(ind2)):
			return None

		price1 = p1.value
		price2 = p2.value
		# Avoid divide-by-zero on free assets like a brand-new listing.
		if price1 == 0:
			return None
		price_delta_pct = (price2 - price1) / price1 * 100.0
		indicator_delta = ind2 - ind1

		if abs(price_delta_pct) < cfg.min_price_diff_pct:
			return None
		if abs(indicator_delta) < cfg.min_indicator_diff:
			return None

		divergence_type: DivergenceType | None = None

		if kind == "high":
			if cfg.enable_regular and price2 > price1 and ind2 < ind1:
				divergence_type = DivergenceType.REGULAR_BEARISH
			elif cfg.enable_hidden and price2 < price1 and ind2 > ind1:
				divergence_type = DivergenceType.HIDDEN_BEARISH
		else:  # "low"
			if cfg.enable_regular and price2 < price1 and ind2 > ind1:
				divergence_type = DivergenceType.REGULAR_BULLISH
			elif cfg.enable_hidden and price2 > price1 and ind2 < ind1:
				divergence_type = DivergenceType.HIDDEN_BULLISH

		if divergence_type is None:
			return None

		# Confirmation thresholds (RSI-style indicators only). We require at
		# least one of the two pivot indicator values to be in the
		# "exhaustion" zone — this is what filters out the bulk of noise
		# divergences in the middle of trends.
		if self.indicator.name.upper().startswith("RSI"):
			if divergence_type.is_bullish and cfg.rsi_oversold is not None:
				if not (ind1 <= cfg.rsi_oversold or ind2 <= cfg.rsi_oversold):
					return None
			if (not divergence_type.is_bullish) and cfg.rsi_overbought is not None:
				if not (ind1 >= cfg.rsi_overbought or ind2 >= cfg.rsi_overbought):
					return None

		# Confirmation bar = pivot2 + right (when the pivot became valid).
		confirmation_index = min(p2.index + cfg.pivot_right, len(timestamps) - 1)

		confidence = self._score_confidence(
			price_delta_pct=price_delta_pct,
			indicator_delta=indicator_delta,
			ind1=ind1,
			ind2=ind2,
			divergence_type=divergence_type,
		)

		return DivergenceSignal(
			symbol=symbol,
			timeframe=timeframe,
			indicator_name=self.indicator.name,
			divergence_type=divergence_type,
			pivot1_index=p1.index,
			pivot1_timestamp=timestamps[p1.index],
			pivot1_price=price1,
			pivot1_indicator=ind1,
			pivot2_index=p2.index,
			pivot2_timestamp=timestamps[p2.index],
			pivot2_price=price2,
			pivot2_indicator=ind2,
			confirmation_index=confirmation_index,
			confirmation_timestamp=timestamps[confirmation_index],
			price_delta_pct=price_delta_pct,
			indicator_delta=indicator_delta,
			confidence=confidence,
		)

	def _score_confidence(
		self,
		price_delta_pct: float,
		indicator_delta: float,
		ind1: float,
		ind2: float,
		divergence_type: DivergenceType,
	) -> float:
		"""Soft [0,1] score blending leg magnitude and exhaustion depth."""
		cfg = self.config
		# Magnitude scores — each normalized so that "comfortably above
		# the noise floor" lands near 1.0.
		price_score = min(abs(price_delta_pct) / max(cfg.min_price_diff_pct * 5.0, 1e-9), 1.0)
		ind_score = min(abs(indicator_delta) / max(cfg.min_indicator_diff * 5.0, 1e-9), 1.0)

		exhaustion_score = 0.5
		if self.indicator.name.upper().startswith("RSI"):
			if divergence_type.is_bullish and cfg.rsi_oversold is not None:
				depth = max(cfg.rsi_oversold - min(ind1, ind2), 0.0)
				exhaustion_score = min(depth / max(cfg.rsi_oversold, 1e-9), 1.0)
			elif (not divergence_type.is_bullish) and cfg.rsi_overbought is not None:
				depth = max(max(ind1, ind2) - cfg.rsi_overbought, 0.0)
				exhaustion_score = min(depth / max(100.0 - cfg.rsi_overbought, 1e-9), 1.0)

		return round(0.4 * price_score + 0.4 * ind_score + 0.2 * exhaustion_score, 4)
