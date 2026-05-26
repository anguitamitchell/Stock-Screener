# Technical Screening Worksheet

This is the first draft of the technical score. It uses your criteria as the core of the model and adds a few supporting checks that are easy to explain and adjust.

## Current Draft Weights

| Component | Weight | Why it matters |
| --- | ---: | --- |
| Current price vs 20-day moving average | 15 | Captures short-term trend strength. |
| Current price vs 200-day moving average | 20 | Captures long-term trend quality. |
| 20-day moving average vs 200-day moving average | 15 | Checks whether the shorter trend is above the long-term trend. |
| Current price above both 20-day and 200-day moving averages | 20 | Gives credit for clean trend alignment. |
| Momentum | 15 | Rewards positive 1-month return, positive 3-month return, and 3-month relative strength versus SPY. |
| Current price vs 50-day moving average | 10 | Adds an intermediate trend check. |
| 50-day moving average vs 200-day moving average | 5 | Adds a longer trend confirmation check. |

Total: 100 points.

## Current Labels

| Score | Label |
| ---: | --- |
| 75-100 | bullish |
| 60-74 | constructive |
| 45-59 | neutral |
| 0-44 | weak |

## Momentum Definition

The momentum score is now split into three parts:

| Momentum input | Weight |
| --- | ---: |
| 1-month return | 5 |
| 3-month return | 5 |
| 3-month return minus SPY 3-month return | 5 |

This keeps momentum from being too dependent on a single trading day and gives extra credit to stocks that are leading the ETF itself.

## Workshop Questions

- Should price above the 200-day moving average matter more than price above the 20-day?
- Momentum currently uses 1-month return, 3-month return, and 3-month relative strength versus SPY.
- Should a stock below the 200-day moving average be capped at `Hold` or `Sell` regardless of other signals?
- Should we require the 20-day moving average to be above the 200-day moving average for a `Buy`?
- Should volume confirmation be part of the score once we add richer volume data?
