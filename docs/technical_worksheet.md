# Technical Screening Worksheet

This is the current stock-level technical score. It is intentionally simple and built around four buckets: DeMark, Momentum, Price, and Moving Average.

## Current Draft Weights

| Component | Weight | Why it matters |
| --- | ---: | --- |
| DeMark | 10 | Uses the selected ETF's DeMark score from the market backdrop. |
| Momentum | 70 | Checks whether the stock is aligned above key long-term and intermediate moving averages. |
| Price | 10 | Checks whether current price is above the 20D moving average. |
| Moving Average | 10 | Checks whether the 20D moving average is above the 200D moving average. |

Total: 100 points.

## Current Labels

| Score | Label |
| ---: | --- |
| 75-100 | bullish |
| 60-74 | constructive |
| 45-59 | neutral |
| 0-44 | weak |

## Momentum Definition

The momentum bucket is split into three true/false checks:

| Momentum input | Signal |
| --- | --- |
| Current price greater than 200D moving average | True can be bullish/neutral; false is bearish |
| 20D moving average greater than 50D moving average | True is bullish/neutral; false is bearish |
| 50D moving average greater than 200D moving average | True can be bullish/neutral; false is bearish |

Scoring: 3 true gets the full 70 points and is bullish, 2 true is neutral, and 1 or 0 true is bearish.

## Price Definition

- Current price greater than 20D moving average: bullish and gets 10 points.
- Current price not greater than 20D moving average: bearish and gets 0 points.

## Moving Average Definition

- 20D moving average greater than 200D moving average: bullish and gets 10 points.
- 20D moving average not greater than 200D moving average: bearish and gets 0 points.

## Workshop Questions

- Should a stock below the 200-day moving average be capped at `Hold` or `Sell` regardless of other signals?
- Should volume confirmation be part of the score once we add richer volume data?
