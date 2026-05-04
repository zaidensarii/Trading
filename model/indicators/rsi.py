"""Wilder's RSI.

Uses TA-Lib when installed (matches the reference C implementation exactly),
falling back to a vectorized Wilder's smoothing implementation in pure numpy.
The fallback is bit-comparable to TA-Lib for sufficiently long series.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from .base import IndicatorProvider

try:
	import talib  # type: ignore

	_HAS_TALIB = True
except Exception:  # pragma: no cover - TA-Lib is optional
	_HAS_TALIB = False


def _rsi_wilder_numpy(closes: np.ndarray, period: int) -> np.ndarray:
	"""Wilder's RSI using recursive smoothing — matches TA-Lib semantics."""
	n = closes.shape[0]
	rsi = np.full(n, np.nan, dtype=np.float64)
	if n <= period:
		return rsi

	deltas = np.diff(closes)
	gains = np.where(deltas > 0, deltas, 0.0)
	losses = np.where(deltas < 0, -deltas, 0.0)

	# Initial averages are simple means of the first `period` deltas.
	avg_gain = gains[:period].mean()
	avg_loss = losses[:period].mean()

	if avg_loss == 0:
		rsi[period] = 100.0
	else:
		rs = avg_gain / avg_loss
		rsi[period] = 100.0 - (100.0 / (1.0 + rs))

	# Wilder's smoothing: AvgGain_t = (AvgGain_{t-1} * (n-1) + gain_t) / n
	for i in range(period + 1, n):
		gain = gains[i - 1]
		loss = losses[i - 1]
		avg_gain = (avg_gain * (period - 1) + gain) / period
		avg_loss = (avg_loss * (period - 1) + loss) / period
		if avg_loss == 0:
			rsi[i] = 100.0
		else:
			rs = avg_gain / avg_loss
			rsi[i] = 100.0 - (100.0 / (1.0 + rs))

	return rsi


class RSIIndicator(IndicatorProvider):
	"""Relative Strength Index (Wilder, 1978)."""

	name = "RSI"

	def __init__(self, period: int = 14) -> None:
		if period < 2:
			raise ValueError("RSI period must be >= 2.")
		self.period = period

	@property
	def warmup(self) -> int:
		return self.period

	def compute(
		self,
		closes: np.ndarray,
		highs: Optional[np.ndarray] = None,
		lows: Optional[np.ndarray] = None,
		opens: Optional[np.ndarray] = None,
	) -> np.ndarray:
		closes = np.asarray(closes, dtype=np.float64)
		if closes.ndim != 1:
			raise ValueError("closes must be 1D.")
		if _HAS_TALIB:
			return talib.RSI(closes, timeperiod=self.period)
		return _rsi_wilder_numpy(closes, self.period)
