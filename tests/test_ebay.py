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

    def test_item_condition_policy_returns_the_first_matching_category(self):
        self.responses = [{"itemConditionPolicies": [{"categoryId": "183454"}]}]
        policy = self.client.item_condition_policy("183454")
        self.assertEqual(policy, {"categoryId": "183454"})
        self.assertIn("filter=categoryIds", self.calls[0]["url"])
        self.assertIn("183454", self.calls[0]["url"])

    def test_item_condition_policy_is_empty_dict_when_no_policy_found(self):
        self.responses = [{"itemConditionPolicies": []}]
        self.assertEqual(self.client.item_condition_policy("183454"), {})

    def test_empty_update_is_rejected_before_any_call(self):
        with self.assertRaises(ValueError):
            self.client.update_price_quantity("SKU1")
        self.assertEqual(self.calls, [])

    def test_offers_for_sku_treats_a_404_as_no_offers(self):
        # eBay returns 404/25713 "This Offer is not available" for this
        # endpoint - not an empty list - when a SKU has no offer yet, which
        # is the normal state for a brand-new SKU, not an error.
        def raise_404(method, url, **kwargs):
            raise EbayError(404, url, {"errors": [{"errorId": 25713}]}, "")

        with mock.patch.object(client_mod, "request", raise_404):
            self.assertEqual(self.client.offers_for_sku("SKU1"), [])

    def test_offers_for_sku_reraises_other_errors(self):
        def raise_500(method, url, **kwargs):
            raise EbayError(500, url, {}, "boom")

        with mock.patch.object(client_mod, "request", raise_500):
            with self.assertRaises(EbayError):
                self.client.offers_for_sku("SKU1")


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

    def test_condition_descriptors_are_omitted_when_unset(self):
        self.assertNotIn("conditionDescriptors", make_draft().inventory_item())

    def test_condition_descriptors_shape(self):
        # Trading-card categories require this alongside `condition` - see
        # `python -m ebay condition-policy CATEGORY` for the ids to use.
        item = make_draft(
            condition="USED_VERY_GOOD",
            condition_descriptors={"40001": ["400010"]},
        ).inventory_item()
        self.assertEqual(
            item["conditionDescriptors"],
            [{"name": "40001", "values": ["400010"]}],
        )

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

    def test_condition_descriptors_group_by_id(self):
        from ebay.cli import _parse_condition_descriptors
        self.assertEqual(
            _parse_condition_descriptors(["40001=400010"]),
            {"40001": ["400010"]},
        )

    def test_condition_descriptor_malformed_pairs_are_rejected(self):
        from ebay.cli import _parse_condition_descriptors
        for bad in ("40001", "=400010", "40001="):
            with self.assertRaises(ValueError):
                _parse_condition_descriptors([bad])

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


# ---- images -------------------------------------------------------------

from ebay.http import encode_multipart  # noqa: E402
from ebay.listing import upload_photos  # noqa: E402


class MultipartTests(unittest.TestCase):
    def _reparse(self, body, content_type):
        """Parse the encoded body back with the stdlib, as a server would."""
        import email

        message = email.message_from_bytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode()
            + body
        )
        self.assertTrue(message.is_multipart())
        return message.get_payload()[0]

    def test_round_trips_through_a_real_mime_parser(self):
        content = b"\xff\xd8\xff\xe0 not really a jpeg \x00\x01"
        body, content_type = encode_multipart("image", "cam.jpg", content, "image/jpeg")
        part = self._reparse(body, content_type)
        self.assertEqual(part.get_payload(decode=True), content)
        self.assertEqual(part.get_content_type(), "image/jpeg")
        self.assertIn('name="image"', part.get("Content-Disposition"))
        self.assertIn('filename="cam.jpg"', part.get("Content-Disposition"))

    def test_boundary_is_declared_in_the_content_type(self):
        body, content_type = encode_multipart("image", "a.png", b"x", "image/png")
        self.assertTrue(content_type.startswith("multipart/form-data; boundary="))
        boundary = content_type.split("boundary=", 1)[1]
        self.assertIn(boundary.encode(), body)

    def test_encoding_is_deterministic_for_the_same_input(self):
        first = encode_multipart("image", "a.jpg", b"abc", "image/jpeg")
        second = encode_multipart("image", "a.jpg", b"abc", "image/jpeg")
        self.assertEqual(first, second)

    def test_boundary_never_collides_with_the_payload(self):
        # Craft content containing the boundary the hash would first pick.
        probe, content_type = encode_multipart("image", "a.jpg", b"seed", "image/jpeg")
        boundary = content_type.split("boundary=", 1)[1]
        body, new_type = encode_multipart(
            "image", "a.jpg", boundary.encode() + b"seed", "image/jpeg"
        )
        new_boundary = new_type.split("boundary=", 1)[1]
        self.assertNotEqual(new_boundary, boundary)
        self.assertEqual(self._reparse(body, new_type).get_payload(decode=True),
                         boundary.encode() + b"seed")

    def test_quotes_in_a_filename_cannot_break_out_of_the_header(self):
        body, content_type = encode_multipart(
            "image", 'evil".jpg', b"x", "image/jpeg"
        )
        part = self._reparse(body, content_type)
        self.assertEqual(part.get_filename(), "evil.jpg")

    def test_raw_body_requires_a_content_type(self):
        with self.assertRaises(ValueError):
            http.request("POST", "https://x", raw_body=b"data")

    def test_only_one_body_kind_is_allowed(self):
        with self.assertRaises(ValueError):
            http.request("POST", "https://x", json_body={}, raw_body=b"d", content_type="text/plain")


class MediaHostTests(unittest.TestCase):
    def test_media_uses_apim_not_the_api_host(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(make_config().media_host, "https://apim.ebay.com")
            self.assertEqual(
                make_config(environment=SANDBOX).media_host, "https://apim.sandbox.ebay.com"
            )

    def test_media_host_is_overridable(self):
        with mock.patch.dict(os.environ, {"EBAY_MEDIA_HOST": "https://media.test"}, clear=True):
            self.assertEqual(make_config().media_host, "https://media.test")


class ImageUploadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.jpg = self.dir / "cam.jpg"
        self.jpg.write_bytes(b"\xff\xd8\xff\xe0fake jpeg")
        self.addCleanup(self._tmp.cleanup)
        self.calls = []
        self.client = EbayClient(make_config(), FakeTokens())

        def fake_request(method, url, *, headers=None, **kwargs):
            self.calls.append({"method": method, "url": url, "headers": headers, **kwargs})
            if url.endswith("create_image_from_file"):
                return None, {"Location": "https://apim.ebay.com/commerce/media/v1_beta/image/IMG-77"}
            return {"imageUrl": "https://i.ebayimg.com/00/s/IMG-77.jpg"}

        patcher = mock.patch.object(client_mod, "request", fake_request)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_upload_posts_multipart_and_resolves_the_hosted_url(self):
        url = self.client.upload_image(self.jpg)
        self.assertEqual(url, "https://i.ebayimg.com/00/s/IMG-77.jpg")
        post = self.calls[0]
        self.assertEqual(post["method"], "POST")
        self.assertIn("apim.ebay.com/commerce/media/v1_beta", post["url"])
        self.assertTrue(post["content_type"].startswith("multipart/form-data;"))
        self.assertIn(b"fake jpeg", post["raw_body"])
        self.assertEqual(post["headers"]["Authorization"], "Bearer TOKEN")
        self.assertEqual(self.calls[1]["url"].rsplit("/", 1)[-1], "IMG-77")

    def test_image_id_is_taken_from_the_location_header(self):
        self.client.upload_image(self.jpg)
        self.assertTrue(self.calls[1]["url"].endswith("/image/IMG-77"))

    def test_missing_file_is_rejected_before_any_call(self):
        with self.assertRaises(FileNotFoundError):
            self.client.upload_image(self.dir / "nope.jpg")
        self.assertEqual(self.calls, [])

    def test_empty_file_is_rejected(self):
        empty = self.dir / "empty.jpg"
        empty.write_bytes(b"")
        with self.assertRaises(ValueError):
            self.client.upload_image(empty)

    def test_non_image_is_rejected_before_upload(self):
        notes = self.dir / "notes.txt"
        notes.write_text("not a picture")
        with self.assertRaises(ValueError) as ctx:
            self.client.upload_image(notes)
        self.assertIn("does not look like an image", str(ctx.exception))
        self.assertEqual(self.calls, [])

    def test_all_paths_are_checked_before_the_first_upload(self):
        with self.assertRaises(ListingError) as ctx:
            upload_photos(self.client, [str(self.jpg), str(self.dir / "gone.jpg")])
        self.assertIn("gone.jpg", str(ctx.exception))
        self.assertEqual(self.calls, [])


class CreateWithPhotosTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.jpg = Path(self._tmp.name) / "a.jpg"
        self.jpg.write_bytes(b"\xff\xd8fake")
        self.addCleanup(self._tmp.cleanup)

    def test_uploaded_urls_land_in_the_inventory_item(self):
        client = FakeClient()
        client.upload_image = lambda path: "https://i.ebayimg.com/00/s/UP.jpg"
        draft = make_draft(image_urls=[])
        create_listing(client, draft, photos=[str(self.jpg)])
        item = next(c[2] for c in client.calls if c[0] == "upsert_inventory_item")
        self.assertEqual(item["product"]["imageUrls"], ["https://i.ebayimg.com/00/s/UP.jpg"])

    def test_photos_append_to_any_urls_already_given(self):
        client = FakeClient()
        client.upload_image = lambda path: "https://i.ebayimg.com/00/s/UP.jpg"
        draft = make_draft(image_urls=["https://existing.example.com/a.jpg"])
        create_listing(client, draft, photos=[str(self.jpg)])
        self.assertEqual(
            draft.image_urls,
            ["https://existing.example.com/a.jpg", "https://i.ebayimg.com/00/s/UP.jpg"],
        )

    def test_dry_run_shows_photos_without_uploading_them(self):
        client = FakeClient()
        result = create_listing(
            client, make_draft(image_urls=[]), photos=[str(self.jpg)], dry_run=True
        )
        self.assertEqual(client.calls, [])
        self.assertIn("<uploaded from", result["inventoryItem"]["product"]["imageUrls"][0])

    def test_a_draft_with_only_photos_still_validates(self):
        client = FakeClient()
        client.upload_image = lambda path: "https://i.ebayimg.com/00/s/UP.jpg"
        create_listing(client, make_draft(image_urls=[]), photos=[str(self.jpg)])


# ---- approval queue -----------------------------------------------------

from ebay.cli import build_parser, cmd_pending, cmd_publish  # noqa: E402


class ApprovalQueueClient:
    """Stands in for EbayClient across the create-then-approve loop."""

    def __init__(self, items, offers, publish_errors=()):
        self.config = make_config()
        self._items = items
        self._offers = offers
        self._publish_errors = dict(publish_errors)
        self.published = []

    def inventory_items(self, max_items=None):
        return iter(self._items[:max_items] if max_items else self._items)

    def offers_for_sku(self, sku):
        return self._offers.get(sku, [])

    def publish_offer(self, offer_id):
        if offer_id in self._publish_errors:
            raise EbayError(400, "u", {"errors": [{"message": self._publish_errors[offer_id]}]}, "")
        self.published.append(offer_id)
        return {"listingId": f"LST-{offer_id}"}


def run_command(func, client, argv):
    """Parse argv for real, swap in a fake client, capture stdout+stderr."""
    import contextlib
    import io

    args = build_parser().parse_args(argv)
    out, err = io.StringIO(), io.StringIO()
    with mock.patch("ebay.cli._client", return_value=client):
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = func(args)
    return code, out.getvalue(), err.getvalue()


class PendingTests(unittest.TestCase):
    def setUp(self):
        self.client = ApprovalQueueClient(
            items=[
                {"sku": "LOT-1", "product": {"title": "Rookie lot A"}},
                {"sku": "LOT-2", "product": {"title": "Rookie lot B"}},
                {"sku": "LIVE-1", "product": {"title": "Already selling"}},
            ],
            offers={
                "LOT-1": [{"offerId": "OF-1", "status": "UNPUBLISHED",
                           "pricingSummary": {"price": {"value": "14.99", "currency": "USD"}}}],
                "LOT-2": [{"offerId": "OF-2", "status": "UNPUBLISHED",
                           "pricingSummary": {"price": {"value": "9.99", "currency": "USD"}}}],
                "LIVE-1": [{"offerId": "OF-3", "status": "PUBLISHED",
                            "pricingSummary": {"price": {"value": "5.00", "currency": "USD"}}}],
            },
        )

    def test_published_offers_are_excluded(self):
        code, out, _ = run_command(cmd_pending, self.client, ["pending"])
        self.assertEqual(code, 0)
        self.assertIn("OF-1", out)
        self.assertIn("OF-2", out)
        self.assertNotIn("OF-3", out)
        self.assertNotIn("Already selling", out)

    def test_prints_a_runnable_publish_command_for_the_whole_queue(self):
        _, out, _ = run_command(cmd_pending, self.client, ["pending"])
        self.assertIn("python -m ebay publish OF-1 OF-2", out)
        self.assertIn("2 awaiting approval", out)

    def test_empty_queue_says_so_without_a_command(self):
        client = ApprovalQueueClient(items=[], offers={})
        _, out, _ = run_command(cmd_pending, client, ["pending"])
        self.assertIn("Nothing awaiting approval", out)
        self.assertNotIn("python -m ebay publish", out)


class BatchPublishTests(unittest.TestCase):
    def test_parser_accepts_many_offer_ids(self):
        args = build_parser().parse_args(["publish", "OF-1", "OF-2", "OF-3"])
        self.assertEqual(args.offer_id, ["OF-1", "OF-2", "OF-3"])

    def test_publishes_every_offer_and_reports_listing_ids(self):
        client = ApprovalQueueClient([], {})
        code, out, _ = run_command(cmd_publish, client, ["publish", "OF-1", "OF-2"])
        self.assertEqual(code, 0)
        self.assertEqual(client.published, ["OF-1", "OF-2"])
        self.assertIn("LST-OF-1", out)
        self.assertIn("2 published, 0 failed", out)

    def test_one_failure_does_not_strand_the_rest_of_the_batch(self):
        client = ApprovalQueueClient([], {}, publish_errors={"OF-2": "missing category"})
        code, out, err = run_command(
            client=client, func=cmd_publish, argv=["publish", "OF-1", "OF-2", "OF-3"]
        )
        self.assertEqual(client.published, ["OF-1", "OF-3"])  # OF-2 failed, others still went
        self.assertEqual(code, 3)
        self.assertIn("1 failed", out)
        self.assertIn("missing category", err)

    def test_failures_come_back_as_a_retry_command(self):
        client = ApprovalQueueClient([], {}, publish_errors={"OF-1": "x", "OF-3": "y"})
        _, _, err = run_command(cmd_publish, client, ["publish", "OF-1", "OF-2", "OF-3"])
        self.assertIn("python -m ebay publish OF-1 OF-3", err)

    def test_single_offer_skips_the_batch_summary(self):
        client = ApprovalQueueClient([], {})
        _, out, _ = run_command(cmd_publish, client, ["publish", "OF-9"])
        self.assertNotIn("published,", out)
        self.assertIn("LST-OF-9", out)


# ---- global flag placement ----------------------------------------------

from ebay.cli import GLOBAL_DEFAULTS, main as cli_main  # noqa: E402


def parse_with_defaults(argv):
    args = build_parser().parse_args(argv)
    for name, default in GLOBAL_DEFAULTS.items():
        if not hasattr(args, name):
            setattr(args, name, default)
    return args


class GlobalFlagPlacementTests(unittest.TestCase):
    """`ebay setup --sandbox` used to be an error; both orders must work."""

    def test_sandbox_before_or_after_the_subcommand(self):
        for argv in (["--sandbox", "setup"], ["setup", "--sandbox"]):
            self.assertTrue(parse_with_defaults(argv).sandbox, argv)

    def test_absent_flag_defaults_to_production(self):
        self.assertFalse(parse_with_defaults(["setup"]).sandbox)
        self.assertFalse(parse_with_defaults(["listings"]).sandbox)

    def test_env_file_works_in_either_position(self):
        for argv in (["--env-file", "x.env", "status"], ["status", "--env-file", "x.env"]):
            self.assertEqual(parse_with_defaults(argv).env_file, "x.env", argv)

    def test_marketplace_works_in_either_position(self):
        for argv in (["--marketplace", "EBAY_GB", "listings"],
                     ["listings", "--marketplace", "EBAY_GB"]):
            self.assertEqual(parse_with_defaults(argv).marketplace, "EBAY_GB", argv)

    def test_defaults_applied_for_every_subcommand(self):
        for command in ("setup", "status", "listings", "orders", "policies",
                        "locations", "pending", "logout"):
            args = parse_with_defaults([command])
            self.assertEqual(args.env_file, ".env", command)
            self.assertIsNone(args.marketplace, command)

    def test_main_applies_defaults_before_dispatch(self):
        """main() must fill the suppressed flags in, or commands see no attrs."""
        seen = {}

        def fake(args):
            seen.update(sandbox=args.sandbox, env_file=args.env_file,
                        marketplace=args.marketplace)
            return 0

        args = build_parser().parse_args(["status"])
        self.assertFalse(hasattr(args, "sandbox"))  # suppressed until main fills it
        args.func = fake
        with mock.patch("ebay.cli.build_parser") as build:
            build.return_value.parse_args.return_value = args
            self.assertEqual(cli_main(["status"]), 0)
        self.assertEqual(
            seen, {"sandbox": False, "env_file": ".env", "marketplace": None}
        )


# ---- seller program enrolment -------------------------------------------

from ebay.cli import cmd_programs  # noqa: E402


class ProgramClient:
    def __init__(self, enrolled=(), fail_opt_in=None):
        self.config = make_config()
        self.enrolled = list(enrolled)
        self.opted = []
        self._fail = fail_opt_in

    def opted_in_programs(self):
        return list(self.enrolled)

    def opt_in(self, program="SELLING_POLICY_MANAGEMENT"):
        if self._fail:
            raise EbayError(400, "u", {"errors": [{"errorId": 20403, "message": self._fail}]}, "")
        self.opted.append(program)
        self.enrolled.append(program)
        return None


class ProgramCommandTests(unittest.TestCase):
    def test_missing_program_is_reported_with_the_fix(self):
        code, out, _ = run_command(cmd_programs, ProgramClient(), ["programs"])
        self.assertEqual(code, 1)
        self.assertIn("SELLING_POLICY_MANAGEMENT", out)
        self.assertIn("programs --opt-in", out)

    def test_enrolled_account_passes(self):
        client = ProgramClient(enrolled=["SELLING_POLICY_MANAGEMENT"])
        code, out, _ = run_command(cmd_programs, client, ["programs"])
        self.assertEqual(code, 0)
        self.assertIn("All required programs enrolled", out)

    def test_opt_in_enrols_and_then_passes(self):
        client = ProgramClient()
        code, out, _ = run_command(cmd_programs, client, ["programs", "--opt-in"])
        self.assertEqual(client.opted, ["SELLING_POLICY_MANAGEMENT"])
        self.assertEqual(code, 0)
        self.assertIn("enrolled", out)

    def test_opt_in_is_idempotent(self):
        client = ProgramClient(enrolled=["SELLING_POLICY_MANAGEMENT"])
        _, out, _ = run_command(cmd_programs, client, ["programs", "--opt-in"])
        self.assertEqual(client.opted, [])  # no redundant call
        self.assertIn("already enrolled", out)


class OptInHintTests(unittest.TestCase):
    """A 20403 anywhere should name the fix, not just the eBay error text."""

    def test_20403_prints_the_opt_in_hint(self):
        import contextlib
        import io

        def boom(args):
            raise EbayError(
                400, "u",
                {"errors": [{"errorId": 20403,
                             "message": "User is not eligible for Business Policy."}]},
                "",
            )

        args = build_parser().parse_args(["policies"])
        args.func = boom
        err = io.StringIO()
        with mock.patch("ebay.cli.build_parser") as build:
            build.return_value.parse_args.return_value = args
            with contextlib.redirect_stderr(err):
                code = cli_main(["policies"])
        self.assertEqual(code, 3)
        self.assertIn("not opted in to eBay Business Policies", err.getvalue())
        self.assertIn("programs --opt-in", err.getvalue())

    def test_other_errors_do_not_get_the_opt_in_hint(self):
        import contextlib
        import io

        def boom(args):
            raise EbayError(401, "u", {"errors": [{"errorId": 1001, "message": "bad token"}]}, "")

        args = build_parser().parse_args(["policies"])
        args.func = boom
        err = io.StringIO()
        with mock.patch("ebay.cli.build_parser") as build:
            build.return_value.parse_args.return_value = args
            with contextlib.redirect_stderr(err):
                cli_main(["policies"])
        self.assertNotIn("Business Policies", err.getvalue())
        self.assertIn("scope", err.getvalue())


# ---- business policy creation -------------------------------------------

from ebay import policies as policies_mod  # noqa: E402
from ebay.policies import (  # noqa: E402
    CATEGORY_TYPE,
    create_missing,
    fulfillment_policy,
    payment_policy,
    return_policy,
)


class PolicyPayloadTests(unittest.TestCase):
    def test_every_policy_carries_marketplace_and_category_type(self):
        config = make_config(marketplace_id="EBAY_GB")
        for payload in (payment_policy(config), return_policy(config),
                        fulfillment_policy(config)):
            self.assertEqual(payload["marketplaceId"], "EBAY_GB")
            self.assertEqual(payload["categoryTypes"], [{"name": CATEGORY_TYPE}])

    def test_payment_policy_requires_immediate_pay(self):
        self.assertTrue(payment_policy(make_config())["immediatePay"])

    def test_return_policy_window_and_payer(self):
        payload = return_policy(make_config(), days=14, buyer_pays_return=False)
        self.assertEqual(payload["returnPeriod"], {"value": 14, "unit": "DAY"})
        self.assertEqual(payload["returnShippingCostPayer"], "SELLER")
        self.assertEqual(payload["refundMethod"], "MONEY_BACK")

    def test_flat_rate_shipping_carries_a_cost(self):
        service = fulfillment_policy(make_config(), cost="7.25")[
            "shippingOptions"][0]["shippingServices"][0]
        self.assertEqual(service["shippingCost"], {"value": "7.25", "currency": "USD"})
        self.assertFalse(service["freeShipping"])

    def test_free_shipping_omits_the_cost(self):
        service = fulfillment_policy(make_config(), free_shipping=True)[
            "shippingOptions"][0]["shippingServices"][0]
        self.assertNotIn("shippingCost", service)
        self.assertTrue(service["freeShipping"])

    def test_currency_and_region_follow_the_marketplace(self):
        payload = fulfillment_policy(make_config(marketplace_id="EBAY_GB"))
        service = payload["shippingOptions"][0]["shippingServices"][0]
        self.assertEqual(service["shippingCost"]["currency"], "GBP")
        self.assertEqual(payload["shipToLocations"]["regionIncluded"],
                         [{"regionName": "GB"}])

    def test_handling_time_and_service_are_configurable(self):
        payload = fulfillment_policy(make_config(), handling_days=3, service="USPSPriority")
        self.assertEqual(payload["handlingTime"], {"value": 3, "unit": "DAY"})
        self.assertEqual(
            payload["shippingOptions"][0]["shippingServices"][0]["shippingServiceCode"],
            "USPSPriority",
        )


class PolicyCreationClient:
    def __init__(self, existing=None, reject=()):
        self.config = make_config()
        self._existing = existing or {}
        self._reject = set(reject)
        self.created = {}

    def payment_policies(self):
        return self._existing.get("payment", [])

    def return_policies(self):
        return self._existing.get("return", [])

    def fulfillment_policies(self):
        return self._existing.get("fulfillment", [])

    def _make(self, kind, id_field, payload):
        if kind in self._reject:
            raise EbayError(400, "u", {"errors": [{"errorId": 20500,
                                                   "message": f"bad {kind}"}]}, "")
        self.created[kind] = payload
        return {id_field: f"{kind.upper()}-1"}

    def create_payment_policy(self, p):
        return self._make("payment", "paymentPolicyId", p)

    def create_return_policy(self, p):
        return self._make("return", "returnPolicyId", p)

    def create_fulfillment_policy(self, p):
        return self._make("fulfillment", "fulfillmentPolicyId", p)


class CreateMissingTests(unittest.TestCase):
    def test_creates_all_three_on_a_bare_account(self):
        client = PolicyCreationClient()
        result = create_missing(client)
        self.assertEqual(
            sorted(result["created"]), ["fulfillment", "payment", "return"]
        )
        self.assertEqual(result["failed"], {})

    def test_existing_policies_are_left_alone(self):
        client = PolicyCreationClient(
            existing={"payment": [{"paymentPolicyId": "P9", "name": "Mine"}]}
        )
        result = create_missing(client)
        self.assertEqual(result["existing"], {"payment": "P9"})
        self.assertNotIn("payment", client.created)
        self.assertIn("return", result["created"])

    def test_one_rejection_does_not_stop_the_others(self):
        client = PolicyCreationClient(reject={"fulfillment"})
        result = create_missing(client)
        self.assertIn("fulfillment", result["failed"])
        self.assertEqual(sorted(result["created"]), ["payment", "return"])

    def test_failure_message_keeps_ebays_wording(self):
        client = PolicyCreationClient(reject={"return"})
        result = create_missing(client)
        self.assertIn("bad return", result["failed"]["return"])

    def test_builder_options_reach_the_payloads(self):
        client = PolicyCreationClient()
        create_missing(client, builder_options={
            "fulfillment": {"handling_days": 2, "free_shipping": True},
            "return": {"days": 60},
        })
        self.assertEqual(client.created["fulfillment"]["handlingTime"]["value"], 2)
        self.assertEqual(client.created["return"]["returnPeriod"]["value"], 60)

    def test_events_are_reported_for_each_kind(self):
        events = []
        create_missing(PolicyCreationClient(), on_event=events.append)
        self.assertEqual(len(events), 3)
        self.assertTrue(all("created" in e for e in events))


# ---- shipping service fallback ------------------------------------------

from ebay.policies import SERVICE_FALLBACKS  # noqa: E402


class FallbackClient(PolicyCreationClient):
    """Rejects shipping service codes until it sees `accepts`."""

    def __init__(self, accepts, **kwargs):
        super().__init__(**kwargs)
        self.accepts = accepts
        self.attempts = []

    def create_fulfillment_policy(self, payload):
        service = payload["shippingOptions"][0]["shippingServices"][0][
            "shippingServiceCode"
        ]
        self.attempts.append(service)
        if service != self.accepts:
            raise EbayError(
                400, "u",
                {"errors": [{"errorId": 20403,
                             "message": f"LSAS validation failed : {service} "
                                        "(SHIPELIG_ERROR_CODE_NAME=UNKNOWN_SHIPPING_SERVICE_CODE)"}]},
                "",
            )
        self.created["fulfillment"] = payload
        return {"fulfillmentPolicyId": "F-OK"}


class ServiceFallbackTests(unittest.TestCase):
    def test_first_candidate_is_used_when_accepted(self):
        client = FallbackClient(accepts=SERVICE_FALLBACKS[0])
        result = create_missing(client)
        self.assertEqual(client.attempts, [SERVICE_FALLBACKS[0]])
        self.assertEqual(result["created"]["fulfillment"], "F-OK")

    def test_walks_the_list_until_one_is_accepted(self):
        client = FallbackClient(accepts=SERVICE_FALLBACKS[2])
        create_missing(client)
        self.assertEqual(client.attempts, list(SERVICE_FALLBACKS[:3]))

    def test_gives_up_after_every_candidate(self):
        client = FallbackClient(accepts="NOTHING_MATCHES")
        result = create_missing(client)
        self.assertEqual(client.attempts, list(SERVICE_FALLBACKS))
        self.assertIn("fulfillment", result["failed"])

    def test_an_explicit_service_is_not_second_guessed(self):
        client = FallbackClient(accepts=SERVICE_FALLBACKS[1])
        result = create_missing(
            client, builder_options={"fulfillment": {"service": "MyCarrierCode"}}
        )
        self.assertEqual(client.attempts, ["MyCarrierCode"])  # tried once, no fallback
        self.assertIn("fulfillment", result["failed"])

    def test_unrelated_errors_are_not_retried(self):
        class Broken(PolicyCreationClient):
            def __init__(self):
                super().__init__()
                self.calls = 0

            def create_fulfillment_policy(self, payload):
                self.calls += 1
                raise EbayError(400, "u", {"errors": [{"errorId": 99,
                                                       "message": "something else"}]}, "")

        client = Broken()
        result = create_missing(client)
        self.assertEqual(client.calls, 1)  # no fallback walk for an unrelated error
        self.assertIn("something else", result["failed"]["fulfillment"])

    def test_progress_is_reported_for_each_rejected_code(self):
        events = []
        create_missing(FallbackClient(accepts=SERVICE_FALLBACKS[2]), on_event=events.append)
        rejections = [e for e in events if "rejected, trying next" in e]
        self.assertEqual(len(rejections), 2)


# ---- inventory location -------------------------------------------------

from ebay.policies import inventory_location  # noqa: E402


class InventoryLocationTests(unittest.TestCase):
    def test_minimal_location_needs_only_postcode_and_country(self):
        payload = inventory_location(postal_code="93401")
        self.assertEqual(
            payload["location"]["address"], {"postalCode": "93401", "country": "US"}
        )
        self.assertEqual(payload["locationTypes"], ["WAREHOUSE"])
        self.assertEqual(payload["merchantLocationStatus"], "ENABLED")

    def test_optional_address_parts_are_included_when_given(self):
        address = inventory_location(
            postal_code="93401", address_line1="1 Main St", city="Ashland", state="OR"
        )["location"]["address"]
        self.assertEqual(address["addressLine1"], "1 Main St")
        self.assertEqual(address["city"], "Ashland")
        self.assertEqual(address["stateOrProvince"], "OR")

    def test_blank_optional_parts_are_omitted_rather_than_sent_empty(self):
        address = inventory_location(postal_code="93401", city="", state="")[
            "location"]["address"]
        self.assertNotIn("city", address)
        self.assertNotIn("stateOrProvince", address)

    def test_missing_postal_code_is_rejected(self):
        for bad in ("", "   "):
            with self.assertRaises(ValueError):
                inventory_location(postal_code=bad)

    def test_country_is_configurable(self):
        payload = inventory_location(postal_code="SW1A 1AA", country="GB")
        self.assertEqual(payload["location"]["address"]["country"], "GB")


# ---- flags override --from-file -----------------------------------------

from ebay.cli import cmd_create  # noqa: E402


class FromFileOverrideTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.path = Path(self._tmp.name) / "draft.json"
        self.addCleanup(self._tmp.cleanup)
        self.path.write_text(json.dumps({
            "sku": "LOT-1",
            "title": "From the file",
            "price": "10.00",
            "category_id": "",
            "quantity": 1,
            "image_urls": ["https://a.example.com/1.jpg"],
        }))
        self.captured = {}

        def capture(client, draft, **kwargs):
            self.captured["draft"] = draft
            return {"sku": draft.sku, "offerId": "OF-1", "offerReused": False,
                    "published": False}

        patcher = mock.patch("ebay.cli.create_listing", capture)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, extra):
        return run_command(
            cmd_create, FakeClient(),
            ["create", "LOT-1", "--from-file", str(self.path), "--draft"] + extra,
        )

    def test_file_values_are_used_when_no_flags_given(self):
        self._run([])
        self.assertEqual(self.captured["draft"].title, "From the file")
        self.assertEqual(self.captured["draft"].price, "10.00")

    def test_category_flag_fills_the_blank_the_file_cannot_know(self):
        self._run(["--category", "261328"])
        self.assertEqual(self.captured["draft"].category_id, "261328")

    def test_price_and_title_flags_win_over_the_file(self):
        self._run(["--price", "24.99", "--title", "Better title"])
        self.assertEqual(self.captured["draft"].price, "24.99")
        self.assertEqual(self.captured["draft"].title, "Better title")

    def test_quantity_only_overrides_when_actually_passed(self):
        self._run([])
        self.assertEqual(self.captured["draft"].quantity, 1)
        self._run(["--quantity", "4"])
        self.assertEqual(self.captured["draft"].quantity, 4)

    def test_extra_images_append_rather_than_replace(self):
        self._run(["--image", "https://b.example.com/2.jpg"])
        self.assertEqual(self.captured["draft"].image_urls, [
            "https://a.example.com/1.jpg", "https://b.example.com/2.jpg",
        ])


# ---- seeding tokens from the environment --------------------------------

class SeededTokenTests(unittest.TestCase):
    """EBAY_REFRESH_TOKEN lets a fresh container connect with no browser step."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.path = Path(self._tmp.name) / "tokens.json"
        self.addCleanup(self._tmp.cleanup)

    def test_refresh_token_from_the_environment_is_used(self):
        with mock.patch.dict(os.environ, {"EBAY_REFRESH_TOKEN": "SEEDED"}, clear=True):
            tokens = TokenStore(make_config(), self.path).load()
        self.assertEqual(tokens.refresh_token, "SEEDED")
        self.assertTrue(tokens.access_expired)   # forces a refresh
        self.assertFalse(tokens.refresh_expired)

    def test_first_call_refreshes_into_a_real_access_token(self):
        store = TokenStore(make_config(), self.path)
        with mock.patch.dict(os.environ, {"EBAY_REFRESH_TOKEN": "SEEDED"}, clear=True):
            with mock.patch.object(
                auth, "request",
                lambda *a, **k: {"access_token": "LIVE", "expires_in": 7200},
            ):
                self.assertEqual(store.access_token(), "LIVE")

    def test_a_token_file_wins_over_the_environment(self):
        store = TokenStore(make_config(), self.path)
        now = time.time()
        store.save(Tokens("FROM-FILE", "RT-FILE", now + 7200, now + 100000))
        with mock.patch.dict(os.environ, {"EBAY_REFRESH_TOKEN": "SEEDED"}, clear=True):
            fresh = TokenStore(make_config(), self.path).load()
        self.assertEqual(fresh.refresh_token, "RT-FILE")

    def test_no_file_and_no_variable_names_both_options(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(AuthError) as ctx:
                TokenStore(make_config(), self.path).access_token()
        message = str(ctx.exception)
        self.assertIn("EBAY_REFRESH_TOKEN", message)
        self.assertIn("login", message)

    def test_blank_variable_is_ignored(self):
        with mock.patch.dict(os.environ, {"EBAY_REFRESH_TOKEN": "   "}, clear=True):
            self.assertIsNone(TokenStore(make_config(), self.path).load())
