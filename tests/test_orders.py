"""Order normalisation, fetching and the `orders` command.

Protects the two things that cannot be re-derived later: what an ESI order row *means* - the
terminal state has to be derived because ESI's history enum has no `filled` - and how one owner's
refusal degrades for everybody else, including the corporation case where two stored characters are
colleagues and would otherwise report one order book twice. No network."""

from __future__ import annotations

import csv
import io
import json
import unittest

from newedenworks import esi, orders, sso

from tests.fake_esi import (
    ADA, CORP_SHARED, MARKET_DOMAIN, MARKET_FORGE, MIRA, STATION_AMARR, STATION_JITA, FakeEsiEnv,
    owner_order,
)


def _market_row(order_id: int, price: float, location_id: int, remain: int, *, buy: bool = False) -> dict:
    """One `/markets/{region}/orders` row as live ESI sends it."""
    return {"order_id": order_id, "type_id": 34, "location_id": location_id, "system_id": 30000142,
            "price": price, "volume_remain": remain, "volume_total": remain, "is_buy_order": buy,
            "issued": "2026-09-01T00:00:00Z", "duration": 90, "min_volume": 1, "range": "station"}


class NormalisationTests(unittest.TestCase):
    """Pure row translation: no client, so a malformed or partial ESI row is still handled."""

    def test_open_row_carries_its_book_identity_and_expiry(self):
        row = owner_order(700001, price=5.75, remain=100, total=250, issued="2026-09-01T06:30:00Z",
                          duration=30)
        order = orders.normalise(row, "char:91000001", "Ada Vane")
        self.assertEqual(order.order_id, 700001)
        self.assertEqual((order.owner_key, order.owner_name), ("char:91000001", "Ada Vane"))
        self.assertFalse(order.is_buy)
        self.assertEqual((order.type_id, order.region_id, order.location_id),
                         (34, 10000002, 60003760))
        self.assertEqual(order.price, 5.75)
        self.assertEqual((order.volume_remain, order.volume_total), (100, 250))
        self.assertEqual(order.filled, 150)          # what already left the order
        self.assertEqual(order.state, "open")        # a live book row is open by definition
        self.assertEqual(order.range, "station")
        # issued + duration days, as an ISO UTC stamp like every other timestamp this tool emits.
        self.assertEqual(order.expires, "2026-10-01T06:30:00Z")

    def test_zero_duration_row_has_no_expiry_date(self):
        # ESI reports duration 0 on rows that were never timed orders; dating their expiry at the
        # moment of issue would report every one of them as instantly lapsed.
        order = orders.normalise(owner_order(700104, duration=0), "char:91000001", "Ada Vane")
        self.assertIsNone(order.expires)

    def test_cancelled_row_stays_cancelled_whatever_it_had_sold(self):
        for remain in (0, 40):
            order = orders.normalise(owner_order(700101, state="cancelled", remain=remain,
                                                 total=100), "char:91000001", "Ada Vane", closed=True)
            self.assertEqual(order.state, "cancelled")   # the owner ended it, not the market

    def test_expired_and_fully_filled_row_is_reported_as_filled(self):
        order = orders.normalise(owner_order(700102, state="expired", remain=0, total=90),
                                 "char:91000001", "Ada Vane", closed=True)
        self.assertEqual(order.state, "filled")
        self.assertEqual(order.filled, 90)

    def test_expired_with_volume_left_is_expired_and_part_filled(self):
        order = orders.normalise(owner_order(700103, state="expired", remain=20, total=80),
                                 "char:91000001", "Ada Vane", closed=True)
        self.assertEqual(order.state, "expired")
        self.assertEqual(order.filled, 60)

    def test_keys_esi_leaves_out_entirely_become_none(self):
        row = owner_order(700002, buy=True, escrow=1550.0, min_volume=10)
        for key in ("escrow", "is_buy_order", "min_volume"):
            row.pop(key)
        order = orders.normalise(row, "char:91000001", "Ada Vane")
        self.assertFalse(order.is_buy)
        self.assertIsNone(order.escrow)
        self.assertIsNone(order.min_volume)

    def test_a_personal_row_can_be_funded_by_the_corporation(self):
        # Character endpoints flag this with `is_corporation`; corporation endpoints never send that
        # key at all, so the fetcher has to say it - a corporation row must not read as a member's.
        flagged = orders.normalise(owner_order(700050, corp_order=True), "char:91000001", "Ada Vane")
        own = orders.normalise(owner_order(700051), "char:91000001", "Ada Vane")
        corporate = orders.normalise(owner_order(700052, wallet_division=3), f"corp:{CORP_SHARED}",
                                     "Shared Ledger Holdings", corporation=True)
        self.assertTrue(flagged.is_corporation)
        self.assertFalse(own.is_corporation)
        self.assertTrue(corporate.is_corporation)


class OrdersFetchCase(unittest.TestCase):
    def setUp(self):
        self.env = FakeEsiEnv()
        self.env.start()
        self.env.install_core()
        self.env.install_orders()
        self.addCleanup(self.env.stop)
        self.client = esi.Esi(esi.default_user_agent(sso.load_config()))

    def record(self, char, without: str | None = None) -> dict:
        tok = dict(sso.get_access_token(char.character_id))
        if without:
            tok["scopes"] = [s for s in tok["scopes"] if s != without]
        return tok

    def public(self, char) -> dict:
        return self.client.get(f"/characters/{char.character_id}")


class FetchTests(OrdersFetchCase):
    def test_character_book_includes_every_history_page(self):
        book = orders.fetch_character(self.client, self.record(ADA))
        self.assertEqual(book.owner_key, "char:91000001")
        self.assertEqual(book.owner_name, "Ada Vane")
        self.assertEqual([o.order_id for o in book.open], [700001, 700002])
        # served over two pages: a fetcher that ignores X-Pages silently loses the last row.
        self.assertEqual([o.order_id for o in book.history], [700101, 700102, 700103])
        self.assertTrue(book.history_ok)

    def test_failed_history_keeps_the_live_book(self):
        self.env.server.get(f"/characters/{ADA.character_id}/orders/history", token=ADA.token,
                            error=(500, {"error": "shard unavailable"}))
        book = orders.fetch_character(self.client, self.record(ADA))
        self.assertEqual([o.order_id for o in book.open], [700001, 700002])
        self.assertEqual(book.history, ())
        self.assertFalse(book.history_ok)   # "unknown", never "everything closed"

    def test_corporation_book_is_keyed_by_the_corporation(self):
        book = orders.fetch_corporation(self.client, self.record(ADA), self.public(ADA))
        self.assertEqual(book.owner_key, f"corp:{CORP_SHARED}")
        self.assertEqual(book.owner_name, "Shared Ledger Holdings")
        self.assertEqual([o.order_id for o in book.open], [700201, 700202])
        self.assertEqual([o.order_id for o in book.history], [700301])
        # Who placed it and which wallet division holds the escrow belong to corporation rows.
        self.assertEqual(book.open[0].issued_by, MIRA.character_id)
        self.assertEqual(book.open[0].wallet_division, 2)

    def test_refusal_without_the_consent_asks_for_the_consent(self):
        self.env.server.get(f"/corporations/{CORP_SHARED}/orders", error=(403, {"error": "Forbidden"}))
        with self.assertRaises(orders.OrderAccess) as ctx:
            orders.fetch_corporation(self.client, self.record(ADA, without=orders.CORPORATION_SCOPE),
                                     self.public(ADA))
        self.assertIn("no corp-orders consent", str(ctx.exception))
        self.assertIn("login --scopes corp-orders", str(ctx.exception))

    def test_refusal_without_the_roles_consent_says_the_role_is_unverifiable(self):
        self.env.server.get(f"/corporations/{CORP_SHARED}/orders", error=(403, {"error": "Forbidden"}))
        with self.assertRaises(orders.OrderAccess) as ctx:
            orders.fetch_corporation(self.client, self.record(ADA, without=orders.ROLE_SCOPE),
                                     self.public(ADA))
        self.assertIn("role could not be verified", str(ctx.exception))

    def test_refusal_names_the_missing_in_game_role(self):
        # Full consent, and the roles endpoint says Ada holds neither of the two order roles.
        self.env.server.get(f"/corporations/{CORP_SHARED}/orders", error=(403, {"error": "Forbidden"}))
        with self.assertRaises(orders.OrderAccess) as ctx:
            orders.fetch_corporation(self.client, self.record(ADA), self.public(ADA))
        message = str(ctx.exception)
        self.assertIn("holds neither Accountant nor Trader in Shared Ledger Holdings", message)
        self.assertIn("ask a director", message)

    def test_refusal_does_not_blame_the_role_when_it_is_held(self):
        self.env.server.get(f"/corporations/{CORP_SHARED}/orders", error=(403, {"error": "Forbidden"}))
        self.env.server.get(f"/characters/{ADA.character_id}/roles", token=ADA.token,
                            doc={"roles": {"Accountant": 1, "Station_Manager": 0}, "titles": []})
        with self.assertRaises(orders.OrderAccess) as ctx:
            orders.fetch_corporation(self.client, self.record(ADA), self.public(ADA))
        # Ada holds the role, so telling her to go get it would send her to the wrong fix.
        self.assertIn("even though it holds Accountant", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
