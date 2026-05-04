"""Indicator abstraction.

Every indicator implements `compute(closes, highs=None, lows=None, opens=None)`
and returns a 1D numpy array aligned 1:1 with the input closes (NaN for
warm-up bars). This contract lets the divergence engine treat any indicator
uniformly, so adding new ones (Stoch, CCI, OBV, …) is a drop-in change.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

import numpy as np


class IndicatorProvider(ABC):
	#: Human-readable name used in alerts/logs (e.g. "RSI", "MACD-Hist").
	name: str

	@property
	def warmup(self) -> int:
		"""Bars required before the indicator returns finite values."""
		return 0

	@abstractmethod
	def compute(
		self,
		closes: np.ndarray,
		highs: Optional[np.ndarray] = None,
		lows: Optional[np.ndarray] = None,
		opens: Optional[np.ndarray] = None,
	) -> np.ndarray:
		"""Return a 1D float array, same length as `closes`, NaN for warm-up."""
		raise NotImplementedError
