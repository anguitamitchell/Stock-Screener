# Market Condition Worksheet

This worksheet translates the market-condition framework into implementation-ready modules. The goal is to produce a bullish, neutral, or bearish market backdrop that can later adjust stock-level Buy / Hold / Sell decisions.

## Layer 1: DeMark Score

Purpose: identify whether the market price structure is exhausted, neutral, or reversing.

Implemented first because it is mechanical and can be calculated from daily OHLC bars.

| Signal | Current implementation |
| --- | --- |
| TD Setup | Tracks consecutive closes higher or lower than the close 4 bars prior. |
| TD Buy Setup | 9 bars where close is lower than close 4 bars prior. |
| TD Sell Setup | 9 bars where close is higher than close 4 bars prior. |
| TD Countdown | Starts from the most recent completed setup and counts qualifying bars against the high/low 2 bars prior. |
| Regime | Converts the current setup/countdown state into bullish, neutral, or bearish. |

Important note: this is a practical DeMark-style implementation, not a licensed reproduction of proprietary Bloomberg/DeMark analytics.

## Layer 2: Market Participation

Reality vs. narrative: cap-weighted indexes can rise while the average stock weakens.

Implemented metric:

- Current `RSP / SPY`

Current rule:

- Bullish: current `RSP / SPY` is above `1.000`
- Neutral: current `RSP / SPY` is exactly `1.000`
- Bearish: current `RSP / SPY` is below `1.000`

## Layer 3: Sentiment Extremes

Reality vs. narrative: when everyone is all-in, marginal buying power may be depleted.

Candidate metrics:

- CNN Fear & Greed Index
- AAII sentiment survey
- Put/call ratio

Implementation note: this needs a source decision before coding.

## Layer 4: Technology Support

Reality vs. narrative: tech is the high-beta engine and often leads both up and down.

Implemented metric:

- `QQQ` relative to its 200D moving average
- `QQQ` 50D moving average relative to its 200D moving average
- `QQQ` 1M / 3M trend
- `QQQ / SPY` 3M relative strength

Current rule:

- Bullish: QQQ above 200D, 50D above 200D, positive trend, and leading SPY
- Neutral: mixed technology trend
- Bearish: QQQ below 200D, weak 50D/200D structure, or lagging SPY

## Layer 5: Breadth

Reality vs. narrative: a rally is healthier when many stocks participate.

Candidate metrics:

- Advance/decline line
- Percent of stocks above 50D moving average
- Percent of stocks above 200D moving average

Implementation note: this needs a breadth source.

## Layer 6: Credit Spreads

Reality vs. narrative: credit often warns before equities.

Implemented metric:

- `HYG / LQD` ratio as an ETF proxy

Future metric:

- ICE BofA US High Yield OAS

Current rule:

- Bullish: high yield outperforming investment grade and spreads stable/tightening
- Neutral: mixed
- Bearish: spreads widening or `HYG / LQD` breaking down

## Layer 7: Defensive Rotation

Reality vs. narrative: managers often hide in defensives before going fully to cash.

Implemented metrics:

- `XLU / SPY`
- `XLP / SPY`

Current rule:

- Bullish risk appetite: defensive ratios falling
- Neutral: mixed
- Bearish de-risking: defensive ratios rising

## Layer 8: Dollar and Treasury Yield Shock

Reality vs. narrative: a sharp dollar and yield rally can drain liquidity from risk assets.

Implemented metrics:

- `UUP` as a tradable dollar proxy
- `^TNX` as the 10-year Treasury yield proxy

Current rule:

- Bullish: dollar and yields stable
- Neutral: one pressure rising
- Bearish: both rising sharply together

## Layer 9: Fed / Liquidity

Purpose: determine whether liquidity is entering or leaving the market.

Current status: documented, not yet scored.

Candidate inputs:

- Fed balance sheet trend
- Treasury General Account trend
- Reverse repo trend
- Fed funds path
- Yield curve steepening/flattening
- MBS runoff or Treasury runoff
- Real rates

Implementation note: this should become a separate liquidity score, not part of DeMark. DeMark measures price exhaustion; Fed/liquidity measures the macro fuel supply.
