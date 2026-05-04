"""Trading bot entrypoint — real-time divergence scanner.

Usage:
	python app.py                         # defaults: BTC_USDT, Min10, RSI, 100 candles
	python app.py --symbol ETH_USDT --time-frame Min5
	python app.py --symbols BTC_USDT,ETH_USDT --indicators RSI,MACD --hidden
	python app.py --once                  # run a single scan and exit
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from typing import List

from model.alerts import TerminalAlertChannel
from model.config import AppConfig, DivergenceConfig, StreamingConfig
from model.data_fetcher import fetch_mexc_futures_ohlc
from model.divergence import DivergenceDetector
from model.indicators import MACDIndicator, RSIIndicator
from model.streaming import CandlePoller


def _parse_args() -> argparse.Namespace:
	p = argparse.ArgumentParser(description="MEXC divergence scanner")
	p.add_argument("--symbol", help="Single symbol shortcut, e.g. BTC_USDT")
	p.add_argument("--symbols", help="Comma-separated list, e.g. BTC_USDT,ETH_USDT")
	p.add_argument(
		"--time-frame",
		default="Min10",
		help="Candle interval (Min10 is synthesized from Min5 on MEXC)",
	)
	p.add_argument("--num-candles", type=int, default=100)
	p.add_argument(
		"--indicators",
		default="RSI",
		help="Comma-separated indicators (RSI, MACD).",
	)
	p.add_argument("--hidden", action="store_true", help="Also detect hidden divergences.")
	p.add_argument("--once", action="store_true", help="Single scan, no loop.")
	p.add_argument("--log-level", default="INFO")
	return p.parse_args()


def _resolve_symbols(args: argparse.Namespace) -> List[str]:
	if args.symbols:
		return [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
	if args.symbol:
		return [args.symbol.strip().upper()]
	return ["BTC_USDT"]


def _build_detectors(
	indicator_names: List[str], div_cfg: DivergenceConfig
) -> List[DivergenceDetector]:
	detectors: List[DivergenceDetector] = []
	for name in indicator_names:
		key = name.strip().upper()
		if key == "RSI":
			detectors.append(
				DivergenceDetector(RSIIndicator(period=div_cfg.rsi_period), div_cfg)
			)
		elif key == "MACD":
			detectors.append(
				DivergenceDetector(
					MACDIndicator(
						fast=div_cfg.macd_fast,
						slow=div_cfg.macd_slow,
						signal=div_cfg.macd_signal,
					),
					div_cfg,
				)
			)
		else:
			raise ValueError(f"Unsupported indicator '{name}'. Use RSI or MACD.")
	return detectors


def _make_fetcher():
	"""Adapt the existing data_fetcher to the (symbol, tf, n) signature."""

	def _fetch(symbol: str, time_frame: str, num_candles: int):
		# Pull a window comfortably larger than `num_candles` to ensure the
		# warm-up bars are present after MEXC trims to its server-side cap.
		now = int(time.time())
		from model.config import interval_to_seconds

		seconds = interval_to_seconds(time_frame)
		# Fetch window = num_candles * seconds + small overhead.
		start = now - (num_candles + 5) * seconds
		data = fetch_mexc_futures_ohlc(
			timeperiod=(start, now),
			time_frame=time_frame,
			coin=[symbol],
		)
		# Normalize to first matched key (data_fetcher upper-cases symbols).
		if symbol in data:
			candles = data[symbol]
		elif data:
			candles = next(iter(data.values()))
		else:
			candles = []
		return candles[-num_candles:]

	return _fetch


def _run_once(
	symbol: str,
	time_frame: str,
	num_candles: int,
	detectors,
	channels,
) -> None:
	fetch = _make_fetcher()
	candles = fetch(symbol, time_frame, num_candles)
	logging.info("Fetched %d candles for %s %s", len(candles), symbol, time_frame)
	for det in detectors:
		signals = det.detect(candles, symbol, time_frame)
		if not signals:
			logging.info("No %s divergences for %s %s", det.indicator.name, symbol, time_frame)
		for sig in signals:
			for ch in channels:
				ch.send(sig)


def _run_streaming(app_cfg: AppConfig, detectors_per_symbol, channels) -> None:
	"""One poller per symbol, each in its own thread."""
	threads: List[threading.Thread] = []
	pollers: List[CandlePoller] = []

	for symbol in app_cfg.symbols:
		stream_cfg = StreamingConfig(
			symbol=symbol,
			time_frame=app_cfg.time_frame,
			num_candles=app_cfg.num_candles,
		)
		poller = CandlePoller(
			config=stream_cfg,
			fetch_callable=_make_fetcher(),
			detectors=detectors_per_symbol,
			channels=channels,
		)
		pollers.append(poller)
		t = threading.Thread(target=poller.run, name=f"poller-{symbol}", daemon=True)
		threads.append(t)

	logging.info(
		"Streaming %s on %s (%s). You should see an OK line after each poll; Ctrl+C to stop.",
		", ".join(app_cfg.symbols),
		app_cfg.time_frame,
		"/".join(app_cfg.indicators),
	)

	# Install signal handlers in main thread; they'll stop all pollers.
	import signal as os_signal

	stop_event = threading.Event()

	def _handler(signum, frame):  # noqa: ANN001
		logging.info("Signal %s received — shutting down …", signum)
		for p in pollers:
			p.stop()
		stop_event.set()

	os_signal.signal(os_signal.SIGINT, _handler)
	os_signal.signal(os_signal.SIGTERM, _handler)

	for t in threads:
		t.start()
	try:
		while not stop_event.is_set() and any(t.is_alive() for t in threads):
			stop_event.wait(timeout=1.0)
	finally:
		for p in pollers:
			p.stop()
		for t in threads:
			t.join(timeout=5.0)
		logging.info("All pollers stopped.")


def main() -> int:
	args = _parse_args()
	logging.basicConfig(
		level=getattr(logging, args.log_level.upper(), logging.INFO),
		format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
	)

	div_cfg = DivergenceConfig(enable_hidden=args.hidden)
	indicators = [s for s in args.indicators.split(",") if s.strip()]
	app_cfg = AppConfig(
		symbols=_resolve_symbols(args),
		time_frame=args.time_frame,
		num_candles=args.num_candles,
		indicators=tuple(indicators),
		divergence=div_cfg,
		log_level=args.log_level,
	)

	detectors = _build_detectors(indicators, div_cfg)
	channels = [TerminalAlertChannel()]

	if args.once:
		logging.info(
			"Scan: symbols=%s tf=%s candles=%d indicators=%s hidden=%s",
			app_cfg.symbols,
			app_cfg.time_frame,
			app_cfg.num_candles,
			app_cfg.indicators,
			args.hidden,
		)
	else:
		logging.debug(
			"Config: symbols=%s tf=%s candles=%d indicators=%s hidden=%s",
			app_cfg.symbols,
			app_cfg.time_frame,
			app_cfg.num_candles,
			app_cfg.indicators,
			args.hidden,
		)

	try:
		if args.once:
			for symbol in app_cfg.symbols:
				_run_once(symbol, app_cfg.time_frame, app_cfg.num_candles, detectors, channels)
		else:
			_run_streaming(app_cfg, detectors, channels)
	except KeyboardInterrupt:
		logging.info("Interrupted by user.")
	except Exception:
		logging.exception("Fatal error")
		return 1
	return 0


if __name__ == "__main__":
	sys.exit(main())
