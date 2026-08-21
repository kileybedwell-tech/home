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
