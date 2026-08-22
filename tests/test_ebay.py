"""Unit tests. No network: the transport is stubbed at the seam."""

from __future__ import annotations

import json
import os
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ebay import auth, client as client_mod, config as config_mod, http  # noqa: E402
from ebay.auth import AuthError, TokenStore, Tokens  # noqa: E402
from ebay.client import EbayClient  # noqa: E402
from ebay.config import PRODUCTION, SANDBOX, Config, ConfigError  # noqa: E402
from ebay.http import EbayError  # noqa: E402


def make_config(**overrides):
    base = dict(
        client_id="APP-ID-123",
        client_secret="SECRET-456",
        redirect_uri="Kiley-RuName-abc",
        environment=PRODUCTION,
    )
    base.update(overrides)
    return Config(**base)


class ConfigTests(unittest.TestCase):
    def test_hosts_differ_per_environment(self):
        self.assertEqual(make_config().token_url, "https://api.ebay.com/identity/v1/oauth2/token")
        sandbox = make_config(environment=SANDBOX)
        self.assertEqual(sandbox.api_host, "https://api.sandbox.ebay.com")
        self.assertEqual(sandbox.authorize_url, "https://auth.sandbox.ebay.com/oauth2/authorize")

    def test_scopes_always_use_the_production_scope_root(self):
        # A sandbox app still requests api.ebay.com scope identifiers.
        for scope in make_config(environment=SANDBOX).scopes:
            self.assertTrue(scope.startswith("https://api.ebay.com/oauth/api_scope"))

    def test_unknown_environment_rejected(self):
        with self.assertRaises(ConfigError):
            make_config(environment="staging")

    def test_from_env_reports_every_missing_variable(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ConfigError) as ctx:
                Config.from_env()
        self.assertIn("EBAY_CLIENT_ID", str(ctx.exception))
        self.assertIn("EBAY_CLIENT_SECRET", str(ctx.exception))

    def test_from_env_reads_overrides(self):
        env = {
            "EBAY_CLIENT_ID": "id",
            "EBAY_CLIENT_SECRET": "secret",
            "EBAY_MARKETPLACE_ID": "EBAY_GB",
            "EBAY_ENVIRONMENT": SANDBOX,
        }
        with mock.patch.dict(os.environ, env, clear=True):
            config = Config.from_env()
        self.assertEqual(config.marketplace_id, "EBAY_GB")
        self.assertEqual(config.environment, SANDBOX)

    def test_load_dotenv_does_not_clobber_real_env(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text('EBAY_CLIENT_ID="from-file"\n# comment\nEBAY_MARKETPLACE_ID=EBAY_AU\n')
            with mock.patch.dict(os.environ, {"EBAY_CLIENT_ID": "from-shell"}, clear=True):
                config_mod.load_dotenv(path)
                self.assertEqual(os.environ["EBAY_CLIENT_ID"], "from-shell")
                self.assertEqual(os.environ["EBAY_MARKETPLACE_ID"], "EBAY_AU")


class AuthorizationUrlTests(unittest.TestCase):
    def test_contains_client_id_redirect_and_scopes(self):
        url = auth.authorization_url(make_config(), state="xyz")
        self.assertTrue(url.startswith("https://auth.ebay.com/oauth2/authorize?"))
        self.assertIn("client_id=APP-ID-123", url)
        self.assertIn("redirect_uri=Kiley-RuName-abc", url)
        self.assertIn("response_type=code", url)
        self.assertIn("state=xyz", url)

    def test_missing_runame_is_a_clear_error(self):
        with self.assertRaises(AuthError) as ctx:
            auth.authorization_url(make_config(redirect_uri=""))
        self.assertIn("RuName", str(ctx.exception))

    def test_parse_code_from_full_redirect_url(self):
        code = auth.parse_authorization_code(
            "https://example.com/cb?code=v%5E1.1%23abc%3D%3D&expires_in=299"
        )
        self.assertEqual(code, "v^1.1#abc==")

    def test_parse_code_from_bare_value(self):
        self.assertEqual(auth.parse_authorization_code(" v%5E1.1 "), "v^1.1")

    def test_declined_authorization_surfaces_ebay_error(self):
        with self.assertRaises(AuthError) as ctx:
            auth.parse_authorization_code(
                "https://example.com/cb?error=access_denied&error_description=nope"
            )
        self.assertIn("access_denied", str(ctx.exception))


class TokenTests(unittest.TestCase):
    def test_exchange_code_uses_basic_auth_and_authorization_code_grant(self):
        captured = {}

        def fake_request(method, url, *, headers=None, form_body=None, **kwargs):
            captured.update(method=method, url=url, headers=headers, form=form_body)
            return {
                "access_token": "AT",
                "refresh_token": "RT",
                "expires_in": 7200,
                "refresh_token_expires_in": 47304000,
            }

        with mock.patch.object(auth, "request", fake_request):
            tokens = auth.exchange_code(make_config(), "the-code")

        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["url"], "https://api.ebay.com/identity/v1/oauth2/token")
        # base64("APP-ID-123:SECRET-456")
        self.assertEqual(captured["headers"]["Authorization"], "Basic QVBQLUlELTEyMzpTRUNSRVQtNDU2")
        self.assertEqual(captured["form"]["grant_type"], "authorization_code")
        self.assertEqual(captured["form"]["code"], "the-code")
        self.assertEqual(captured["form"]["redirect_uri"], "Kiley-RuName-abc")
        self.assertEqual(tokens.access_token, "AT")
        self.assertGreater(tokens.refresh_expires_at, tokens.access_expires_at)

    def test_refresh_response_carries_the_old_refresh_token_forward(self):
        now = time.time()
        old = Tokens("old-at", "RT", now - 10, now + 1000, scopes=("s1",))
        with mock.patch.object(
            auth, "request", lambda *a, **k: {"access_token": "new-at", "expires_in": 7200}
        ):
            fresh = auth.refresh_tokens(make_config(), old)
        self.assertEqual(fresh.access_token, "new-at")
        self.assertEqual(fresh.refresh_token, "RT")
        self.assertEqual(fresh.refresh_expires_at, old.refresh_expires_at)
        self.assertEqual(fresh.scopes, ("s1",))

    def test_refresh_scope_defaults_to_the_originally_granted_scopes(self):
        captured = {}
        now = time.time()
        old = Tokens("at", "RT", now - 10, now + 1000, scopes=("scope-a", "scope-b"))

        def fake_request(method, url, *, headers=None, form_body=None, **kwargs):
            captured.update(form_body)
            return {"access_token": "new", "expires_in": 7200}

        with mock.patch.object(auth, "request", fake_request):
            auth.refresh_tokens(make_config(), old)
        self.assertEqual(captured["scope"], "scope-a scope-b")

    def test_expired_refresh_token_refuses_to_call_ebay(self):
        stale = Tokens("at", "RT", 0, time.time() - 1)
        with mock.patch.object(auth, "request", mock.Mock(side_effect=AssertionError)):
            with self.assertRaises(AuthError):
                auth.refresh_tokens(make_config(), stale)

    def test_malformed_token_response_is_rejected(self):
        with mock.patch.object(auth, "request", lambda *a, **k: {"error": "invalid_grant"}):
            with self.assertRaises(AuthError):
                auth.exchange_code(make_config(), "bad")


class TokenStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.path = Path(self._tmp.name) / "tokens.json"
        self.addCleanup(self._tmp.cleanup)

    def test_round_trip_is_private_to_the_owner(self):
        store = TokenStore(make_config(), self.path)
        now = time.time()
        store.save(Tokens("AT", "RT", now + 7200, now + 100000, scopes=("s",)))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        reloaded = TokenStore(make_config(), self.path).load()
        self.assertEqual(reloaded.access_token, "AT")
        self.assertEqual(reloaded.scopes, ("s",))

    def test_access_token_refreshes_and_persists_when_stale(self):
        store = TokenStore(make_config(), self.path)
        now = time.time()
        store.save(Tokens("stale", "RT", now + 60, now + 100000))  # inside the skew
        with mock.patch.object(
            auth, "request", lambda *a, **k: {"access_token": "fresh", "expires_in": 7200}
        ):
            self.assertEqual(store.access_token(), "fresh")
        on_disk = json.loads(self.path.read_text())
        self.assertEqual(on_disk["access_token"], "fresh")

    def test_valid_token_is_reused_without_a_network_call(self):
        store = TokenStore(make_config(), self.path)
        now = time.time()
        store.save(Tokens("good", "RT", now + 7200, now + 100000))
        with mock.patch.object(auth, "request", mock.Mock(side_effect=AssertionError)):
            self.assertEqual(store.access_token(), "good")

    def test_missing_file_tells_the_user_to_log_in(self):
        with self.assertRaises(AuthError) as ctx:
            TokenStore(make_config(), self.path).access_token()
        self.assertIn("login", str(ctx.exception))

    def test_token_path_honours_xdg_and_environment(self):
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/cfg"}, clear=True):
            self.assertEqual(
                auth.default_token_path(SANDBOX), Path("/cfg/ebay-connect/sandbox.json")
            )


class FakeTokens:
    path = Path("/dev/null")

    def access_token(self):
        return "TOKEN"


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.responses = []
        self.client = EbayClient(make_config(marketplace_id="EBAY_GB"), FakeTokens())

        def fake_request(method, url, *, headers=None, json_body=None, **kwargs):
            self.calls.append({"method": method, "url": url, "headers": headers, "body": json_body})
            return self.responses.pop(0) if self.responses else {}

        patcher = mock.patch.object(client_mod, "request", fake_request)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_every_call_carries_bearer_token_and_marketplace(self):
        self.client.privileges()
        headers = self.calls[0]["headers"]
        self.assertEqual(headers["Authorization"], "Bearer TOKEN")
        self.assertEqual(headers["X-EBAY-C-MARKETPLACE-ID"], "EBAY_GB")
        self.assertNotIn("Content-Language", headers)  # only on writes

    def test_writes_add_content_language(self):
        self.client.upsert_inventory_item("SKU1", {"product": {"title": "t"}})
        self.assertEqual(self.calls[0]["headers"]["Content-Language"], "en-US")
        self.assertEqual(self.calls[0]["method"], "PUT")

    def test_sku_is_url_encoded_in_the_path(self):
        self.client.get_inventory_item("A/B C")
        self.assertTrue(self.calls[0]["url"].endswith("/inventory_item/A%2FB%20C"))

    def test_pagination_walks_pages_and_stops_on_total(self):
        self.responses = [
            {"inventoryItems": [{"sku": "a"}, {"sku": "b"}], "total": 3},
            {"inventoryItems": [{"sku": "c"}], "total": 3},
        ]
        skus = [i["sku"] for i in self.client.inventory_items()]
        self.assertEqual(skus, ["a", "b", "c"])
        self.assertIn("offset=0", self.calls[0]["url"])
        self.assertIn("offset=2", self.calls[1]["url"])

    def test_pagination_stops_on_an_empty_page_without_a_total(self):
        self.responses = [{"inventoryItems": [{"sku": "a"}]}, {"inventoryItems": []}]
        self.assertEqual(len(list(self.client.inventory_items())), 1)
        self.assertEqual(len(self.calls), 2)

    def test_max_items_caps_both_results_and_page_size(self):
        self.responses = [{"inventoryItems": [{"sku": "a"}, {"sku": "b"}], "total": 99}]
        self.assertEqual(len(list(self.client.inventory_items(max_items=2))), 2)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("limit=2", self.calls[0]["url"])

    def test_order_filter_is_passed_through(self):
        self.responses = [{"orders": [], "total": 0}]
        list(self.client.orders(order_filter="orderfulfillmentstatus:{NOT_STARTED}"))
        self.assertIn("filter=", self.calls[0]["url"])

    def test_none_params_are_dropped_from_the_query(self):
        self.responses = [{"orders": [], "total": 0}]
        list(self.client.orders(order_filter=None))
        self.assertNotIn("filter", self.calls[0]["url"])

    def test_quantity_only_update_skips_the_offer_lookup(self):
        self.client.update_price_quantity("SKU1", quantity=4)
        self.assertEqual(len(self.calls), 1)
        request_body = self.calls[0]["body"]["requests"][0]
        self.assertEqual(request_body["shipToLocationAvailability"], {"quantity": 4})
        self.assertNotIn("offers", request_body)

    def test_price_update_reuses_the_offer_currency(self):
        self.responses = [
            {"offers": [{"offerId": "OF1", "pricingSummary": {"price": {"currency": "GBP"}}}]},
            {},
        ]
        self.client.update_price_quantity("SKU1", price="24.99")
        offers = self.calls[-1]["body"]["requests"][0]["offers"]
        self.assertEqual(offers, [{"offerId": "OF1", "price": {"value": "24.99", "currency": "GBP"}}])

    def test_price_update_without_an_offer_is_a_clear_error(self):
        self.responses = [{"offers": []}]
        with self.assertRaises(ValueError):
            self.client.update_price_quantity("SKU1", price="9.99")

    def test_empty_update_is_rejected_before_any_call(self):
        with self.assertRaises(ValueError):
            self.client.update_price_quantity("SKU1")
        self.assertEqual(self.calls, [])


class HttpErrorTests(unittest.TestCase):
    def test_error_message_uses_ebays_error_array(self):
        payload = {
            "errors": [
                {
                    "errorId": 25002,
                    "longMessage": "A user error has occurred. Duplicate SKU.",
                    "parameters": [{"name": "sku", "value": "SKU1"}],
                }
            ]
        }
        error = EbayError(400, "https://api.ebay.com/x", payload, json.dumps(payload))
        text = str(error)
        self.assertIn("25002", text)
        self.assertIn("Duplicate SKU", text)
        self.assertIn("sku=SKU1", text)
        self.assertEqual(len(error.errors), 1)

    def test_non_json_error_falls_back_to_the_body(self):
        error = EbayError(502, "https://api.ebay.com/x", "<html>bad gateway</html>", "<html>bad gateway</html>")
        self.assertIn("502", str(error))

    def test_retry_after_is_clamped(self):
        self.assertEqual(http._retry_after("5"), 5.0)
        self.assertEqual(http._retry_after("9999"), 60.0)
        self.assertIsNone(http._retry_after("Wed, 21 Oct 2026 07:28:00 GMT"))
        self.assertIsNone(http._retry_after(None))

    def test_json_and_form_bodies_are_mutually_exclusive(self):
        with self.assertRaises(ValueError):
            http.request("POST", "https://x", json_body={}, form_body={})


if __name__ == "__main__":
    unittest.main(verbosity=2)


# ---- listing creation ---------------------------------------------------

from ebay import listing as listing_mod  # noqa: E402
from ebay.listing import CONDITIONS, ListingDraft, ListingError, create_listing  # noqa: E402


def make_draft(**overrides):
    base = dict(
        sku="CAM-1",
        title="Canon AE-1 35mm Film Camera",
        price="189.00",
        category_id="15230",
        image_urls=["https://img.example.com/a.jpg"],
    )
    base.update(overrides)
    return ListingDraft(**base)


class DraftValidationTests(unittest.TestCase):
    def test_a_good_draft_validates(self):
        make_draft().validate()  # must not raise

    def test_every_problem_is_reported_at_once(self):
        draft = make_draft(sku=" ", title="", price="free", quantity=0, condition="MINT",
                           category_id="", image_urls=[])
        with self.assertRaises(ListingError) as ctx:
            draft.validate()
        message = str(ctx.exception)
        for expected in ("sku", "title", "price", "quantity", "condition", "category"):
            self.assertIn(expected, message)

    def test_title_over_the_ebay_limit_is_rejected_with_its_length(self):
        with self.assertRaises(ListingError) as ctx:
            make_draft(title="x" * 81).validate()
        self.assertIn("81 characters", str(ctx.exception))

    def test_title_at_the_limit_is_allowed(self):
        make_draft(title="x" * 80).validate()

    def test_http_image_urls_are_rejected(self):
        with self.assertRaises(ListingError) as ctx:
            make_draft(image_urls=["http://img.example.com/a.jpg"]).validate()
        self.assertIn("https", str(ctx.exception))

    def test_missing_images_is_rejected(self):
        with self.assertRaises(ListingError):
            make_draft(image_urls=[]).validate()

    def test_zero_price_is_rejected(self):
        with self.assertRaises(ListingError):
            make_draft(price="0").validate()

    def test_every_documented_condition_is_accepted(self):
        for condition in CONDITIONS:
            make_draft(condition=condition).validate()


class PayloadTests(unittest.TestCase):
    def test_inventory_item_shape(self):
        item = make_draft(
            description="Fully working.",
            quantity=2,
            condition="USED_EXCELLENT",
            condition_description="Light brassing.",
            aspects={"Brand": ["Canon"]},
        ).inventory_item()
        self.assertEqual(item["condition"], "USED_EXCELLENT")
        self.assertEqual(item["conditionDescription"], "Light brassing.")
        self.assertEqual(item["availability"]["shipToLocationAvailability"]["quantity"], 2)
        self.assertEqual(item["product"]["aspects"], {"Brand": ["Canon"]})
        self.assertEqual(item["product"]["imageUrls"], ["https://img.example.com/a.jpg"])

    def test_optional_product_fields_are_omitted_when_unset(self):
        product = make_draft().inventory_item()["product"]
        self.assertNotIn("description", product)
        self.assertNotIn("aspects", product)

    def test_offer_shape_carries_marketplace_policies_and_location(self):
        config = make_config(marketplace_id="EBAY_GB")
        offer = make_draft(quantity=3, currency="GBP").offer(
            config, {"paymentPolicyId": "P1"}, "warehouse-1"
        )
        self.assertEqual(offer["marketplaceId"], "EBAY_GB")
        self.assertEqual(offer["format"], "FIXED_PRICE")
        self.assertEqual(offer["availableQuantity"], 3)
        self.assertEqual(offer["merchantLocationKey"], "warehouse-1")
        self.assertEqual(offer["listingPolicies"], {"paymentPolicyId": "P1"})
        self.assertEqual(offer["pricingSummary"]["price"],
                         {"value": "189.00", "currency": "GBP"})

    def test_listing_description_falls_back_to_the_title(self):
        offer = make_draft().offer(make_config(), {}, "loc")
        self.assertEqual(offer["listingDescription"], "Canon AE-1 35mm Film Camera")


class FakeClient:
    """Records calls and returns scripted values, without any HTTP."""

    def __init__(self, **scripted):
        self.config = make_config()
        self.calls = []
        self.scripted = {
            "fulfillment_policies": [{"fulfillmentPolicyId": "F1", "name": "Ground"}],
            "payment_policies": [{"paymentPolicyId": "P1", "name": "Immediate"}],
            "return_policies": [{"returnPolicyId": "R1", "name": "30 day"}],
            "inventory_locations": [{"merchantLocationKey": "store-1", "name": "Store"}],
            "offers_for_sku": [],
            "create_offer": {"offerId": "OF-1"},
            "publish_offer": {"listingId": "1102345"},
        }
        self.scripted.update(scripted)

    def _record(self, name, *args):
        self.calls.append((name, *args))
        return self.scripted.get(name)

    def fulfillment_policies(self): return self._record("fulfillment_policies")
    def payment_policies(self): return self._record("payment_policies")
    def return_policies(self): return self._record("return_policies")
    def inventory_locations(self): return self._record("inventory_locations")
    def offers_for_sku(self, sku): return self._record("offers_for_sku", sku)
    def upsert_inventory_item(self, sku, item): return self._record("upsert_inventory_item", sku, item)
    def create_offer(self, offer): return self._record("create_offer", offer)
    def update_offer(self, offer_id, offer): return self._record("update_offer", offer_id, offer)
    def publish_offer(self, offer_id): return self._record("publish_offer", offer_id)

    def names(self):
        return [call[0] for call in self.calls]


class ResolutionTests(unittest.TestCase):
    def test_single_policy_of_each_kind_is_selected_automatically(self):
        resolved = listing_mod.resolve_policies(FakeClient())
        self.assertEqual(
            resolved,
            {"fulfillmentPolicyId": "F1", "paymentPolicyId": "P1", "returnPolicyId": "R1"},
        )

    def test_overrides_short_circuit_the_lookup(self):
        client = FakeClient()
        resolved = listing_mod.resolve_policies(client, {"paymentPolicyId": "MINE"})
        self.assertEqual(resolved["paymentPolicyId"], "MINE")
        self.assertNotIn("payment_policies", client.names())

    def test_several_policies_is_ambiguous_and_lists_the_options(self):
        client = FakeClient(payment_policies=[
            {"paymentPolicyId": "P1", "name": "Immediate"},
            {"paymentPolicyId": "P2", "name": "Invoice"},
        ])
        with self.assertRaises(ListingError) as ctx:
            listing_mod.resolve_policies(client)
        message = str(ctx.exception)
        self.assertIn("--payment-policy", message)
        self.assertIn("P1", message)
        self.assertIn("P2", message)

    def test_no_policy_explains_how_to_create_one(self):
        with self.assertRaises(ListingError) as ctx:
            listing_mod.resolve_policies(FakeClient(return_policies=[]))
        self.assertIn("Business policies", str(ctx.exception))

    def test_disabled_locations_are_ignored_when_an_enabled_one_exists(self):
        client = FakeClient(inventory_locations=[
            {"merchantLocationKey": "old", "merchantLocationStatus": "DISABLED"},
            {"merchantLocationKey": "current", "merchantLocationStatus": "ENABLED"},
        ])
        self.assertEqual(listing_mod.resolve_location(client), "current")

    def test_location_falls_back_when_all_are_disabled(self):
        client = FakeClient(inventory_locations=[
            {"merchantLocationKey": "only", "merchantLocationStatus": "DISABLED"},
        ])
        self.assertEqual(listing_mod.resolve_location(client), "only")

    def test_no_location_is_a_clear_error(self):
        with self.assertRaises(ListingError) as ctx:
            listing_mod.resolve_location(FakeClient(inventory_locations=[]))
        self.assertIn("cannot publish", str(ctx.exception))

    def test_several_locations_is_ambiguous(self):
        client = FakeClient(inventory_locations=[
            {"merchantLocationKey": "a"}, {"merchantLocationKey": "b"},
        ])
        with self.assertRaises(ListingError) as ctx:
            listing_mod.resolve_location(client)
        self.assertIn("--location", str(ctx.exception))


class CreateListingTests(unittest.TestCase):
    def test_happy_path_runs_item_then_offer_then_publish(self):
        client = FakeClient()
        result = create_listing(client, make_draft())
        self.assertEqual(
            client.names()[-4:],
            ["upsert_inventory_item", "offers_for_sku", "create_offer", "publish_offer"],
        )
        self.assertEqual(result["offerId"], "OF-1")
        self.assertEqual(result["listingId"], "1102345")
        self.assertTrue(result["published"])
        self.assertFalse(result["offerReused"])

    def test_draft_mode_stops_before_publishing(self):
        client = FakeClient()
        result = create_listing(client, make_draft(), publish=False)
        self.assertNotIn("publish_offer", client.names())
        self.assertFalse(result["published"])

    def test_existing_offer_is_updated_rather_than_duplicated(self):
        client = FakeClient(offers_for_sku=[{"offerId": "OF-EXISTING"}])
        result = create_listing(client, make_draft())
        self.assertIn("update_offer", client.names())
        self.assertNotIn("create_offer", client.names())
        self.assertEqual(result["offerId"], "OF-EXISTING")
        self.assertTrue(result["offerReused"])

    def test_validation_runs_before_any_network_call(self):
        client = FakeClient()
        with self.assertRaises(ListingError):
            create_listing(client, make_draft(title=""))
        self.assertEqual(client.calls, [])

    def test_dry_run_touches_nothing_and_returns_both_payloads(self):
        client = FakeClient()
        result = create_listing(client, make_draft(), dry_run=True)
        self.assertEqual(client.calls, [])
        self.assertTrue(result["dryRun"])
        self.assertIn("inventoryItem", result)
        self.assertIn("offer", result)

    def test_missing_offer_id_in_the_response_is_an_error(self):
        client = FakeClient(create_offer={})
        with self.assertRaises(ListingError) as ctx:
            create_listing(client, make_draft())
        self.assertIn("offerId", str(ctx.exception))

    def test_resolved_ids_reach_the_offer_payload(self):
        client = FakeClient()
        create_listing(client, make_draft())
        offer = next(c[1] for c in client.calls if c[0] == "create_offer")
        self.assertEqual(offer["listingPolicies"]["fulfillmentPolicyId"], "F1")
        self.assertEqual(offer["merchantLocationKey"], "store-1")


class AspectParsingTests(unittest.TestCase):
    def test_repeated_flags_group_by_name(self):
        from ebay.cli import _parse_aspects
        self.assertEqual(
            _parse_aspects(["Brand=Canon", "Colour=Black", "Colour=Silver"]),
            {"Brand": ["Canon"], "Colour": ["Black", "Silver"]},
        )

    def test_malformed_pairs_are_rejected(self):
        from ebay.cli import _parse_aspects
        for bad in ("Brand", "=Canon", "Brand="):
            with self.assertRaises(ValueError):
                _parse_aspects([bad])

    def test_no_aspects_is_an_empty_dict(self):
        from ebay.cli import _parse_aspects
        self.assertEqual(_parse_aspects(None), {})

    def test_dry_run_names_each_policy_slot_it_would_resolve(self):
        result = create_listing(FakeClient(), make_draft(), dry_run=True)
        policies = result["offer"]["listingPolicies"]
        self.assertEqual(
            sorted(policies),
            ["fulfillmentPolicyId", "paymentPolicyId", "returnPolicyId"],
        )
        self.assertTrue(all("resolved at run time" in v for v in policies.values()))

    def test_dry_run_shows_overrides_it_was_given(self):
        result = create_listing(
            FakeClient(), make_draft(), policy_overrides={"paymentPolicyId": "MINE"},
            dry_run=True,
        )
        self.assertEqual(result["offer"]["listingPolicies"]["paymentPolicyId"], "MINE")


# ---- setup wizard -------------------------------------------------------

from ebay.config import check_credentials, write_env_file  # noqa: E402


class CredentialCheckTests(unittest.TestCase):
    def test_a_sane_set_produces_no_warnings(self):
        self.assertEqual(
            check_credentials(
                {
                    "EBAY_CLIENT_ID": "KileyB-App-PRD-abc-123",
                    "EBAY_REDIRECT_URI": "Kiley_B-KileyB-App-abcdef",
                    "EBAY_ENVIRONMENT": PRODUCTION,
                }
            ),
            [],
        )

    def test_runame_pasted_as_a_url_is_caught(self):
        for url in ("https://example.com/cb", "http://example.com/cb"):
            warnings = check_credentials({"EBAY_REDIRECT_URI": url})
            self.assertTrue(any("RuName" in w for w in warnings), url)

    def test_sandbox_key_on_production_is_caught(self):
        warnings = check_credentials(
            {"EBAY_CLIENT_ID": "KileyB-App-SBX-abc", "EBAY_ENVIRONMENT": PRODUCTION}
        )
        self.assertTrue(any("Sandbox key" in w for w in warnings))

    def test_production_key_on_sandbox_is_caught(self):
        warnings = check_credentials(
            {"EBAY_CLIENT_ID": "KileyB-App-PRD-abc", "EBAY_ENVIRONMENT": SANDBOX}
        )
        self.assertTrue(any("Production key" in w for w in warnings))

    def test_matching_key_and_environment_pass(self):
        self.assertEqual(
            check_credentials(
                {"EBAY_CLIENT_ID": "KileyB-App-SBX-abc", "EBAY_ENVIRONMENT": SANDBOX}
            ),
            [],
        )


class WriteEnvTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.path = Path(self._tmp.name) / ".env"
        self.addCleanup(self._tmp.cleanup)

    def test_written_file_is_private_and_round_trips(self):
        write_env_file(
            self.path,
            {
                "EBAY_CLIENT_ID": "id-1",
                "EBAY_CLIENT_SECRET": "secret-1",
                "EBAY_REDIRECT_URI": "Ru-Name",
                "EBAY_ENVIRONMENT": SANDBOX,
                "EBAY_MARKETPLACE_ID": "EBAY_GB",
                "EBAY_CONTENT_LANGUAGE": "en-GB",
            },
        )
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        with mock.patch.dict(os.environ, {}, clear=True):
            config_mod.load_dotenv(self.path)
            config = Config.from_env()
        self.assertEqual(config.client_id, "id-1")
        self.assertEqual(config.redirect_uri, "Ru-Name")
        self.assertEqual(config.environment, SANDBOX)
        self.assertEqual(config.marketplace_id, "EBAY_GB")

    def test_absent_keys_are_omitted_entirely(self):
        write_env_file(self.path, {"EBAY_CLIENT_ID": "only-this"})
        text = self.path.read_text()
        self.assertIn("EBAY_CLIENT_ID=only-this", text)
        self.assertNotIn("EBAY_CLIENT_SECRET", text)

    def test_no_leftover_temp_file(self):
        write_env_file(self.path, {"EBAY_CLIENT_ID": "x"})
        siblings = [p.name for p in self.path.parent.iterdir()]
        self.assertEqual(siblings, [".env"])
