"""Convert downloaded CSV klines to parquet format for backtesting."""
import pandas as pd
import os
import glob

data_dir = os.path.join(os.path.dirname(__file__), "data", "backtest_cache")

csv_files = glob.glob(os.path.join(data_dir, "*_60d.csv"))
print(f"Found {len(csv_files)} CSV files to convert")

for csv_path in sorted(csv_files):
    try:
        df = pd.read_csv(csv_path)
        if df.empty:
            os.remove(csv_path)
            continue

        ts_col = None
        for c in ["timestamp", "open_time", "close_time", "date"]:
            if c in df.columns:
                ts_col = c
                break
        if ts_col:
            max_ts = pd.to_datetime(df[ts_col], errors="coerce").max()
            date_suffix = max_ts.strftime("%Y%m%d") if not pd.isna(max_ts) else "unknown"
        else:
            date_suffix = "unknown"

        basename = os.path.basename(csv_path)
        parts = basename.replace("_60d.csv", "").rsplit("_", 1)
        if len(parts) == 2:
            sym, tf = parts
        else:
            continue

        parquet_name = f"{sym}_{tf}_60d_{date_suffix}.parquet"
        parquet_path = os.path.join(data_dir, parquet_name)

        old_pq = glob.glob(os.path.join(data_dir, f"{sym}_{tf}_60d_*.parquet"))
        for old in old_pq:
            os.remove(old)

        df.to_parquet(parquet_path, index=False)
        os.remove(csv_path)
        print(f"  {basename} -> {parquet_name} ({len(df)} rows)")
    except Exception as e:
        print(f"  ERR {csv_path}: {e}")

print("DONE")
