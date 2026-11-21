"""Market price lookup: name resolution, book reduction, cluster folding, the quote cache and fee rates.
(The `market` command itself is the trade plugin's, and tested there.)

Runs against tests/fake_esi.py's in-process ESI, whose order books are served with real RFC 1123
`Last-Modified` headers - freshness has to be pinned against the same clock the client reads, not
against an ISO string the real endpoint never sends."""

from __future__ import annotations

import csv
import io
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from newedenworks import alphadata, esi, exports, market


from tests.fake_esi import (
    ABYSSAL_REGION, INV_CITADEL_SEEN, INV_TYPE_CONTAINER, INV_TYPE_SHIP, MARKET_BROKEN,
    MARKET_BOOK_AGE, MARKET_DOMAIN, MARKET_FORGE, MARKET_PLEX, MARKET_PRICES_AGE, MARKET_UNTRADED,
    STATION_AMARR, STATION_FORGE_OTHER, STATION_JITA, SYSTEM_FORGE, WORMHOLE_REGION, FakeEsiEnv,
    http_date, _order, ADA, VELA,
)


class MarketTestCase(unittest.TestCase):
    def setUp(self):
        self.env = FakeEsiEnv()
        self.env.start()
        self.env.install_core()       # /universe/names
        self.env.install_market()     # ids, region list, two books, one dead shard, history
        self.addCleanup(self.env.stop)
        self.client = esi.Esi("unittest")

    def now(self) -> float:
        return self.client.now().timestamp()


class ResolveTests(MarketTestCase):
    def test_type_name_reads_only_the_inventory_bucket(self):
        # The fixture answers "Tritanium" with a character as well; taking whichever bucket came
        # back first would price a player.
        self.assertEqual(market.resolve_type(self.client, "Tritanium"), (34, "Tritanium"))

    def test_numeric_type_id_is_not_looked_up_by_name(self):
        self.assertEqual(market.resolve_type(self.client, "34"), (34, "Tritanium"))
        self.assertEqual(self.env.server.calls_to("/universe/ids"), [])

    def test_unknown_type_names_say_that_esi_matches_exactly(self):
        with self.assertRaisesRegex(RuntimeError, r"no type named 'Tritaniom'"):
            market.resolve_type(self.client, "Tritaniom")

    def test_region_resolves_by_name_and_by_id(self):
        forge = (MARKET_FORGE, "The Forge")
        self.assertEqual(market.resolve_region(self.client, "The Forge"), forge)
        self.assertEqual(market.resolve_region(self.client, str(MARKET_FORGE)), forge)


class QuoteTests(MarketTestCase):
    def test_regional_book_reduces_both_sides(self):
        quote = market.quote(self.client, 34, market.Scope(MARKET_FORGE, "The Forge"))
        self.assertEqual((quote.min_sell, quote.max_buy), (4.98, 4.30))
        # volume_remain, not volume_total: what is left on the book is what can be traded.
        self.assertEqual((quote.sell_volume, quote.buy_volume), (1250, 920))
        # Two sells and two buys of type 34; the Pyerite and blueprint rows stay out.
        self.assertEqual((quote.sell_orders, quote.buy_orders), (2, 2))
        self.assertEqual(quote.best_sell_location, STATION_FORGE_OTHER)
        self.assertAlmostEqual(quote.spread, 0.68, places=9)
        self.assertAlmostEqual(quote.margin_pct, 15.813953488372095, places=9)

    def test_station_scope_only_counts_orders_at_that_station(self):
        hub = market.HUBS["jita"]
        scope = market.Scope(hub.region_id, "Jita 4-4 (station)", hub.system_id, hub.station_id)
        quote = market.quote(self.client, 34, scope)
        self.assertEqual((quote.min_sell, quote.max_buy), (5.05, 4.20))
        self.assertEqual((quote.sell_orders, quote.buy_orders), (1, 1))
        self.assertEqual(quote.best_sell_location, STATION_JITA)
        self.assertEqual(quote.best_buy_location, STATION_JITA)

    def test_a_side_with_no_orders_is_missing_rather_than_zero(self):
        # Type 590 has a buy order and no sell order: "somebody sells it for 0 ISK" is a lie.
        quote = market.quote(self.client, 590, market.Scope(MARKET_FORGE, "The Forge"))
        self.assertIsNone(quote.min_sell)
        self.assertEqual(quote.max_buy, 3.10)
        self.assertIsNone(quote.spread)
        self.assertIsNone(quote.margin_pct)

    def test_an_empty_book_quotes_nothing(self):
        quote = market.quote(self.client, 40520, market.Scope(MARKET_DOMAIN, "Domain"))
        self.assertIsNone(quote.min_sell)
        self.assertIsNone(quote.max_buy)
        self.assertEqual((quote.sell_volume, quote.buy_volume), (0, 0))

    def test_freshness_comes_from_the_response_not_from_the_clock(self):
        quote = market.quote(self.client, 34, market.Scope(MARKET_FORGE, "The Forge"))
        age = self.now() - quote.meta.last_modified
        self.assertGreater(age, 100)      # the fixture serves a book two minutes old
        self.assertLess(age, 240)
        line = market.freshness_line(quote.meta, self.now())
        self.assertIn("as of", line)
        self.assertIn("ago; ESI refreshes the book every 5 min", line)

    def test_a_response_without_a_stamp_says_so(self):
        self.assertIn("unknown", market.freshness_line(esi.Meta(), self.now()))


class ClusterTests(MarketTestCase):
    def test_market_regions_skips_space_that_has_no_markets(self):
        # Wormhole and abyssal regions are listed by /universe/regions but never trade; scanning
        # them would add ~44 pointless requests to every cluster scan.
        self.assertEqual(market.market_regions(self.client),
                         [(MARKET_DOMAIN, "Domain"), (MARKET_BROKEN, "Heimatar"), (MARKET_FORGE, "The Forge")])

    def test_cluster_keeps_the_region_of_each_extreme_and_counts_failures(self):
        quote = market.quote_cluster(self.client, 34, market.market_regions(self.client))
        self.assertEqual(quote.scope, "global (2/3 regions)")
        self.assertEqual((quote.regions_scanned, quote.regions_failed), (2, 1))
        self.assertEqual((quote.min_sell, quote.best_sell_region), (4.98, MARKET_FORGE))
        self.assertEqual((quote.max_buy, quote.best_buy_region), (4.55, MARKET_DOMAIN))
        self.assertEqual((quote.sell_volume, quote.buy_volume), (1300, 980))

    def test_cluster_is_as_old_as_its_oldest_region(self):
        # The Forge answers with a 2-minute book, Domain with a 15-minute one: the fold is the
        # worst of the batch, because that is when the numbers stopped agreeing with each other.
        quote = market.quote_cluster(self.client, 34, market.market_regions(self.client))
        self.assertGreater(self.now() - quote.meta.last_modified, 800)

    def test_one_dead_region_does_not_sink_the_scan(self):
        paths = [market.book_path(region, 34) for region in (MARKET_FORGE, MARKET_DOMAIN, MARKET_BROKEN)]
        fetched = self.client.get_many(paths)
        self.assertEqual(set(fetched), set(paths))
        self.assertIsInstance(fetched[paths[0]], list)
        self.assertIsInstance(fetched[paths[2]], Exception)

    def test_a_repeated_path_is_fetched_once(self):
        path = market.book_path(MARKET_FORGE, 34)
        self.client.get_many([path, path, path])
        self.assertEqual(len(self.env.server.calls_to(f"/markets/{MARKET_FORGE}/orders")), 1)


class ReferencePriceTests(MarketTestCase):
    """`/markets/prices`: CCP's published figures, for the types no order book answers for."""

    def test_reference_reads_the_row_for_a_type(self):
        ref = market.reference_price(self.client, 34)
        self.assertEqual((ref.type_id, ref.average_price, ref.adjusted_price), (34, 4.87, 4.05))

    def test_a_published_zero_is_not_reported_as_missing(self):
        # Live ESI says `adjusted_price: 0.0` for PLEX. Zero is a figure CCP publishes, not a gap,
        # so the lookup must hand it through instead of collapsing it to "no industry reference".
        ref = market.reference_price(self.client, MARKET_PLEX)
        self.assertEqual((ref.average_price, ref.adjusted_price), (4574918.36, 0.0))

    def test_an_omitted_key_is_missing_rather_than_zero(self):
        # Pyerite's row carries no `adjusted_price` at all - the other half of that distinction.
        ref = market.reference_price(self.client, 36)
        self.assertIsNone(ref.adjusted_price)
        self.assertEqual(ref.average_price, 4.20)

    def test_a_type_the_document_omits_has_no_reference(self):
        self.assertIsNone(market.reference_price(self.client, 40520))

    def test_one_request_answers_for_every_type(self):
        # The document lists every priced type: reading it per type would be one huge request each.
        table = market.price_table(self.client)
        self.assertEqual([table.reference(t).average_price for t in (34, 36, MARKET_PLEX)],
                         [4.87, 4.20, 4574918.36])
        self.assertIsNone(table.reference(40520))
        self.assertEqual(len(self.env.server.calls_to("/markets/prices")), 1)

    def test_the_reference_carries_the_documents_own_age(self):
        # Its own, that is: a reference stamped with the five-minute book's age would be wrong in
        # whichever direction the reader trusted.
        ref = market.reference_price(self.client, MARKET_PLEX)
        self.assertGreater(self.now() - ref.meta.last_modified, MARKET_PRICES_AGE - 60)


# ---------------------------------------------------------------------------
# per-scope quote cache (quotes.json)
# ---------------------------------------------------------------------------

class QuoteCacheFixture:
    """Scaffolding shared by the two quote-cache test classes.

    A plain mixin, not a base TestCase: unittest would otherwise re-run every case below in each
    subclass. `Esi` keeps responses in memory for their stated TTL, so anything measuring the disk
    cache has to ask with a client of its own - which is also what each real invocation gets."""

    BOOK_TTL = 300.0     # what ESI states for a regional book, in seconds from the response

    def process(self) -> esi.Esi:
        return esi.Esi("unittest")

    def serve_books_with_expiry(self) -> None:
        """Re-serve both fixture books with an `Expires` header, as live ESI does."""
        for region in (MARKET_FORGE, MARKET_DOMAIN):
            def handler(call, region=region):
                rows, headers = self.env._market_orders(call)
                return rows, {**headers, "Expires": http_date(self.BOOK_TTL)}
            self.env.server.get(f"/markets/{region}/orders", handler=handler)

    def book_calls(self, region: int = MARKET_FORGE) -> list:
        return self.env.server.calls_to(f"/markets/{region}/orders")


class QuoteCacheTestCase(QuoteCacheFixture, MarketTestCase):
    """The figures behind `inventory --value-at`, reused only while ESI's own expiry vouches.

    The fixture's books arrive with `Last-Modified` alone - all `tests.fake_esi` has ever sent - so
    each test that expects caching re-serves them with the `Expires` live ESI adds. Without that
    header there is no stated expiry to honour and nothing may be cached, which is asserted below as
    its own case rather than left as an accident of the fixture."""

    def setUp(self):
        super().setUp()
        self.cache = tempfile.mkdtemp(prefix="quotes-")
        self.addCleanup(shutil.rmtree, self.cache, True)

    def seed(self, scope: market.Scope, type_id: int, *, age: float,
             ttl: float = QuoteCacheFixture.BOOK_TTL) -> None:
        """Put one figure on disk as though an earlier run had learned it `age` seconds ago."""
        moment = self.now()
        market.publish_figures({market.figure_key(scope, type_id): market.CachedFigure(
            min_sell=9.0, max_buy=8.0, last_modified=moment - age, expires=moment + ttl)},
            cache_dir=self.cache)

    def expire(self, scope: market.Scope, type_id: int) -> None:
        """Rewrite one record as one ESI has already disowned - the state a warm cache reaches five
        minutes later, and the reason a second run cannot simply trust whatever it finds."""
        path = market.quote_doc_path(self.cache)
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["figures"][market.figure_key(scope, type_id)]["expires"] = self.now() - 1.0
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)

    def test_a_warm_run_reads_no_book_and_reports_the_same_figures(self):
        self.serve_books_with_expiry()
        scope = market.Scope(MARKET_FORGE, "The Forge")
        cold = market.book_figures(self.process(), [34, 36], scope, cache_dir=self.cache)
        self.assertEqual((cold.fetched, cold.cached, cold.failed), (2, 0, 0))
        self.assertEqual(2, len(self.book_calls()))
        warm = market.book_figures(self.process(), [34, 36], scope, cache_dir=self.cache)
        self.assertEqual((warm.fetched, warm.cached), (0, 2))
        self.assertEqual(2, len(self.book_calls()))      # nothing new asked of ESI
        self.assertEqual(warm.max_buy, cold.max_buy)     # the money did not move by re-reading
        self.assertEqual(warm.min_sell, cold.min_sell)

    def test_only_the_entries_past_their_stated_expiry_are_refetched(self):
        self.serve_books_with_expiry()
        scope = market.Scope(MARKET_FORGE, "The Forge")
        market.book_figures(self.process(), [34, 36], scope, cache_dir=self.cache)
        read = len(self.book_calls())
        self.expire(scope, 34)
        figures = market.book_figures(self.process(), [34, 36], scope, cache_dir=self.cache)
        self.assertEqual((figures.fetched, figures.cached), (1, 1))
        # The fan-out answers out of order, so compare the calls this run added rather than the last.
        self.assertEqual(["34"], [call.query["type_id"] for call in self.book_calls()[read:]])

    def test_a_station_figure_never_answers_a_region_question(self):
        """The key is the scope's filter, not just its type: `--value-at jita` and
        `--value-at The Forge` read different order sets and may not borrow from each other."""
        self.serve_books_with_expiry()
        hub = market.HUBS["jita"]
        station = market.Scope(hub.region_id, hub.label, hub.system_id, hub.station_id)
        region = market.Scope(MARKET_FORGE, "The Forge")
        at_station = market.book_figures(self.process(), [34], station, cache_dir=self.cache)
        self.assertAlmostEqual(at_station.max_buy[34], 4.20, places=9)
        wide = market.book_figures(self.process(), [34], region, cache_dir=self.cache)
        self.assertEqual((wide.fetched, wide.cached), (1, 0))
        self.assertAlmostEqual(wide.max_buy[34], 4.30, places=9)     # the region's richest buy
        again = market.book_figures(self.process(), [34], station, cache_dir=self.cache)
        self.assertEqual((again.fetched, again.cached), (0, 1))      # and each stayed in its lane
        self.assertAlmostEqual(again.max_buy[34], 4.20, places=9)

    def test_the_freshness_names_the_oldest_figure_whatever_its_source(self):
        """Half off disk, half off ESI: the pair is as old as its stalest input, both ways round."""
        self.serve_books_with_expiry()
        forge = market.Scope(MARKET_FORGE, "The Forge")
        self.seed(forge, 36, age=1000)         # a cached figure older than the book read beside it
        figures = market.book_figures(self.process(), [34, 36], forge, cache_dir=self.cache)
        self.assertEqual((figures.cached, figures.fetched), (1, 1))
        self.assertAlmostEqual(figures.meta.last_modified, self.now() - 1000, delta=2.0)

        domain = market.Scope(MARKET_DOMAIN, "Domain")
        self.seed(domain, 36, age=10)          # and the other way: now the book is the older input
        figures = market.book_figures(self.process(), [34, 36], domain, cache_dir=self.cache)
        self.assertEqual((figures.cached, figures.fetched), (1, 1))
        self.assertGreater(self.now() - figures.meta.last_modified,
                           MARKET_BOOK_AGE[MARKET_DOMAIN] - 60)

    def test_a_book_with_no_stated_expiry_is_used_but_never_cached(self):
        # The fixture's default books carry `Last-Modified` only. Without an `Expires` nothing bounds
        # how long the figures may still be called current, so they are used once and forgotten.
        scope = market.Scope(MARKET_FORGE, "The Forge")
        first = market.book_figures(self.process(), [34], scope, cache_dir=self.cache)
        self.assertAlmostEqual(first.max_buy[34], 4.30, places=9)
        second = market.book_figures(self.process(), [34], scope, cache_dir=self.cache)
        self.assertEqual((second.fetched, second.cached), (1, 0))
        self.assertEqual(2, len(self.book_calls()))
        self.assertFalse(os.path.exists(market.quote_doc_path(self.cache)))

    def test_an_empty_book_is_an_answer_and_is_cached_as_one(self):
        """`[]` is ESI saying nobody has ordered this here, as of that stamp - worth keeping, and it
        leaves the type unpriced rather than worthless exactly as a cold read does."""
        self.serve_books_with_expiry()
        scope = market.Scope(MARKET_FORGE, "The Forge")
        first = market.book_figures(self.process(), [MARKET_UNTRADED], scope, cache_dir=self.cache)
        self.assertEqual((first.fetched, first.cached, first.failed), (1, 0, 0))
        self.assertEqual(first.max_buy, {})
        self.assertEqual(1, first.answered)     # it spoke; it simply had nothing to say
        second = market.book_figures(self.process(), [MARKET_UNTRADED], scope, cache_dir=self.cache)
        self.assertEqual((second.fetched, second.cached), (0, 1))
        self.assertEqual(1, len(self.book_calls()))     # the second run asked for nothing

    def test_a_book_that_did_not_answer_is_left_out_of_the_cache(self):
        """A timeout must cost a retry on the next run, not five minutes of confident silence."""
        scope = market.Scope(MARKET_BROKEN, "Placid shard")
        first = market.book_figures(self.process(), [34], scope, cache_dir=self.cache)
        self.assertEqual((first.fetched, first.failed, first.cached), (1, 1, 0))
        self.assertEqual(first.max_buy, {})
        second = market.book_figures(self.process(), [34], scope, cache_dir=self.cache)
        self.assertEqual((second.fetched, second.cached), (1, 0))
        self.assertFalse(os.path.exists(market.quote_doc_path(self.cache)))

    def test_a_cache_that_cannot_be_read_costs_a_refetch_and_nothing_else(self):
        scope = market.Scope(MARKET_FORGE, "The Forge")
        bodies = ["not json at all", "[]", '{"version": 999, "figures": {}}',
                  '{"version": 1, "figures": "nope"}',
                  '{"version": 1, "figures": {"k": {"min_sell": 1.0}}}']
        for body in bodies:
            with self.subTest(body=body):
                with open(market.quote_doc_path(self.cache), "w", encoding="utf-8") as fh:
                    fh.write(body)
                figures = market.book_figures(self.process(), [34], scope, cache_dir=self.cache)
                self.assertEqual((figures.fetched, figures.cached), (1, 0))
                self.assertAlmostEqual(figures.max_buy[34], 4.30, places=9)
        # Publishing over a broken document leaves a readable one behind rather than joining it.
        with open(market.quote_doc_path(self.cache), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["version"], market.QUOTE_CACHE_VERSION)

    def test_a_figure_with_no_stated_expiry_is_never_served(self):
        """Hand-edited or half-written records without `expires` are the same case as a live response
        that omitted it: unusable, whatever the prices in them look like."""
        self.serve_books_with_expiry()
        scope = market.Scope(MARKET_FORGE, "The Forge")
        key = market.figure_key(scope, 34)
        with open(market.quote_doc_path(self.cache), "w", encoding="utf-8") as fh:
            json.dump({"version": market.QUOTE_CACHE_VERSION,
                       "figures": {key: {"min_sell": 1.0, "max_buy": 99.0}}}, fh)
        figures = market.book_figures(self.process(), [34], scope, cache_dir=self.cache)
        self.assertEqual((figures.fetched, figures.cached), (1, 0))
        self.assertAlmostEqual(figures.max_buy[34], 4.30, places=9)   # ESI's figure, not the record's

    def test_expired_records_are_dropped_rather_than_written_back(self):
        """An entry past its stated expiry can never be served again, so keeping it would only grow
        the file; a live one from another run still has to survive this one's write."""
        self.serve_books_with_expiry()
        scope = market.Scope(MARKET_FORGE, "The Forge")
        self.seed(scope, 36, age=1000, ttl=-60.0)      # already expired when this run starts
        self.seed(scope, MARKET_UNTRADED, age=5)       # somebody else's live figure
        market.book_figures(self.process(), [34], scope, cache_dir=self.cache)
        with open(market.quote_doc_path(self.cache), encoding="utf-8") as fh:
            figures = json.load(fh)["figures"]
        self.assertNotIn(market.figure_key(scope, 36), figures)
        self.assertIn(market.figure_key(scope, MARKET_UNTRADED), figures)


class QuoteCacheMergeTests(unittest.TestCase):
    """Two runs publishing to one cache file - the two characters of one account, or two shells."""

    class Books:
        """A transport that only answers books, and overlaps the two runs inside one barrier.

        The barrier sits in the fetch, which both runs reach only after reading the cache: so both
        reads are provably stale by the time either publishes, and a publisher that wrote the
        document it had read earlier would lose the other run's types here every time."""

        def __init__(self, barrier: threading.Barrier):
            self.barrier = barrier

        def get_many_meta(self, paths):
            self.barrier.wait()
            moment = time.time()
            rows = [{"price": 5.0, "is_buy_order": False}, {"price": 4.0, "is_buy_order": True}]
            meta = esi.Meta(last_modified=moment - 10, expires=moment + 300)
            return {path: (rows, meta) for path in paths}

    def test_two_valuations_publish_without_losing_each_other(self):
        cache = tempfile.mkdtemp(prefix="quotes-")
        self.addCleanup(shutil.rmtree, cache, True)
        scope = market.Scope(MARKET_FORGE, "The Forge")
        barrier = threading.Barrier(2, timeout=5)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda ident: market.book_figures(self.Books(barrier), [ident], scope,
                                                            cache_dir=cache), (34, 36)))
        stored = market.read_quote_cache(cache)
        self.assertEqual(sorted(stored), sorted([market.figure_key(scope, 34),
                                                 market.figure_key(scope, 36)]))


class InventoryQuoteCacheTests(QuoteCacheFixture, MarketTestCase):
    """`inventory --value-at` end to end: what the run says before it starts, and admits after.

    These live here rather than in `test_cli_integration.py` because they are about the quote cache.
    Every `env.run` builds its own `Esi`, so nothing carried from one run to the next is in memory:
    what a second run fails to ask for is exactly what this cache saved."""

    HELD = [34, 36, 590, INV_TYPE_SHIP, INV_TYPE_CONTAINER]

    def setUp(self):
        super().setUp()
        self.env.install_inventory()      # re-installs the market routes, so wrap afterwards
        self.serve_books_with_expiry()

    def hub_scope(self, name: str = "jita") -> market.Scope:
        """The scope `--value-at <hub>` prices at, built the way `_valuation_scope` builds it."""
        hub = market.HUBS[name]
        return market.Scope(hub.region_id, hub.label, hub.system_id, hub.station_id)

    def total_line(self, out: str) -> str:
        return next(line for line in out.splitlines() if line.startswith("TOTAL"))

    def test_a_second_valuation_reads_no_book_at_all(self):
        code, cold, err = self.env.run(["inventory", "--value-at", "jita"])
        self.assertEqual(code, 0)
        self.assertIn("pricing 5 distinct types held: 5 order books to read, one per type", err)
        self.assertNotIn("at this size", err)    # five books is not a wait worth quoting a cost for
        read = len(self.book_calls())
        self.assertEqual(5, read)
        code, warm, err = self.env.run(["inventory", "--value-at", "jita"])
        self.assertEqual(code, 0)
        self.assertEqual(read, len(self.book_calls()))
        self.assertIn("every figure already in the local quote cache, so no order book is read", err)
        # Same figures, and the same stated age: a cached number is not re-dated to this run.
        self.assertEqual(self.total_line(cold), self.total_line(warm))
        self.assertIn("0 order-book requests now; 5 types served from the local quote cache", warm)
        # Asking for a different view of the same holdings is not a new scope either.
        code, items, _ = self.env.run(["inventory", "--items", "--by", "category",
                                       "--value-at", "jita"])
        self.assertEqual((code, len(self.book_calls())), (0, read))
        self.assertEqual(self.total_line(warm), self.total_line(items))

    def test_a_cached_figure_is_still_reported_at_its_own_age(self):
        """The freshness line is the honesty of the whole feature: figures reused from disk are
        printed with the stamp ESI gave them, never with the moment they happened to be read."""
        scope = self.hub_scope()
        stamp = self.now() - 3600
        market.publish_figures({market.figure_key(scope, ident): market.CachedFigure(
            min_sell=9.0, max_buy=8.0, last_modified=stamp, expires=self.now() + 300)
            for ident in self.HELD})
        code, out, _ = self.env.run(["inventory", "--value-at", "jita"])
        self.assertEqual(code, 0)
        self.assertEqual([], self.book_calls())
        self.assertIn(f"as of {time.strftime('%H:%M:%SZ', time.gmtime(stamp))}", out)
        self.assertIn("1h 00m ago", out)

    def test_a_partially_warm_run_says_which_half_it_still_has_to_read(self):
        code, _out, err = self.env.run(["inventory", "--value-at", "jita"])
        self.assertEqual((code, len(self.book_calls())), (0, 5))
        scope = self.hub_scope()
        path = market.quote_doc_path()
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["figures"][market.figure_key(scope, 34)]["expires"] = self.now() - 1.0
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        code, _out, err = self.env.run(["inventory", "--value-at", "jita"])
        self.assertEqual(code, 0)
        self.assertIn("pricing 5 distinct types held: 1 order book to read, one per type", err)
        self.assertIn("(4 already priced from the last run)", err)
        self.assertEqual(6, len(self.book_calls()))

    def test_machine_output_keeps_the_notice_off_both_streams(self):
        code, out, err = self.env.run(["inventory", "--value-at", "jita", "--json"])
        self.assertEqual((code, err), (0, ""))
        cold = json.loads(out)["value_basis"]
        self.assertEqual((cold["requests"], cold["cached_figures"]), (5, 0))
        warm = json.loads(self.env.run(["inventory", "--value-at", "jita", "--json"])[1])
        self.assertEqual((warm["value_basis"]["requests"], warm["value_basis"]["cached_figures"]),
                         (0, 5))

    def test_the_reference_basis_never_goes_through_the_quote_cache(self):
        """`/markets/prices` is one document for the whole cluster, stamped on its own schedule.
        Caching its figures under an order book's five-minute expiry would misstate them."""
        code, out, _ = self.env.run(["inventory"])
        self.assertEqual(code, 0)
        self.assertIn("freshness: as of ", out)
        self.assertEqual(1, len(self.env.server.calls_to("/markets/prices")))
        self.assertEqual([], self.book_calls())
        self.assertFalse(os.path.exists(market.quote_doc_path()))

    def test_the_notice_quotes_a_duration_only_when_the_wait_is_long(self):
        small = exports._valuation_notice(market.Preflight(types=5, cached=0, fetches=5))
        self.assertNotIn("at this size", small)
        big = exports._valuation_notice(market.Preflight(types=518, cached=18, fetches=500))
        self.assertIn("pricing 518 distinct types held: 500 order books to read", big)
        self.assertIn("(18 already priced from the last run)", big)
        self.assertIn("at this size", big)      # a minute of silence earns an explanation
        one = exports._valuation_notice(market.Preflight(types=1, cached=0, fetches=1))
        self.assertIn("1 distinct type held: 1 order book to read", one)


class MarketIndexFixture(MarketTestCase):
    """`--group`/`--category` and `--fields`, against a seeded market type index.

    The bundled index is 0.94 MB of live SDE names; quoting it would make every assertion here a bet
    that CCP does not rename a group before the next build. These four types - all served by
    tests/fake_esi.py's books, one of them deliberately empty - are the whole world instead."""

    GROUPS = {
        "Basic Commodities - Tier 1": {"id": 1042, "category_id": 43, "category": "Planetary Commodities",
                                       "types": {"34": "Tritanium", "36": "Pyerite"}},
        "Refined Commodities - Tier 2": {"id": 1034, "category_id": 43,
                                         "category": "Planetary Commodities",
                                         "types": {"590": "Caldari Ship Blueprint"}},
        "Account Status": {"id": 517, "category_id": 17, "category": "Commodity",
                           "types": {"44992": "PLEX"}},
    }
    CATEGORIES = {
        "Planetary Commodities": {"id": 43, "groups": ["Basic Commodities - Tier 1",
                                                       "Refined Commodities - Tier 2"]},
        "Commodity": {"id": 17, "groups": ["Account Status"]},
    }

    def setUp(self):
        super().setUp()
        self.seed_index()

    def seed_index(self, **overrides) -> None:
        """Write the index where `alphadata` looks before the package's own copy."""
        self.env._write_json(os.path.join(self.env.data_home, "eve-skills", "market_types.json"),
                             {"source": "synthetic", "build": 2500001, "fetched": "2026-09-01T00:00:00Z",
                              "groups": self.GROUPS, "categories": self.CATEGORIES, **overrides})

    def without_any_index(self) -> None:
        """Hide the copy bundled in the package too, so "nothing installed" is provable whatever the
        wheel ships - the same trick `tests/test_doctor.py` uses."""
        empty = os.path.join(tempfile.mkdtemp(prefix="neworks-no-index-"), "package-data")
        os.makedirs(empty, exist_ok=True)
        patcher = mock.patch.object(alphadata, "PACKAGE_DATA_DIR", Path(empty))
        patcher.start()
        self.addCleanup(patcher.stop)

    def books_read(self) -> list[str]:
        """Which type ids this run asked ESI for, in request order."""
        return [call.query["type_id"]
                for region in (MARKET_FORGE, MARKET_DOMAIN)
                for call in self.env.server.calls_to(f"/markets/{region}/orders")]


# ---------------------------------------------------------------------------
# --seller: what a named character nets after CCP's two cuts
# ---------------------------------------------------------------------------

# Station ownership exactly as live ESI reports it, measured on 2026-09-13: Jita IV - Moon 4 belongs to
# corporation 1000035 (Caldari Navy) and Amarr VIII (Oris) to 1000086 (Emperor Family). The fixture's
# second Forge station is synthetic, so its owner is invented here too - what the tests pin is that the
# fee follows whichever corporation ESI names, not which corporation that turns out to be.
CORP_CALDARI_NAVY = 1000035
CORP_EMPIRER_FAMILY = 1000086
CORP_SYNTHETIC_FORGE = 90000099


class SellerFeeFormulaTests(unittest.TestCase):
    """CCP's published arithmetic, tested without ESI so that a rate change is one obvious failure."""

    def test_accounting_cuts_the_sales_tax_multiplicatively(self):
        # "Sales tax starts at 7.5%" and Accounting "reduces sales tax by 11%" per level - 11% of what
        # is left, not 11 percentage points off, which would make level V free.
        self.assertEqual([7.5, 6.675, 5.85, 5.025, 4.2],
                         [market.sales_tax_pct(level) for level in range(5)])
        # CCP's stated floor lands exactly at level V: 7.5 x 0.45 = 3.375.
        self.assertEqual(3.375, market.sales_tax_pct(5))

    def test_broker_relations_and_standing_deduct_percentage_points(self):
        self.assertEqual(3.0, market.broker_fee_pct(0))
        self.assertEqual(1.8, market.broker_fee_pct(4))
        # +7.742 is a standing measured against a real NPC corporation on 2026-09-13. The corp term is
        # 0.02 points per standing point, so it barely moves next to the skill - which is the reason
        # this column exists: training is worth more than friendship.
        self.assertEqual(1.645, market.broker_fee_pct(4, 7.742))
        # A negative standing makes the fee worse rather than better; CCP's formula has no floor at zero.
        self.assertEqual(1.825, market.broker_fee_pct(4, -1.25))

    def test_the_one_percent_floor_does_not_bite_at_legal_inputs(self):
        # "to a minimal broker fee of 1% of the order value". Level V alone leaves 1.5%, and even with
        # a perfect +10 standing at the station it is 1.3%, so the clamp in `broker_fee_pct` is CCP's
        # cap recorded faithfully rather than live behaviour - pinning both means a future faction term
        # cannot quietly cross the floor it now shares.
        self.assertEqual(1.5, market.broker_fee_pct(5))
        self.assertEqual(1.3, market.broker_fee_pct(5, 10.0))
        self.assertEqual(1.0, market.broker_fee_pct(9))

    def test_net_price_is_the_price_after_one_cut(self):
        # The worked case: a 14,150 ISK ask at a 6% total cut nets 13,301 - and not the
        # 13300.999999999998 that the float product prints into a table cell.
        self.assertEqual(13301.0, market.net_price(14150, 6.0))
        self.assertIsNone(market.net_price(None, 6.0))
        self.assertIsNone(market.net_price(14150, None))     # a place whose fee is not knowable


if __name__ == "__main__":
    unittest.main()
