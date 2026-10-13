#!/usr/bin/env python3
"""Log Solana swap quotes once a minute: same pair, same size, same moment for every venue.

Standard library only, no API keys. Venues: Jupiter (lite-api) and Raydium (Trade API).
Swap.io has no public quote API, so its readings are entered by hand (manual.csv) in the same minutes.

    python3 best_swap_price.py collect --minutes 600 --out quotes.csv   # collect (Ctrl+C to stop)
    python3 best_swap_price.py summary quotes.csv                        # win rates, median spread, $ difference
    python3 best_swap_price.py summary quotes.csv --manual manual.csv    # plus manual Swap.io readings

The score is how many tokens you get back for the same amount of USDC (quote output).
"""

import argparse
import csv
import json
import statistics
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
# symbol -> (mint, decimals)
TOKENS = {
    "SOL": ("So11111111111111111111111111111111111111112", 9),
    "WIF": ("EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm", 6),
    "POPCAT": ("7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr", 9),
}
SIZES_USD = [100, 1_000, 10_000]
SLIPPAGE_BPS = 50
FIELDS = ["ts_utc", "venue", "pair", "size_usd", "out_amount", "price_impact_pct", "route", "error"]


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "quote-compare/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def jupiter(out_mint: str, amount: int) -> tuple[int, float | None, str]:
    q = urllib.parse.urlencode({"inputMint": USDC, "outputMint": out_mint, "amount": amount,
                                "slippageBps": SLIPPAGE_BPS, "restrictIntermediateTokens": "true"})
    d = _get(f"https://lite-api.jup.ag/swap/v1/quote?{q}")
    route = "+".join(f"{(p.get('swapInfo') or {}).get('label', '?')}:{p.get('percent', '?')}%"
                     for p in d.get("routePlan") or [])
    impact = d.get("priceImpactPct")
    return int(d["outAmount"]), float(impact) * 100 if impact not in (None, "") else None, route


def raydium(out_mint: str, amount: int) -> tuple[int, float | None, str]:
    q = urllib.parse.urlencode({"inputMint": USDC, "outputMint": out_mint, "amount": amount,
                                "slippageBps": SLIPPAGE_BPS, "txVersion": "V0"})
    d = _get(f"https://transaction-v1.raydium.io/compute/swap-base-in?{q}")
    if not d.get("success"):
        raise RuntimeError(str(d.get("msg") or d)[:120])
    data = d["data"]
    hops = len(data.get("routePlan") or [])
    return int(data["outputAmount"]), data.get("priceImpactPct"), f"raydium:{hops}hop"


VENUES = {"jupiter": jupiter, "raydium": raydium}


def collect(minutes: int, out: str, interval: int) -> None:
    new = False
    try:
        open(out).close()
    except FileNotFoundError:
        new = True
    with open(out, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        for i in range(minutes):
            started = time.monotonic()
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            ok = 0
            for sym, (mint, dec) in TOKENS.items():
                for size in SIZES_USD:
                    for venue, fn in VENUES.items():
                        row = {"ts_utc": ts, "venue": venue, "pair": f"USDC->{sym}", "size_usd": size}
                        try:
                            raw, impact, route = fn(mint, size * 10**6)
                            row.update(out_amount=raw / 10**dec, price_impact_pct=impact, route=route)
                            ok += 1
                        except Exception as e:  # noqa: BLE001
                            row["error"] = f"{type(e).__name__}: {e}"[:150]
                        w.writerow(row)
                        time.sleep(0.4)  # stay well inside free rate limits
            f.flush()
            print(f"{ts} round {i + 1}/{minutes}: {ok}/{len(TOKENS) * len(SIZES_USD) * len(VENUES)} quotes ok",
                  file=sys.stderr)
            time.sleep(max(0.0, interval - (time.monotonic() - started)))


def summary(path: str, manual: str | None) -> None:
    rows = [r for r in csv.DictReader(open(path)) if not r.get("error")]
    by_moment: dict[tuple, dict[str, float]] = defaultdict(dict)
    for r in rows:
        by_moment[(r["ts_utc"], r["pair"], int(r["size_usd"]))][r["venue"]] = float(r["out_amount"])
    stats: dict[tuple, dict] = defaultdict(lambda: {"n": 0, "wins": defaultdict(int), "gap_bps": [], "gap_usd": []})
    for (ts, pair, size), vals in by_moment.items():
        if len(vals) < 2:
            continue
        best_v = max(vals, key=vals.get)
        best, worst = vals[best_v], min(vals.values())
        s = stats[(pair, size)]
        s["n"] += 1
        s["wins"][best_v] += 1
        bps = (best / worst - 1) * 10_000 if worst else 0.0
        s["gap_bps"].append(bps)
        s["gap_usd"].append(size * bps / 10_000)
    first = min((k[0] for k in by_moment), default="-")
    last = max((k[0] for k in by_moment), default="-")
    print(f"Samples: {len({k[0] for k in by_moment})} moments, {first} to {last} UTC\n")
    print(f"{'pair':<14}{'size $':>8}{'n':>6}  {'best quote (share of moments)':<30}{'median gap':>14}{'max':>10}"
          f"{'median $':>10}")
    for (pair, size), s in sorted(stats.items()):
        wins = ", ".join(f"{v} {c * 100 // s['n']}%" for v, c in sorted(s["wins"].items(), key=lambda x: -x[1]))
        print(f"{pair:<14}{size:>8}{s['n']:>6}  {wins:<30}{statistics.median(s['gap_bps']):>10.1f} bps"
              f"{max(s['gap_bps']):>10.1f}{statistics.median(s['gap_usd']):>10.2f}")
    print("\nbps = basis point, 0.01%. Gap = how much more the best quote pays than the worst one at the same moment.")
    if manual:
        _manual_vs_script(manual, by_moment)


def _manual_vs_script(manual: str, by_moment: dict) -> None:
    """Manual Swap.io readings against the script's nearest sample for the same pair and size."""
    print("\nSwap.io (manual) vs script quotes from the same minute:")
    index = defaultdict(list)
    for (ts, pair, size), vals in by_moment.items():
        index[(pair, size)].append((datetime.strptime(ts, "%Y-%m-%d %H:%M:%S"), vals))
    for r in csv.DictReader(open(manual)):
        if not r.get("swapio_out"):
            continue
        t = datetime.strptime(r["ts_utc"], "%Y-%m-%d %H:%M")
        cands = index.get((r["pair"], int(r["size_usd"])))
        if not cands:
            print(f"  {r['ts_utc']} {r['pair']} ${r['size_usd']}: no script samples")
            continue
        near_t, vals = min(cands, key=lambda c: abs((c[0] - t).total_seconds()))
        sw = float(r["swapio_out"])
        cmp = ", ".join(f"{v} {sw / x * 100 - 100:+.3f}%" for v, x in vals.items())
        print(f"  {r['ts_utc']} {r['pair']} ${r['size_usd']}: swap.io {sw:g} ({cmp}; "
              f"script sample at {near_t:%H:%M:%S}, {abs((near_t - t).total_seconds()):.0f}s apart)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--minutes", type=int, default=600, help="number of rounds (one every --interval seconds)")
    c.add_argument("--interval", type=int, default=60)
    c.add_argument("--out", default="quotes.csv")
    s = sub.add_parser("summary")
    s.add_argument("path")
    s.add_argument("--manual", help="CSV with manual Swap.io readings (see manual_template.csv)")
    a = ap.parse_args(argv)
    if a.cmd == "collect":
        collect(a.minutes, a.out, a.interval)
    else:
        summary(a.path, a.manual)


if __name__ == "__main__":
    main()
