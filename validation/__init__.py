"""Institutional quantitative validation of the regime model (P5).

READ-ONLY over the engine: this package reuses `zones.fetch_daily`, `regime.analyze`
and the analog machinery to build an out-of-sample-in-time backtest and a battery of
statistical audits. It NEVER modifies the engine, the inference, the scores or the
datasets. Everything here is derived analysis on top of the shipped model.
"""
