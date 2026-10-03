"""
Download raw historical TVL for every chain on DeFiLlama.

Run:   python download_defillama.py
Needs: pip install requests pandas

Output (all RAW, nothing cleaned on purpose):
    data/raw/chains_list.csv        one row per chain (name, tvl, chainId, ...)
    data/raw/chain_tvl_daily.csv    long table: chain, date (unix seconds), tvl
    data/raw/pull_metadata.json     when you pulled it (put this date in your README)
    data/raw/failed_chains.txt      chains that could not be downloaded (if any)

Design notes:
  * We download ALL chains, not just EVM. Deciding which chains are EVM and
    which to keep is YOUR cleaning task.
  * Full history per chain is kept. Trimming to your date window is also
    a cleaning step.
  * Each chain is cached as its own JSON file, so if the script stops halfway
    you can re-run it and it resumes instead of starting over.
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests

BASE_URL = "https://api.llama.fi"
RAW_DIR = Path("data/raw")
CACHE_DIR = RAW_DIR / "chain_cache"
PAUSE_SECONDS = 0.5      # polite delay between requests
MAX_RETRIES = 5
TIMEOUT = 30             # seconds to wait for a response before giving up

RAW_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

session = requests.Session()  # reuses the connection, faster than requests.get() each time


def get_json(path: str):
    """GET BASE_URL + path and return parsed JSON, retrying on failure."""
    url = BASE_URL + path
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=TIMEOUT)
            if resp.status_code == 200:
                return resp.json()
            # 429 = rate limited, 5xx = server trouble: worth retrying
            if resp.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"status {resp.status_code}")
            # Anything else (404 etc.) will not fix itself, so stop retrying
            print(f"  {url} -> status {resp.status_code}, not retrying")
            return None
        except (requests.RequestException, ValueError) as err:
            wait = 2 ** attempt  # exponential backoff: 2, 4, 8, 16, 32 seconds
            print(f"  attempt {attempt}/{MAX_RETRIES} failed ({err}); waiting {wait}s")
            time.sleep(wait)
    return None


def main():
    # 1) List of all chains -------------------------------------------------
    print("Fetching chain list...")
    chains = get_json("/v2/chains")
    if not chains:
        raise SystemExit("Could not fetch chain list. Check your internet and re-run.")

    chains_df = pd.DataFrame(chains)
    chains_df.to_csv(RAW_DIR / "chains_list.csv", index=False)
    print(f"Found {len(chains_df)} chains. Columns: {list(chains_df.columns)}")

    # 2) Historical TVL per chain ------------------------------------------
    failed = []
    names = chains_df["name"].dropna().tolist()

    for i, name in enumerate(names, start=1):
        cache_file = CACHE_DIR / (name.replace("/", "_").replace(" ", "_") + ".json")

        if cache_file.exists():  # already downloaded on a previous run
            continue

        print(f"[{i}/{len(names)}] {name}")
        # quote() makes names URL-safe, e.g. "Polygon zkEVM" -> "Polygon%20zkEVM"
        data = get_json(f"/v2/historicalChainTvl/{quote(name, safe='')}")

        if data is None:
            failed.append(name)
        else:
            cache_file.write_text(json.dumps(data))

        time.sleep(PAUSE_SECONDS)

    # 3) Stack all cached files into one long table -------------------------
    frames = []
    for name in names:
        cache_file = CACHE_DIR / (name.replace("/", "_").replace(" ", "_") + ".json")
        if not cache_file.exists():
            continue
        records = json.loads(cache_file.read_text())
        if not records:  # some chains return an empty list
            continue
        part = pd.DataFrame(records)   # columns: date, tvl
        part["chain"] = name
        frames.append(part)

    daily = pd.concat(frames, ignore_index=True)[["chain", "date", "tvl"]]
    daily.to_csv(RAW_DIR / "chain_tvl_daily.csv", index=False)

    # 4) Metadata so the analysis is reproducible ---------------------------
    meta = {
        "pulled_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": BASE_URL,
        "endpoints": ["/v2/chains", "/v2/historicalChainTvl/{chain}"],
        "chains_listed": len(names),
        "chains_downloaded": daily["chain"].nunique(),
        "rows": len(daily),
    }
    (RAW_DIR / "pull_metadata.json").write_text(json.dumps(meta, indent=2))

    if failed:
        (RAW_DIR / "failed_chains.txt").write_text("\n".join(failed))
        print(f"\n{len(failed)} chains failed. See failed_chains.txt, then re-run to retry them.")

    print(f"\nDone. {meta['rows']:,} rows across {meta['chains_downloaded']} chains.")
    print(f"Pulled at {meta['pulled_at_utc']}")


if __name__ == "__main__":
    main()
