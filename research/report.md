# Entry-confirmation backtest report

Window: 2025-07-16 14:00 → 2026-07-11 14:00 UTC · Symbols: BTCUSDT, ETHUSDT, SOLUSDT · notional $200/trade of $1,000 allocated
Costs: taker 0.055%/side + slippage 0.02%/side + historical funding. Exits on 1-minute data (conservative 15m fallback tagged).

| metric | M1_current | M2_twocandle | M3_structure | M4_break2 |
|---|---|---|---|---|
| trades | 87 | 93 | 53 | 33 |
| win rate % | 41.4 | 39.8 | 35.8 | 36.4 |
| net $ | -42.53 | -25.3 | -38.04 | -17.6 |
| profit factor | 0.81 | 0.89 | 0.73 | 0.8 |
| expectancy (R) | -0.122 | -0.068 | -0.179 | -0.133 |
| max drawdown $ | 92.51 | 97.28 | 53.07 | 37.97 |
| max consec losses | 11 | 11 | 7 | 5 |
| avg entry delay (min) | 147.6 | 78.1 | 305.9 | 340.9 |
| BUY pnl $ | -71.28 | -73.9 | -31.29 | -23.0 |
| SELL pnl $ | 28.75 | 48.6 | -6.75 | 5.4 |
| funding $ | -0.1 | 0.24 | 0.17 | 0.1 |
| intrabar-ambiguous | 0 | 0 | 0 | 0 |
| trail halves activated | 22 | 27 | 13 | 8 |
| trail avg exit % | 4.83 | 4.93 | 5.08 | 5.69 |
| trail avg give-back % | 3.84 | 3.86 | 3.85 | 3.81 |

## Per-symbol net $
- **mode1_current**: {'BTCUSDT': -20.98, 'ETHUSDT': -2.27, 'SOLUSDT': -19.28}
- **mode2_twocandle**: {'BTCUSDT': 1.54, 'ETHUSDT': -12.67, 'SOLUSDT': -14.17}
- **mode3_structure**: {'BTCUSDT': -53.46, 'ETHUSDT': -9.32, 'SOLUSDT': 24.74}
- **mode4_break2**: {'BTCUSDT': -35.18, 'ETHUSDT': 22.19, 'SOLUSDT': -4.61}

## Missed / disarmed setups (counts by reason)
- **mode1_current**: {'blocked_position_open': 175, 'timeout_12h': 6, 'trend_flip': 2}
- **mode2_twocandle**: {'blocked_position_open': 385, 'rsi_neutral': 8, 'trend_flip': 1}
- **mode3_structure**: {'blocked_position_open': 36, 'rsi_neutral': 126, 'timeout_12h': 15, 'trend_flip': 3}
- **mode4_break2**: {'rsi_neutral': 156, 'timeout_12h': 23, 'blocked_position_open': 6, 'trend_flip': 3}

## Disclaimers
- Mode 1 is simulated on closed hourly candles (intended design); the live engine currently includes the forming candle — a known deviation, not fixed here by owner instruction.
- These are evaluation results, **not a guarantee of future profit**. Win rate is not the primary metric — expectancy (R) and profit factor matter more.
- Funding applied from Bybit's historical funding rates at each 8h timestamp a simulated position spanned.
