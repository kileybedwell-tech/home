"""AirDrop intake, eBay sold sync and ending listings for crosslisting."""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ebay import airdrop, cli, trading  # noqa: E402
from ebay.inventory import InventoryStore  # noqa: E402


def fake_jpeg(src: Path, dest: Path) -> None:
    shutil.copy(src, dest)


class GroupByGapTest(unittest.TestCase):
    def test_splits_on_gaps_longer_than_the_limit(self):
        stamped = [(100.0, Path("a")), (105.0, Path("b")), (300.0, Path("c")), (310.0, Path("d"))]
        self.assertEqual(
            airdrop.group_by_gap(stamped, gap=60),
            [[Path("a"), Path("b")], [Path("c"), Path("d")]],
        )

    def test_sorts_by_arrival_first(self):
        stamped = [(300.0, Path("c")), (100.0, Path("a"))]
        self.assertEqual(airdrop.group_by_gap(stamped, gap=60), [[Path("a")], [Path("c")]])


@mock.patch.object(airdrop, "_to_jpeg", fake_jpeg)
class ScanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        base = Path(self.tmp.name)
        self.downloads, self.root, self.state = base / "dl", base / "photos", base / "state.json"
        self.downloads.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def drop(self, name: str, when: float) -> Path:
        path = self.downloads / name
        path.write_bytes(b"img")
        return path

    def scan(self, stamps: dict[str, float], now: float):
        with mock.patch.object(airdrop, "arrived_at", lambda p: stamps[p.name]):
            return airdrop.scan(self.downloads, self.root, self.state, now=now)

    def test_first_scan_only_records_a_starting_point(self):
        self.drop("old.jpg", 50)
        self.assertEqual(self.scan({"old.jpg": 50}, now=100), [])
        self.assertTrue((self.downloads / "old.jpg").exists())
        self.assertEqual(json.loads(self.state.read_text())["since"], 100)

    def test_groups_new_photos_into_item_folders(self):
        self.state.write_text(json.dumps({"since": 100}))
        stamps = {"old.jpg": 90, "IMG_1.HEIC": 200, "IMG_2.HEIC": 205, "IMG_3.jpg": 400, "notes.pdf": 210}
        for name, when in stamps.items():
            self.drop(name, when)
        staged = self.scan(stamps, now=1000)

        self.assertEqual([len(s.photos) for s in staged], [2, 1])
        first = staged[0].folder
        self.assertEqual(sorted(p.name for p in first.iterdir()), ["01.jpg", "02.jpg", "originals"])
        self.assertEqual(sorted(p.name for p in (first / "originals").iterdir()), ["IMG_1.HEIC", "IMG_2.HEIC"])
        # Old photos and non-photos stay in Downloads; staged ones leave it.
        self.assertEqual(sorted(p.name for p in self.downloads.iterdir()), ["notes.pdf", "old.jpg"])
        self.assertEqual(json.loads(self.state.read_text())["since"], 400)

    def test_waits_while_a_send_is_still_arriving(self):
        self.state.write_text(json.dumps({"since": 100}))
        self.drop("IMG_1.jpg", 200)
        self.assertEqual(self.scan({"IMG_1.jpg": 200}, now=205), [])
        self.assertTrue((self.downloads / "IMG_1.jpg").exists())


class SalesToRecordTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = InventoryStore(Path(self.tmp.name) / "inv.json")
        self.by_id = self.store.add("Funko Daredevil")
        self.store.update(self.by_id.id, status="listed", ebay_item_id="111", mercari_url="m1")
        self.by_sku = self.store.add("Card lot")
        self.store.update(self.by_sku.id, status="listed", sku="SKU-2")
        self.unsold = self.store.add("Mug")
        self.store.update(self.unsold.id, status="listed", ebay_item_id="333")

    def tearDown(self):
        self.tmp.cleanup()

    def test_matches_item_id_and_sku_and_skips_cancelled(self):
        orders = [
            {"orderId": "o1", "lineItems": [{"legacyItemId": "111"}]},
            {"orderId": "o2", "lineItems": [{"legacyItemId": "999", "sku": "SKU-2"}]},
            {"orderId": "o3", "lineItems": [{"legacyItemId": "333"}],
             "cancelStatus": {"cancelState": "CANCELED"}},
        ]
        found = cli.sales_to_record(orders, self.store.all())
        self.assertEqual(sorted((i.id, o["orderId"]) for i, o in found),
                         sorted([(self.by_id.id, "o1"), (self.by_sku.id, "o2")]))

    def test_already_sold_items_are_not_reported_again(self):
        self.store.update(self.by_id.id, status="sold")
        orders = [{"orderId": "o1", "lineItems": [{"legacyItemId": "111"}]}]
        self.assertEqual(cli.sales_to_record(orders, self.store.all()), [])

    def test_sold_sync_marks_sold_and_says_to_take_down_mercari(self):
        client = mock.Mock()
        client.orders.return_value = [
            {"orderId": "o1", "creationDate": "2026-09-27T10:00:00Z", "lineItems": [{"legacyItemId": "111"}]}
        ]
        out = io.StringIO()
        with mock.patch.object(cli, "_client", return_value=client), redirect_stdout(out):
            cli.main(["sold-sync", "--file", str(self.store.path)])
        item = self.store.get(self.by_id.id)
        self.assertEqual(item.status, "sold")
        self.assertIn("order o1", item.notes)
        self.assertIn("still listed on Mercari", out.getvalue())

    def test_ended_clears_the_cross_site_reminder(self):
        self.store.update(self.by_id.id, mercari="sold")
        self.assertTrue(cli._cross_site_reminders(self.store.get(self.by_id.id)))
        self.store.update(self.by_id.id, status="ended")
        self.assertEqual(cli._cross_site_reminders(self.store.get(self.by_id.id)), [])


class EndItemTest(unittest.TestCase):
    def test_sends_item_id_and_reason(self):
        from xml.etree import ElementTree
        reply = ElementTree.fromstring(
            f'<EndItemResponse xmlns="{trading._NS}"><Ack>Success</Ack>'
            "<EndTime>2026-09-27T20:00:00.000Z</EndTime></EndItemResponse>"
        )
        with mock.patch.object(trading, "_call", return_value=reply) as call:
            self.assertEqual(trading.end_item(None, None, "123456"), "2026-09-27T20:00:00.000Z")
        name, body = call.call_args[0][2], call.call_args[0][3]
        self.assertEqual(name, "EndItem")
        self.assertIn("<ItemID>123456</ItemID>", body)
        self.assertIn("<EndingReason>NotAvailable</EndingReason>", body)

    def test_rejects_unknown_reason_and_non_numeric_ids(self):
        with self.assertRaises(ValueError):
            trading.end_item(None, None, "1", reason="Sold")
        with self.assertRaises(ValueError):
            trading.end_item(None, None, "1</ItemID><x>")


class PackageTest(unittest.TestCase):
    def draft(self, **package):
        from ebay.listing import ListingDraft
        return ListingDraft(sku="S", title="T", price="1.00", category_id="280",
                            image_urls=["https://x/1.jpg"], package=package)

    def test_pounds_and_inches_become_ebay_units(self):
        item = self.draft(weight_lb=2, length_in=14, width_in=10, height_in=2).inventory_item()
        self.assertEqual(item["packageWeightAndSize"], {
            "weight": {"value": 32.0, "unit": "OUNCE"},
            "dimensions": {"length": 14.0, "width": 10.0, "height": 2.0, "unit": "INCH"},
            "shippingIrregular": False,
        })

    def test_no_package_sends_nothing(self):
        self.assertNotIn("packageWeightAndSize", self.draft().inventory_item())

    def test_partial_dimensions_and_bad_keys_are_rejected(self):
        from ebay.listing import ListingError
        with self.assertRaises(ListingError):
            self.draft(length_in=14, width_in=10).validate()
        with self.assertRaises(ListingError):
            self.draft(weight_kg=1).validate()
        with self.assertRaises(ListingError):
            self.draft(weight_oz=0).validate()


class RequiredPackageTest(unittest.TestCase):
    def draft(self, category, **package):
        from ebay.listing import ListingDraft
        return ListingDraft(sku="S", title="T", price="1.00", category_id=category,
                            image_urls=["https://x/1.jpg"], package=package)

    def test_other_categories_must_say_their_package(self):
        from ebay.listing import ListingError
        d = self.draft("15230")
        d.fill_default_package()
        with self.assertRaises(ListingError) as ctx:
            d.validate()
        self.assertIn("needs a weight", str(ctx.exception))
        self.assertIn("length_in", str(ctx.exception))

    def test_cards_and_magazines_get_the_store_standard(self):
        card = self.draft("261328"); card.fill_default_package(); card.validate()
        self.assertEqual(card.package_weight_and_size()["weight"]["value"], 1.0)
        mag = self.draft("280"); mag.fill_default_package(); mag.validate()
        self.assertEqual(mag.package_weight_and_size()["dimensions"]["length"], 14.0)

    def test_a_draft_value_beats_the_default(self):
        mag = self.draft("280", weight_oz=12)
        mag.fill_default_package()
        pkg = mag.package_weight_and_size()
        self.assertEqual(pkg["weight"]["value"], 12.0)
        self.assertEqual(pkg["dimensions"]["height"], 2.0)

    def test_create_fails_before_uploading_photos(self):
        from ebay.listing import ListingError, create_listing
        client = mock.Mock()
        with self.assertRaises(ListingError):
            create_listing(client, self.draft("15230"), photos=["a.jpg"])
        client.upload_image.assert_not_called()

    def test_flags_become_a_package(self):
        args = cli.build_parser().parse_args(
            ["create", "S", "--title", "T", "--price", "1", "--category", "1",
             "--weight-oz", "8", "--dimensions", "10x8x4"])
        self.assertEqual(cli._package_flags(args),
                         {"weight_oz": 8.0, "length_in": 10.0, "width_in": 8.0, "height_in": 4.0})


if __name__ == "__main__":
    unittest.main()
