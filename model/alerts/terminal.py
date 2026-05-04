"""Terminal alert channel.

Prints a short, readable alert block: one headline line, a plain-language
summary, the key swing facts, and confirmation time. No decorative rules or
heavy ANSI — only the divergence label is color-highlighted when stdout is a TTY.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

from ..divergence.types import DivergenceSignal, DivergenceType
from .base import AlertChannel

log = logging.getLogger(__name__)

_RESET = "\033[0m"
_BOLD = "\033[1m"
_RED = "\033[31m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"


def _format_ts(ts: int) -> str:
	return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _type_word_color(div_type: DivergenceType, use_color: bool) -> str:
	if not use_color:
		return ""
	if div_type in (
		DivergenceType.REGULAR_BULLISH,
		DivergenceType.HIDDEN_BULLISH,
	):
		return _GREEN
	if div_type in (
		DivergenceType.REGULAR_BEARISH,
		DivergenceType.HIDDEN_BEARISH,
	):
		return _RED
	return ""


def _plain_summary(div_type: DivergenceType) -> str:
	"""One short sentence describing what was observed (not financial advice)."""
	if div_type is DivergenceType.REGULAR_BULLISH:
		return (
			"Price printed a lower low while the indicator printed a higher low "
			"(regular bullish, reversal bias)."
		)
	if div_type is DivergenceType.REGULAR_BEARISH:
		return (
			"Price printed a higher high while the indicator printed a lower high "
			"(regular bearish, reversal bias)."
		)
	if div_type is DivergenceType.HIDDEN_BULLISH:
		return (
			"Price printed a higher low while the indicator printed a lower low "
			"(hidden bullish, continuation bias)."
		)
	return (
		"Price printed a lower high while the indicator printed a higher high "
		"(hidden bearish, continuation bias)."
	)


class TerminalAlertChannel(AlertChannel):
	def __init__(self, use_color: bool | None = None, stream=None) -> None:
		self.stream = stream or sys.stdout
		if use_color is None:
			use_color = bool(getattr(self.stream, "isatty", lambda: False)())
		self.use_color = use_color

	def send(self, signal: DivergenceSignal) -> None:
		try:
			use = self.use_color
			bold = _BOLD if use else ""
			reset = _RESET if use else ""
			yellow = _YELLOW if use else ""
			tcolor = _type_word_color(signal.divergence_type, use)

			bars = signal.pivot2_index - signal.pivot1_index
			conf_pct = max(0.0, min(100.0, signal.confidence * 100.0))

			head = (
				f"{bold}[ALERT]{reset} {tcolor}{bold}{signal.divergence_type.value}{reset}"
				f" | {signal.symbol} | {signal.timeframe} | {yellow}{signal.indicator_name}{reset}"
				f" | confidence {conf_pct:.0f}%"
			)
			summary = _plain_summary(signal.divergence_type)
			metrics = (
				f"Metrics: price {signal.price_delta_pct:+.3f}%  "
				f"{signal.indicator_name} {signal.indicator_delta:+.4f}  "
				f"over {bars} bars between swings."
			)
			swings = (
				f"Swings: {_format_ts(signal.pivot1_timestamp)}  "
				f"price={signal.pivot1_price:.6f}  {signal.indicator_name}={signal.pivot1_indicator:.4f}"
				f"  ->  {_format_ts(signal.pivot2_timestamp)}  "
				f"price={signal.pivot2_price:.6f}  {signal.indicator_name}={signal.pivot2_indicator:.4f}"
			)
			confirm = (
				f"Confirmed on closed bar: {_format_ts(signal.confirmation_timestamp)} "
				f"(bar index {signal.confirmation_index})."
			)

			block = "\n".join([head, summary, metrics, swings, confirm, ""])
			self.stream.write(block)
			self.stream.flush()
		except Exception:
			log.exception("TerminalAlertChannel.send failed")
