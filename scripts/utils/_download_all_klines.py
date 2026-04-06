"""Download latest kline data for all trading symbols used by backtests."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.data.klines_downloader import load_or_download

symbols = [
    "XRPUSDT", "SOLUSDT", "DOGEUSDT", "ADAUSDT", "HYPEUSDT", "BCHUSDT",
    "LINKUSDT", "XLMUSDT", "AVAXUSDT", "DOTUSDT", "LTCUSDT", "ZECUSDT",
    "SUIUSDT", "TONUSDT", "TAOUSDT", "AAVEUSDT", "ATOMUSDT", "ICPUSDT",
    "ETCUSDT", "ONDOUSDT", "PUMPUSDT", "KASUSDT", "POLUSDT", "WLDUSDT",
    "MORPHOUSDT", "ENAUSDT", "RENDERUSDT", "TRUMPUSDT", "ALGOUSDT", "APTUSDT",
    "FILUSDT", "VETUSDT", "ARBUSDT", "JUPUSDT", "ZROUSDT", "JSTUSDT", "FETUSDT",
]

data_dir = "data/backtest_cache"
os.makedirs(data_dir, exist_ok=True)

timeframes = ["5m", "15m", "1h", "4h"]

for sym in symbols:
    for tf in timeframes:
        print(f"Downloading {sym} {tf}...")
        try:
            load_or_download(sym, tf, 60, data_dir)
            print(f"  OK")
        except Exception as e:
            print(f"  ERR: {e}")

print("\nAll downloads complete. Run _convert_csv_to_parquet.py next.")
