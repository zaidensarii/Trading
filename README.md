# Trading — MEXC divergence scanner

Python tool that polls **MEXC futures** OHLC data, computes **RSI**, and emits **bullish and bearish** divergence signals with terminal alerts. Intended as a modular base you can extend with Slack, Telegram, webhooks, or websocket feeds.

> **Disclaimer:** This project is educational software only. Divergence signals are heuristics, not financial advice. You are solely responsible for any trading decisions.

---

## Highlights

- **Streaming-aware polling** — aligned to candle close, backoff on errors, and a hard cap on consecutive failures.
- **RSI divergence engine** — pluggable `IndicatorProvider` interface for adding indicators.
- **Bullish & bearish** divergences — confirmation thresholds (RSI zones), pivot-distance filters, magnitude noise floors, and a soft confidence score.
- **Non-repainting pivots** — confirmation lag built into pivot detection.
- **TA-Lib when installed** — vectorized NumPy fallback if TA-Lib is missing.
- **Pluggable alerts** — `TerminalAlertChannel` included; subclass `AlertChannel` for other destinations.
- **Multi-symbol streaming** — one thread per symbol, graceful **SIGINT** / **SIGTERM** shutdown.

## Requirements

- **Python 3.10+** (type syntax and modern stdlib expectations).
- Dependencies: see [`requirements.txt`](requirements.txt) (`numpy`, `certifi`). No API keys are required for public MEXC OHLC endpoints used by the fetcher — verify current MEXC terms and limits for your use case.

## Project layout

```
model/
├── data_fetcher.py    # MEXC futures OHLC
├── config.py          # AppConfig, DivergenceConfig, StreamingConfig, intervals
├── indicators/       # IndicatorProvider, RSI
├── divergence/       # Pivots, types, DivergenceDetector
├── alerts/           # AlertChannel, terminal channel
└── streaming/        # CandlePoller (close-aligned)
app.py                # CLI entrypoint
tests/
└── test_divergence_smoke.py
```

## Install

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Optional (recommended for TA-Lib parity and speed):

```bash
# macOS
brew install ta-lib
pip install TA-Lib==0.4.32

# Debian/Ubuntu (package name may vary)
# sudo apt-get install -y libta-lib-dev
# pip install TA-Lib==0.4.32
```

If TA-Lib is not installed, indicators use pure NumPy implementations.

## Usage

```bash
# Real-time streaming: default BTC_USDT, Min10, RSI, 100 candles
python app.py

# Multiple symbols; RSI only
python app.py --symbols BTC_USDT,ETH_USDT --time-frame Min5 \
  --indicators RSI

# Single scan (no loop) — handy for cron or CI
python app.py --once

# Verbosity
python app.py --log-level DEBUG
```

### CLI reference

| Option | Description |
|--------|-------------|
| `--symbol` | Single pair, e.g. `BTC_USDT`. |
| `--symbols` | Comma-separated list; overrides `--symbol`. |
| `--time-frame` | Candle interval (`Min1`, `Min5`, `Min10`, `Min15`, …). Default: `Min10`. **Note:** `Min10` is synthesized from `Min5` where MEXC has no native 10m futures kline. |
| `--num-candles` | Bars to fetch per poll. Default: `100`. |
| `--indicators` | Comma-separated: `RSI`. Default: `RSI`. |
| `--once` | Run one fetch + detect cycle per symbol, then exit. |
| `--log-level` | Logging level (e.g. `INFO`, `DEBUG`). |

## Tests

Offline smoke tests for the divergence engine (synthetic candles):

```bash
python -m tests.test_divergence_smoke
```

## Tuning

All detector and streaming knobs are in [`model/config.py`](model/config.py): `DivergenceConfig` and `StreamingConfig`. Defaults skew conservative (precision over recall). High-impact parameters:

| Parameter | Effect |
|-----------|--------|
| `pivot_left` / `pivot_right` | Larger → fewer false pivots, more confirmation lag. |
| `min_pivot_distance` | Ignores divergences across very nearby swings. |
| `max_pivot_distance` | Maximum bar distance between pivots in a pair. |
| `min_price_diff_pct` | Noise floor on the price leg. |
| `min_indicator_diff` | Noise floor on the indicator leg (e.g. RSI points). |
| `rsi_overbought` / `rsi_oversold` | Optional exhaustion zones for bearish/bullish RSI confirmation. |

## Extending

1. **New indicator** — implement `IndicatorProvider` under `model/indicators/`, wire it in `_build_detectors()` in [`app.py`](app.py).
2. **New alert channel** — subclass `AlertChannel` and append instances to `channels` in [`app.py`](app.py).
3. **Live websocket feed** — replace or wrap `_make_fetcher()` with a callable that returns the same candle list shape the poller expects.

## License

Add a `LICENSE` file in this repository if you plan to publish on GitHub; this README does not impose one by default.
