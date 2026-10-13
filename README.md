# best-swap-price

Does it matter where you swap on Solana? This script answers it with data instead of a few screenshots.

Every minute it asks several venues for a quote on **the same pair, the same size, at the same moment**, and logs
how many tokens each one would give you back. Then it summarises who wins, how often, and by how much.

- Python 3.10+, standard library only. No API keys, no dependencies.
- Venues: **Jupiter** (aggregator, `lite-api.jup.ag`) and **Raydium** (single DEX, Trade API).
- Pairs: USDC → SOL, WIF, POPCAT. Sizes: $100, $1,000, $10,000. Easy to change at the top of the file.
- Venues without a public quote API (for example Swap.io or the Jupiter web app) can be added by hand in the same
  minutes and compared against the script's quotes.

## Quick start

```bash
# collect: one round per minute, ~18 quotes per round (Ctrl+C to stop; the CSV is appended, not overwritten)
python3 best_swap_price.py collect --minutes 600 --out quotes.csv

# summary: win rates, median and max gap, gap in dollars
python3 best_swap_price.py summary quotes.csv
```

Run it in the background on a server:

```bash
nohup python3 best_swap_price.py collect --minutes 900 --out quotes.csv > collect.log 2>&1 &
tail -f collect.log   # "round N/900: 18/18 quotes ok"
```

## Reading the summary

```
pair            size $     n  best quote (share of moments)     median gap       max  median $
USDC->SOL          100   147  jupiter 97%, raydium 2%              3.0 bps       6.5      0.03
USDC->SOL        10000   148  jupiter 100%                         5.8 bps      10.3      5.77
USDC->POPCAT     10000   143  jupiter 100%                        13.4 bps      21.9     13.42
```

- **n**: moments where every venue answered.
- **best quote**: which venue gave the most tokens, as a share of moments.
- **median gap**: how much more the best quote paid than the worst one at the same moment, in basis points
  (1 bp = 0.01%). **median $** is the same gap in dollars for that swap size.

## Adding venues by hand

Some venues only show quotes in their web app. Put them on one screen next to the others, refresh all of them
together, screenshot, and write the numbers into a copy of `manual_template.csv`:

| column | meaning |
|---|---|
| `ts_utc` | minute of the screenshot, `YYYY-MM-DD HH:MM` (UTC) |
| `pair`, `size_usd` | must match the script, e.g. `USDC->SOL`, `10000` |
| `swapio_out` | tokens the manual venue would give you |
| `swapio_route`, `screenshot`, `notes` | free text |

```bash
python3 best_swap_price.py summary quotes.csv --manual manual.csv
```

Each manual reading is compared with the script's quotes from the nearest minute for the same pair and size.

## Findings from one run (Oct 13, 2026, 2.5 hours, ~2,800 quotes)

- **Small swaps don't care where you go.** At $100 the gap between venues was 2–17 cents.
- **The gap grows with size and thinner liquidity.** At $10,000 the aggregator beat the single DEX by a median of
  $5.8 on SOL, $10.6 on WIF and $13.4 on POPCAT, and won every sampled minute at that size.
- **A single DEX pays for hops and price impact.** In our samples Raydium routed WIF through SOL (two pool fees), and its price
  impact on a $10k WIF swap was 0.37% versus 0.03% on SOL.

Quotes are not fills: the real result also depends on slippage between quote and execution, priority fees and
network conditions. Treat this as a fair comparison of venues at the same moment, not as an execution guarantee.

## Customising

Edit the constants at the top of `best_swap_price.py`:

- `TOKENS`: symbol → (mint, decimals);
- `SIZES_USD`: swap sizes in USDC;
- `VENUES`: name → function `(output_mint, raw_amount) -> (raw_out, price_impact_pct, route)`. Adding a venue is
  one function.
