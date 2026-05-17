"""Public types for divergence signals."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from enum import Enum
from typing import Any, Dict


class DivergenceType(str, Enum):
	BULLISH = "Bullish"
	BEARISH = "Bearish"

	@property
	def is_bullish(self) -> bool:
		return self is DivergenceType.BULLISH


@dataclass(frozen=True)
class DivergenceSignal:
	"""A single, fully-qualified divergence event.

	Designed to be the canonical payload sent to every alert channel and
	suitable for direct serialization to JSON (for webhooks, files, queues).
	"""

	symbol: str
	timeframe: str
	indicator_name: str
	divergence_type: DivergenceType

	# Older pivot.
	pivot1_index: int
	pivot1_timestamp: int
	pivot1_price: float
	pivot1_indicator: float

	# Newer pivot (the one that just confirmed).
	pivot2_index: int
	pivot2_timestamp: int
	pivot2_price: float
	pivot2_indicator: float

	# Bar at which the signal became actionable (i.e. when the newer pivot
	# was confirmed). May lag pivot2 by `pivot_right` bars.
	confirmation_index: int
	confirmation_timestamp: int

	# Magnitudes — useful for downstream ranking / position-sizing.
	price_delta_pct: float
	indicator_delta: float

	# Soft confidence in [0, 1] — combines magnitude and confirmation thresholds.
	confidence: float

	def to_dict(self) -> Dict[str, Any]:
		d = asdict(self)
		d["divergence_type"] = self.divergence_type.value
		return d

	def signal_id(self) -> str:
		"""Stable identity used to deduplicate alerts."""
		return (
			f"{self.symbol}|{self.timeframe}|{self.indicator_name}|"
			f"{self.divergence_type.value}|{self.pivot1_timestamp}|"
			f"{self.pivot2_timestamp}"
		)
