"""Whether the private trade plugin is installed.

The core and the public plugins pass without it (CI runs a job that way). Their tests use `TRADE` only
where installing trade legitimately changes a core result (a ledger file gains trade's tables, a price
basis gains trade's hook); `without_trade` marks a test of what happens in its absence. The trade
plugin's own tests live with it, in plugins/trade/trade_tests.
"""

import importlib.util
import unittest

TRADE = importlib.util.find_spec("newedenworks_trade") is not None

without_trade = unittest.skipIf(TRADE, "trade is installed; this checks what happens without it")
