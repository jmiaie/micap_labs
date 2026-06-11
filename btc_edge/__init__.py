"""btc_edge — short-horizon BTC direction forecasting + prediction-market edge engine.

Pipeline: bars -> causal features -> calibrated P(up) models -> fair-value pricer
-> edge vs Polymarket/Kalshi quotes -> (paper) trades.
"""

__version__ = "0.1.0"
