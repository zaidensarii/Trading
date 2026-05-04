"""MACD histogram for divergence analysis.

For divergence purposes the histogram (MACD - signal) is the most
informative series, since it is what reverses ahead of price more reliably
than the raw MACD line.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from .base import IndicatorProvider

try:
	import talib  # type: ignore

	_HAS_TALIB = True
except Exception:  # pragma: no cover
	_HAS_TALIB = False


def _ema(values: np.ndarray, period: int) -> np.ndarray:
	n = values.shape[0]
	out = np.full(n, np.nan, dtype=np.float64)
	if n < period:
		return out
	alpha = 2.0 / (period + 1.0)
	# Seed with SMA of the first `period` values, then iterate.
	out[period - 1] = values[:period].mean()
	for i in range(period, n):
		out[i] = alpha * values[i] + (1.0 - alpha) * out[i - 1]
	return out


class MACDIndicator(IndicatorProvider):
	"""MACD histogram = EMA_fast(close) - EMA_slow(close) - EMA_signal(...)."""

	name = "MACD-Hist"

	def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9) -> None:
		if not (0 < fast < slow):
			raise ValueError("Require 0 < fast < slow.")
		if signal < 1:
			raise ValueError("signal must be >= 1.")
		self.fast = fast
		self.slow = slow
		self.signal = signal

	@property
	def warmup(self) -> int:
		# TA-Lib needs slow + signal - 1 bars before first finite hist value.
		return self.slow + self.signal - 1

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
			_, _, hist = talib.MACD(
				closes,
				fastperiod=self.fast,
				slowperiod=self.slow,
				signalperiod=self.signal,
			)
			return hist

		ema_fast = _ema(closes, self.fast)
		ema_slow = _ema(closes, self.slow)
		macd_line = ema_fast - ema_slow
		# Signal EMA is computed over the MACD line; ignore leading NaNs.
		valid_mask = ~np.isnan(macd_line)
		signal_line = np.full_like(macd_line, np.nan)
		if valid_mask.sum() >= self.signal:
			start = int(np.argmax(valid_mask))
			ema_sig_partial = _ema(macd_line[start:], self.signal)
			signal_line[start:] = ema_sig_partial
		return macd_line - signal_line
