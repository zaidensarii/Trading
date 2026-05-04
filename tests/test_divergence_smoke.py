"""Offline smoke test for the divergence engine.

Builds two synthetic series:
  1. A regular **bearish** scenario: price makes a higher high, RSI makes a
     lower high. We expect a REGULAR_BEARISH signal.
  2. A regular **bullish** scenario: price makes a lower low, RSI makes a
     higher low. We expect a REGULAR_BULLISH signal.

Run:
	python -m tests.test_divergence_smoke
"""
from __future__ import annotations

import math
import sys
import time
from typing import List

import numpy as np

from model.alerts import TerminalAlertChannel
from model.config import DivergenceConfig
from model.divergence import DivergenceDetector
from model.divergence.types import DivergenceType
from model.indicators import RSIIndicator


def _candles_from_closes(closes: List[float], start_ts: int = 1_700_000_000, step: int = 900):
	"""Wrap a closes-only series into the OHLC dict format the detector expects."""
	out = []
	for i, c in enumerate(closes):
		# Tight high/low so pivot detection runs on a clean signal.
		out.append(
			{
				"timestamp": start_ts + i * step,
				"open": c,
				"high": c * 1.0005,
				"low": c * 0.9995,
				"close": c,
			}
		)
	return out


def _bearish_series() -> List[float]:
	"""Hand-crafted shape with a textbook regular bearish divergence:

	  phase 1  — sharp rally to peak A  (strong momentum → high RSI)
	  phase 2  — pullback to B
	  phase 3  — slow grind to peak C marginally above A (weak momentum → lower RSI)
	  phase 4  — rollover

	The two confirmed pivots will be A (high RSI) and C (lower RSI).
	"""
	rng = np.random.default_rng(7)
	closes: List[float] = []
	# Phase 0: flat warm-up so RSI stabilizes (~50).
	closes.extend([100.0] * 30)
	# Phase 1: sharp rally — 60 bars, +0.40 per bar.
	closes.extend([100.0 + i * 0.40 for i in range(1, 61)])  # ends at 124
	# Phase 2: pullback — 30 bars, -0.30 per bar.
	closes.extend([124.0 - i * 0.30 for i in range(1, 31)])  # ends at 115
	# Phase 3: slow grind to a marginally higher high — 110 bars, +0.10 / bar.
	closes.extend([115.0 + i * 0.10 for i in range(1, 111)])  # ends at 126
	# Phase 4: rollover — 60 bars, -0.20 / bar.
	closes.extend([126.0 - i * 0.20 for i in range(1, 61)])
	noise = rng.normal(0.0, 0.10, len(closes))
	return (np.array(closes) + noise).tolist()


def _bullish_series() -> List[float]:
	"""Hand-crafted shape with a textbook regular bullish divergence:

	  phase 1 — sharp drop to trough A (strong momentum → low RSI)
	  phase 2 — bounce to B
	  phase 3 — slow grind down to trough C marginally below A (weak momentum → higher RSI)
	  phase 4 — recovery
	"""
	rng = np.random.default_rng(11)
	closes: List[float] = []
	closes.extend([100.0] * 30)
	# Phase 1: sharp drop — 60 bars, -0.40 per bar.
	closes.extend([100.0 - i * 0.40 for i in range(1, 61)])  # ends at 76
	# Phase 2: bounce — 30 bars, +0.30 per bar.
	closes.extend([76.0 + i * 0.30 for i in range(1, 31)])  # ends at 85
	# Phase 3: slow grind down — 110 bars, -0.10 per bar.
	closes.extend([85.0 - i * 0.10 for i in range(1, 111)])  # ends at 74
	# Phase 4: recovery — 60 bars, +0.20 per bar.
	closes.extend([74.0 + i * 0.20 for i in range(1, 61)])
	noise = rng.normal(0.0, 0.10, len(closes))
	return (np.array(closes) + noise).tolist()


def _run(scenario: str, candles, expected: DivergenceType) -> bool:
	cfg = DivergenceConfig(
		# Loosen confirmations slightly for synthetic data — real markets
		# routinely hit 70/30 RSI; these sine-based prices may not.
		rsi_overbought=55.0,
		rsi_oversold=45.0,
		min_indicator_diff=0.5,
		min_price_diff_pct=0.05,
		pivot_left=3,
		pivot_right=3,
		min_pivot_distance=20,
		max_pivot_distance=200,
	)
	detector = DivergenceDetector(RSIIndicator(period=14), cfg)
	signals = detector.detect(candles, symbol="TEST_USDT", timeframe="Min15")
	matched = [s for s in signals if s.divergence_type is expected]
	channel = TerminalAlertChannel(use_color=True)
	print(f"\n[{scenario}] total signals={len(signals)} expected={expected.value} matched={len(matched)}")
	for s in signals:
		channel.send(s)
	return len(matched) >= 1


def main() -> int:
	bearish_ok = _run(
		"BEARISH",
		_candles_from_closes(_bearish_series()),
		DivergenceType.REGULAR_BEARISH,
	)
	bullish_ok = _run(
		"BULLISH",
		_candles_from_closes(_bullish_series()),
		DivergenceType.REGULAR_BULLISH,
	)
	print(
		f"\nResult: bearish={'PASS' if bearish_ok else 'FAIL'}  "
		f"bullish={'PASS' if bullish_ok else 'FAIL'}"
	)
	return 0 if (bearish_ok and bullish_ok) else 1


if __name__ == "__main__":
	sys.exit(main())
