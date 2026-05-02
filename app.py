from model.data_fetcher import fetch_mexc_futures_ohlc
import time


def main() -> None:
	# Example input values
	# Last 2 hours in Unix seconds
	now = int(time.time())
	timeperiod = (now - 2 * 60 * 60, now)
	time_frame = "Min15"
	coin = ["BTC", "ETH"]

	try:
		data = fetch_mexc_futures_ohlc(
			timeperiod=timeperiod,
			time_frame=time_frame,
			coin=coin,
		)
	except Exception as exc:
		print(f"Failed to fetch OHLC data: {exc}")
		return

	for symbol, candles in data.items():
		print(f"\n{symbol} ({len(candles)} candles)")
		for candle in candles:
			print(candle)


if __name__ == "__main__":
	main()

