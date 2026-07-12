"""CROON RFQ - a recurring-demand engine for CROO agents.

Every standing order is recurring demand. Every CROO agent is my supply.
On every run we re-open the market: request quotes, score, select under budget,
settle on-chain via CAP, and emit a signed receipt.
"""

__version__ = "0.1.0"
