"""Candle-close-aligned poller for real-time divergence scanning.

Why polling vs WebSocket?
	- The existing `data_fetcher` is REST. Aligning the poll to the candle
	  close means we still get sub-second latency between close and analysis,
	  without taking on a websocket dependency or the reconnect logic that
	  comes with it. WebSocket can be added as a second source by swapping
	  the `fetch_callable`.

The poller:
	1. Sleeps until the next candle close + a small buffer.
	2. Fetches the most recent N candles.
	3. Drops the still-forming current candle (only closed bars feed the detector).
	4. Runs every detector and dispatches *new* signals to every channel.
	5. Tracks already-emitted signals by `signal_id` so the same divergence is
	   not re-alerted on subsequent polls.
"""
from __future__ import annotations

import logging
import signal as os_signal
import time
from datetime import datetime, timezone
from typing import Callable, Dict, List, Sequence

from ..config import StreamingConfig, interval_to_seconds
from ..divergence.detector import DivergenceDetector
from ..divergence.types import DivergenceSignal

log = logging.getLogger(__name__)


CandleFetcher = Callable[[str, str, int], List[Dict[str, float]]]
"""Signature: (symbol, time_frame, num_candles) -> list[ohlc dict]."""


def _utc_str(ts: int) -> str:
	return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


class CandlePoller:
	def __init__(
		self,
		config: StreamingConfig,
		fetch_callable: CandleFetcher,
		detectors: Sequence[DivergenceDetector],
		signal_cache_size: int = 2048,
	) -> None:
		if not detectors:
			raise ValueError("At least one detector is required.")
		self.config = config
		self.fetch_callable = fetch_callable
		self.detectors = list(detectors)
		self._seen_signals: dict[str, int] = {}  # signal_id -> first-seen wall time
		self._seen_cache_size = signal_cache_size
		self._stop = False
		self._consecutive_errors = 0

	# ----------------------------------------------------------------- lifecycle

	def stop(self) -> None:
		self._stop = True

	def install_signal_handlers(self) -> None:
		def _handler(signum, frame):  # noqa: ANN001
			log.info("Received signal %s, stopping poller …", signum)
			self.stop()

		try:
			os_signal.signal(os_signal.SIGINT, _handler)
			os_signal.signal(os_signal.SIGTERM, _handler)
		except ValueError:
			# Not the main thread — caller can stop() manually.
			pass

	# ----------------------------------------------------------------- main loop

	def run(self) -> None:
		cfg = self.config
		interval_s = interval_to_seconds(cfg.time_frame)
		log.debug(
			"Poller started: symbol=%s timeframe=%s candles=%d interval=%ds",
			cfg.symbol,
			cfg.time_frame,
			cfg.num_candles,
			interval_s,
		)

		first_cycle = True
		while not self._stop:
			if not first_cycle:
				sleep_for = self._seconds_until_next_poll(interval_s)
				log.debug(
					"%s %s: sleeping %.1fs until next poll",
					cfg.symbol, cfg.time_frame, sleep_for,
				)
				self._interruptible_sleep(sleep_for)
				if self._stop:
					break
			first_cycle = False

			try:
				candles = self._safe_fetch()
			except Exception as exc:
				self._on_error(exc)
				continue

			# Drop the (likely) still-forming current candle. We only emit on
			# fully-closed bars to avoid repaint.
			closed = self._only_closed_candles(candles, interval_s)
			if not closed:
				if candles:
					raw_ts = int(candles[-1]["timestamp"])
					log.warning(
						"%s %s: fetched %d candles but none are fully closed yet "
						"(last open %s, need close >= %s). Retrying on next cycle.",
						cfg.symbol,
						cfg.time_frame,
						len(candles),
						_utc_str(raw_ts),
						_utc_str(raw_ts + interval_s),
					)
				else:
					log.warning("%s %s: fetch returned zero candles.", cfg.symbol, cfg.time_frame)
				continue

			self._consecutive_errors = 0  # success resets backoff
			n_new = self._run_detectors(closed)
			last = closed[-1]
			ind_names = ", ".join(d.indicator.name for d in self.detectors)
			if n_new == 0:
				log.info(
					"OK | %s | %s | analyzed %d closed bars (last close %s @ %.6f) | %s | no new divergence",
					cfg.symbol,
					cfg.time_frame,
					len(closed),
					_utc_str(int(last["timestamp"]) + interval_s),
					float(last["close"]),
					ind_names,
				)
			else:
				log.info(
					"OK | %s | %s | analyzed %d closed bars | %s | %d new divergence signal(s)",
					cfg.symbol,
					cfg.time_frame,
					len(closed),
					ind_names,
					n_new,
				)

	# ------------------------------------------------------------------ internal

	def _safe_fetch(self) -> List[Dict[str, float]]:
		cfg = self.config
		fetched = self.fetch_callable(cfg.symbol, cfg.time_frame, cfg.num_candles)
		if not isinstance(fetched, list):
			raise RuntimeError(
				f"fetch_callable returned {type(fetched).__name__}, expected list."
			)
		return fetched

	def _only_closed_candles(
		self, candles: List[Dict[str, float]], interval_s: int
	) -> List[Dict[str, float]]:
		if not candles:
			return candles
		now = int(time.time())
		# A candle is closed once `now >= timestamp + interval`. MEXC timestamps
		# are the candle's open time in seconds.
		return [c for c in candles if int(c["timestamp"]) + interval_s <= now]

	def _run_detectors(self, candles: List[Dict[str, float]]) -> int:
		"""Run detectors; return count of *new* signals emitted this cycle."""
		cfg = self.config
		latest_close_ts = int(candles[-1]["timestamp"])
		latest_close_price = float(candles[-1]["close"])
		log.debug(
			"Analyzing %s %s | %d closed candles | last open ts=%d price=%.6f",
			cfg.symbol,
			cfg.time_frame,
			len(candles),
			latest_close_ts,
			latest_close_price,
		)

		emitted = 0
		for detector in self.detectors:
			try:
				signals = detector.detect(candles, cfg.symbol, cfg.time_frame)
			except Exception:
				log.exception(
					"Detector %s failed on %s %s",
					detector.indicator.name, cfg.symbol, cfg.time_frame,
				)
				continue
			for sig in signals:
				if self._emit(sig):
					emitted += 1
		return emitted

	def _emit(self, signal: DivergenceSignal) -> bool:
		"""Return True if this was a new signal (alert dispatched)."""
		key = signal.signal_id()
		if key in self._seen_signals:
			return False
		self._remember(key)
		log.debug(
			"New divergence: %s %s %s conf=%.2f",
			signal.symbol,
			signal.divergence_type.value,
			signal.indicator_name,
			signal.confidence,
		)
		log.info(
			"SIGNAL | %s | %s | %s | %s | confidence %.0f%%",
			signal.symbol,
			signal.timeframe,
			signal.divergence_type.value,
			signal.indicator_name,
			signal.confidence * 100.0,
		)
		return True

	def _remember(self, key: str) -> None:
		if len(self._seen_signals) >= self._seen_cache_size:
			# Drop the oldest half — keeps the dict bounded under long uptime.
			oldest = sorted(self._seen_signals.items(), key=lambda kv: kv[1])
			for k, _ in oldest[: self._seen_cache_size // 2]:
				self._seen_signals.pop(k, None)
		self._seen_signals[key] = int(time.time())

	def _seconds_until_next_poll(self, interval_s: int) -> float:
		cfg = self.config
		now = time.time()
		# Next candle close in absolute Unix seconds.
		next_close = (int(now) // interval_s + 1) * interval_s
		until_close = max(next_close - now, 0.0) + cfg.close_buffer_seconds

		# Apply backoff if we're in an error streak.
		if self._consecutive_errors > 0:
			backoff = min(
				cfg.error_backoff_base * (2 ** (self._consecutive_errors - 1)),
				cfg.error_backoff_cap,
			)
			until_close = max(until_close, backoff)

		# Cap so very long timeframes still refresh occasionally.
		until_close = min(until_close, cfg.max_poll_interval_seconds)
		until_close = max(until_close, cfg.min_poll_interval_seconds)
		return until_close

	def _interruptible_sleep(self, seconds: float) -> None:
		# Sleep in 250ms slices so SIGINT is responsive.
		end = time.monotonic() + seconds
		while not self._stop:
			remaining = end - time.monotonic()
			if remaining <= 0:
				return
			time.sleep(min(0.25, remaining))

	def _on_error(self, exc: Exception) -> None:
		cfg = self.config
		self._consecutive_errors += 1
		log.warning(
			"Fetch error #%d for %s %s: %s",
			self._consecutive_errors, cfg.symbol, cfg.time_frame, exc,
		)
		if self._consecutive_errors >= cfg.max_consecutive_errors:
			log.error(
				"Aborting poller after %d consecutive errors", self._consecutive_errors
			)
			raise exc
