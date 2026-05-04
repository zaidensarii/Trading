"""Confirmation-lagged pivot (swing) detection.

A pivot high at index `i` is the maximum within window `[i-left, i+right]`,
strict on the left, non-strict on the right (so equal-value plateaus don't
hide a real swing). A pivot low is the symmetric definition.

Because we require `right` bars *after* the pivot, the most recent confirmable
pivot is at index `n - 1 - right`. This is exactly what you want in real-time:
it guarantees a pivot won't be revised after the fact.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal

import numpy as np


@dataclass(frozen=True)
class Pivot:
	index: int
	value: float
	kind: Literal["high", "low"]


def find_pivots(
	values: np.ndarray,
	left: int = 3,
	right: int = 3,
	mode: Literal["high", "low"] = "high",
) -> List[Pivot]:
	"""Return all confirmed pivots of the requested kind, oldest -> newest.

	Args:
		values: 1D array of prices (highs for `high` mode, lows for `low`).
				NaNs are treated as non-pivots.
		left:   Bars to the left required to be strictly less (high) or strictly
				greater (low) than the candidate.
		right:  Bars to the right required to be less-or-equal (high) or
				greater-or-equal (low) than the candidate. This is the
				confirmation lag.
		mode:   "high" or "low".
	"""
	if left < 1 or right < 1:
		raise ValueError("left and right must both be >= 1.")
	if mode not in ("high", "low"):
		raise ValueError("mode must be 'high' or 'low'.")

	values = np.asarray(values, dtype=np.float64)
	n = values.shape[0]
	pivots: List[Pivot] = []
	if n < left + right + 1:
		return pivots

	for i in range(left, n - right):
		v = values[i]
		if not np.isfinite(v):
			continue

		left_window = values[i - left : i]
		right_window = values[i + 1 : i + 1 + right]
		if not np.all(np.isfinite(left_window)) or not np.all(np.isfinite(right_window)):
			continue

		if mode == "high":
			if np.all(left_window < v) and np.all(right_window <= v):
				pivots.append(Pivot(index=i, value=float(v), kind="high"))
		else:
			if np.all(left_window > v) and np.all(right_window >= v):
				pivots.append(Pivot(index=i, value=float(v), kind="low"))

	return pivots
