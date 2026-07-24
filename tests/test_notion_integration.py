import io
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import notion_integration  # noqa: E402


PAGE_ID = "11111111-1111-4111-8111-111111111111"
SECOND_PAGE_ID = "22222222-2222-4222-8222-222222222222"
DATA_SOURCE_ID = "33333333-3333-4333-8333-333333333333"
CHILD_PAGE_ID = "44444444-4444-4444-8444-444444444444"
CHILD_DATABASE_ID = "55555555-5555-4555-8555-555555555555"


class FakeNotionClient:
    def __init__(self, discovered=(), failures=()):
        self.discovered = tuple(discovered)
        self.failures = set(failures)
        self.fetched = []

    def search_targets(self, **_kwargs):
        return self.discovered

    def fetch_target_context(self, target, **_kwargs):
        self.fetched.append(target.id)
        if target.id in self.failures:
            raise notion_integration.NotionApiError("権限を確認してください。", status=403)
        fetched_at = "2026-07-24T00:00:00+00:00"
        source = notion_integration.NotionSource(
            id=target.id,
            object_type=target.object_type,
            title=target.display_name,
            url=f"https://www.notion.so/{target.id.replace('-', '')}",
            allowed_target_id=target.id,
            allowed_target_title=target.display_name,
            fetched_at=fetched_at,
        )
        return notion_integration._FetchedTarget(
            text=f"[Allowed Notion source]\nTitle: {target.display_name}\nContent:\nPRIVATE_BODY_{target.id}",
            sources=(source,),
        )


def discovered(target_id, object_type="page", title="Shared target", in_trash=False):
    return notion_integration.NotionDiscoveredTarget(
        id=target_id,
        object_type=object_type,
        title=title,
        url=f"https://www.notion.so/{target_id.replace('-', '')}",
        in_trash=in_trash,
    )


def synthetic_notion_id(index):
    compact = f"{index:032x}"
    return f"{compact[:8]}-{compact[8:12]}-{compact[12:16]}-{compact[16:20]}-{compact[20:]}"


class NotionIntegrationTests(unittest.TestCase):
    def test_normalizes_uuid_and_notion_url(self):
        compact = PAGE_ID.replace("-", "")
        self.assertEqual(PAGE_ID, notion_integration.normalize_notion_id(compact))
        self.assertEqual(PAGE_ID, notion_integration.normalize_notion_id(f"https://www.notion.so/Notes-{compact}?v=1"))
        with self.assertRaises(ValueError):
            notion_integration.normalize_notion_id("not-an-id")

    def test_settings_round_trip_contains_allowlist_metadata_but_no_body_or_token(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = notion_integration.NotionSettings(
                targets=(
                    notion_integration.NotionTarget(
                        id=PAGE_ID,
                        object_type="page",
                        enabled=True,
                        display_name="Project notes",
                        purpose="回答時の仕様確認",
                    ),
                )
            )
            path = notion_integration.save_notion_settings(root, settings)
            loaded = notion_integration.load_notion_settings(root)
            raw = path.read_text(encoding="utf-8")

            self.assertEqual(settings, loaded)
            self.assertIn("Project notes", raw)
            self.assertNotIn("PRIVATE_BODY", raw)
            self.assertNotIn("secret_", raw)

    def test_context_fetch_uses_only_enabled_allowlist_and_does_not_cache_body(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            notion_integration.save_notion_settings(
                root,
                notion_integration.NotionSettings(
                    targets=(
                        notion_integration.NotionTarget(PAGE_ID, "page", True, "Allowed page", "project"),
                        notion_integration.NotionTarget(SECOND_PAGE_ID, "page", False, "Disabled page", "project"),
                    )
                ),
            )
            client = FakeNotionClient()

            result = notion_integration.retrieve_notion_context(root, "project", client=client)

            self.assertTrue(result.used)
            self.assertEqual("ok", result.status)
            self.assertEqual([PAGE_ID], client.fetched)
            self.assertIn(f"PRIVATE_BODY_{PAGE_ID}", result.context_text)
            self.assertNotIn(SECOND_PAGE_ID, result.context_text)
            settings_text = notion_integration.notion_settings_path(root).read_text(encoding="utf-8")
            self.assertNotIn("PRIVATE_BODY", settings_text)
            self.assertFalse((root / "memory").exists())
            self.assertFalse((root / "journal").exists())
            self.assertFalse((root / "cache").exists())

    def test_context_failure_returns_no_stale_content_and_records_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            notion_integration.save_notion_settings(
                root,
                notion_integration.NotionSettings(
                    targets=(notion_integration.NotionTarget(PAGE_ID, "page", True, "Lost page", ""),)
                ),
            )

            result = notion_integration.retrieve_notion_context(
                root,
                "question",
                client=FakeNotionClient(failures=(PAGE_ID,)),
            )

            self.assertFalse(result.used)
            self.assertEqual("error", result.status)
            self.assertEqual("", result.context_text)
            self.assertIn("Lost page", result.error)
            saved = notion_integration.load_notion_settings(root).targets[0]
            self.assertEqual("error", saved.last_status)
            self.assertIsNotNone(saved.last_fetched_at)

    def test_page_fetch_does_not_follow_unallowlisted_child_page_or_database(self):
        requests = []

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _maximum):
                return json.dumps(self.payload).encode("utf-8")

        def opener(request, timeout):
            requests.append((request.full_url, timeout))
            if f"/v1/pages/{PAGE_ID}" in request.full_url:
                return Response(
                    {
                        "object": "page",
                        "id": PAGE_ID,
                        "in_trash": False,
                        "url": f"https://www.notion.so/{PAGE_ID.replace('-', '')}",
                        "properties": {},
                    }
                )
            if f"/v1/blocks/{PAGE_ID}/children" in request.full_url:
                return Response(
                    {
                        "object": "list",
                        "results": [
                            {
                                "object": "block",
                                "id": CHILD_PAGE_ID,
                                "type": "child_page",
                                "has_children": True,
                                "in_trash": False,
                                "child_page": {"title": "Child notes"},
                            },
                            {
                                "object": "block",
                                "id": CHILD_DATABASE_ID,
                                "type": "child_database",
                                "has_children": True,
                                "in_trash": False,
                                "child_database": {"title": "Child table"},
                            },
                        ],
                        "has_more": False,
                    }
                )
            return Response(
                {
                    "object": "list",
                    "results": [
                        {
                            "object": "block",
                            "id": synthetic_notion_id(900),
                            "type": "paragraph",
                            "has_children": False,
                            "in_trash": False,
                            "paragraph": {"rich_text": [{"plain_text": "UNALLOWLISTED_CHILD_BODY"}]},
                        }
                    ],
                    "has_more": False,
                }
            )

        client = notion_integration.NotionClient("test-value", opener=opener)
        fetched = client.fetch_target_context(
            notion_integration.NotionTarget(PAGE_ID, "page", True, "Allowed parent", ""),
            question="parent",
            limits=notion_integration.NotionLimits(),
            deadline=time.monotonic() + 5,
            is_cancelled=None,
        )

        self.assertIn("child_page: Child notes", fetched.text)
        self.assertIn("child_database: Child table", fetched.text)
        self.assertNotIn("UNALLOWLISTED_CHILD_BODY", fetched.text)
        requested_urls = [url for url, _timeout in requests]
        self.assertFalse(any(f"/v1/blocks/{CHILD_PAGE_ID}/children" in url for url in requested_urls))
        self.assertFalse(any(f"/v1/blocks/{CHILD_DATABASE_ID}/children" in url for url in requested_urls))

    def test_status_recording_merges_into_settings_changed_during_fetch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            notion_integration.save_notion_settings(
                root,
                notion_integration.NotionSettings(
                    targets=(notion_integration.NotionTarget(PAGE_ID, "page", True, "Original name", "old purpose"),)
                ),
            )

            class SettingsChangingClient(FakeNotionClient):
                def fetch_target_context(self, target, **kwargs):
                    current = notion_integration.load_notion_settings(root)
                    notion_integration.save_notion_settings(
                        root,
                        notion_integration.NotionSettings(
                            targets=(
                                notion_integration.NotionTarget(
                                    PAGE_ID,
                                    "page",
                                    False,
                                    "Latest name",
                                    "latest purpose",
                                ),
                            ),
                            limits=current.limits,
                        ),
                    )
                    return super().fetch_target_context(target, **kwargs)

            result = notion_integration.retrieve_notion_context(root, "question", client=SettingsChangingClient())

            self.assertEqual("ok", result.status)
            saved = notion_integration.load_notion_settings(root).targets[0]
            self.assertFalse(saved.enabled)
            self.assertEqual("Latest name", saved.display_name)
            self.assertEqual("latest purpose", saved.purpose)
            self.assertEqual("ok", saved.last_status)
            self.assertIsNotNone(saved.last_fetched_at)

    def test_discovery_view_never_exceeds_two_hundred_saveable_targets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            client = FakeNotionClient(
                discovered=tuple(discovered(synthetic_notion_id(index), title=f"Target {index}") for index in range(1, 206))
            )

            view = notion_integration.get_notion_settings_view(root, refresh=True, client=client)

            self.assertLessEqual(len(view["targets"]), 200)
            updates = [
                {
                    "id": target["id"],
                    "object_type": target["object_type"],
                    "enabled": False,
                    "display_name": target["display_name"],
                    "purpose": target["purpose"],
                }
                for target in view["targets"]
            ]
            notion_integration.update_notion_allowlist(root, updates, client=client)
            self.assertLessEqual(len(notion_integration.load_notion_settings(root).targets), 200)

    def test_multiple_target_partial_failure_uses_only_successful_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            notion_integration.save_notion_settings(
                root,
                notion_integration.NotionSettings(
                    targets=(
                        notion_integration.NotionTarget(PAGE_ID, "page", True, "Available page", ""),
                        notion_integration.NotionTarget(SECOND_PAGE_ID, "page", True, "Unavailable page", ""),
                    )
                ),
            )

            result = notion_integration.retrieve_notion_context(
                root,
                "question",
                client=FakeNotionClient(failures=(SECOND_PAGE_ID,)),
            )

            self.assertTrue(result.used)
            self.assertEqual("partial", result.status)
            self.assertIn(f"PRIVATE_BODY_{PAGE_ID}", result.context_text)
            self.assertNotIn(f"PRIVATE_BODY_{SECOND_PAGE_ID}", result.context_text)
            self.assertEqual([PAGE_ID], [source.id for source in result.sources])
            saved = {target.id: target for target in notion_integration.load_notion_settings(root).targets}
            self.assertEqual("ok", saved[PAGE_ID].last_status)
            self.assertEqual("error", saved[SECOND_PAGE_ID].last_status)

    def test_missing_token_allows_disabling_or_clearing_but_rejects_new_enable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            notion_integration.save_notion_settings(
                root,
                notion_integration.NotionSettings(
                    targets=(notion_integration.NotionTarget(PAGE_ID, "page", True, "Existing page", "project"),)
                ),
            )

            with mock.patch.object(notion_integration, "read_notion_token", return_value=None):
                disabled_view = notion_integration.update_notion_allowlist(
                    root,
                    [
                        {
                            "id": PAGE_ID,
                            "object_type": "page",
                            "enabled": False,
                            "display_name": "Existing page",
                            "purpose": "project",
                        }
                    ],
                )
                self.assertFalse(disabled_view["targets"][0]["enabled"])
                self.assertFalse(notion_integration.load_notion_settings(root).targets[0].enabled)

                cleared_view = notion_integration.update_notion_allowlist(root, [])
                self.assertEqual([], cleared_view["targets"])
                self.assertEqual((), notion_integration.load_notion_settings(root).targets)

                with self.assertRaises(notion_integration.NotionCredentialError):
                    notion_integration.update_notion_allowlist(
                        root,
                        [
                            {
                                "id": SECOND_PAGE_ID,
                                "object_type": "page",
                                "enabled": True,
                                "display_name": "New page",
                                "purpose": "",
                            }
                        ],
                    )

    def test_malformed_local_settings_fail_as_empty_context_instead_of_blocking_local_answer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = notion_integration.notion_settings_path(root)
            path.parent.mkdir(parents=True)
            path.write_text("{not-json", encoding="utf-8")

            result = notion_integration.retrieve_notion_context(root, "question", client=FakeNotionClient())

            self.assertFalse(result.used)
            self.assertEqual("error", result.status)
            self.assertEqual("", result.context_text)
            self.assertIn("設定", result.error)

    def test_settings_view_merges_discovered_targets_and_marks_lost_target_unavailable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            notion_integration.save_notion_settings(
                root,
                notion_integration.NotionSettings(
                    targets=(notion_integration.NotionTarget(PAGE_ID, "page", True, "Saved page", ""),)
                ),
            )
            client = FakeNotionClient(
                discovered=(discovered(SECOND_PAGE_ID, title="New page"), discovered(DATA_SOURCE_ID, "data_source", "Tasks"))
            )

            view = notion_integration.get_notion_settings_view(root, refresh=True, client=client)

            self.assertTrue(view["connection"]["connected"])
            by_id = {item["id"]: item for item in view["targets"]}
            self.assertFalse(by_id[PAGE_ID]["available"])
            self.assertFalse(by_id[SECOND_PAGE_ID]["enabled"])
            self.assertEqual("data_source", by_id[DATA_SOURCE_ID]["object_type"])

    def test_allowlist_update_rejects_enabling_unavailable_target(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            notion_integration.save_notion_settings(
                root,
                notion_integration.NotionSettings(
                    targets=(notion_integration.NotionTarget(PAGE_ID, "page", False, "Old page", ""),)
                ),
            )
            client = FakeNotionClient(discovered=(discovered(SECOND_PAGE_ID),))

            with self.assertRaisesRegex(ValueError, "有効化"):
                notion_integration.update_notion_allowlist(
                    root,
                    [{"id": PAGE_ID, "object_type": "page", "enabled": True, "display_name": "Old", "purpose": ""}],
                    client=client,
                )

    def test_allowlist_update_accepts_only_currently_discovered_type(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            client = FakeNotionClient(discovered=(discovered(DATA_SOURCE_ID, "data_source", "Tasks"),))

            result = notion_integration.update_notion_allowlist(
                root,
                [
                    {
                        "id": DATA_SOURCE_ID,
                        "object_type": "data_source",
                        "enabled": True,
                        "display_name": "Work tasks",
                        "purpose": "進捗確認",
                    }
                ],
                client=client,
            )

            self.assertTrue(result["targets"][0]["enabled"])
            saved = notion_integration.load_notion_settings(root).targets[0]
            self.assertEqual("data_source", saved.object_type)
            self.assertEqual("進捗確認", saved.purpose)

    def test_http_client_rejects_every_non_read_endpoint_before_opening_network(self):
        calls = []

        def opener(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("network must not be called")

        client = notion_integration.NotionClient("private-token", opener=opener)
        forbidden = (
            ("POST", "/v1/pages"),
            ("PATCH", f"/v1/pages/{PAGE_ID}"),
            ("DELETE", f"/v1/blocks/{PAGE_ID}"),
            ("POST", f"/v1/blocks/{PAGE_ID}/children"),
            ("PATCH", f"/v1/data_sources/{DATA_SOURCE_ID}"),
        )
        for method, path in forbidden:
            with self.subTest(method=method, path=path):
                with self.assertRaisesRegex(notion_integration.NotionIntegrationError, "読み取り専用"):
                    client._request(method, path, body={})
        self.assertEqual([], calls)

    def test_http_search_uses_current_api_version_and_read_query_endpoint(self):
        requests = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _maximum):
                return json.dumps(
                    {
                        "object": "list",
                        "results": [
                            {
                                "object": "page",
                                "id": PAGE_ID,
                                "url": f"https://www.notion.so/{PAGE_ID.replace('-', '')}",
                                "in_trash": False,
                                "properties": {
                                    "title": {
                                        "type": "title",
                                        "title": [{"plain_text": "Shared spec"}],
                                    }
                                },
                            }
                        ],
                        "has_more": False,
                        "next_cursor": None,
                    }
                ).encode("utf-8")

        def opener(request, timeout):
            requests.append((request, timeout))
            return Response()

        client = notion_integration.NotionClient("private-value", opener=opener)
        targets = client.search_targets()

        self.assertEqual("Shared spec", targets[0].title)
        request, timeout = requests[0]
        self.assertEqual("POST", request.get_method())
        self.assertTrue(request.full_url.endswith("/v1/search"))
        self.assertEqual(notion_integration.NOTION_API_VERSION, request.headers["Notion-version"])
        self.assertLessEqual(timeout, 8.0)

    def test_nan_timeout_limits_are_rejected(self):
        for field in ("timeout_seconds", "total_timeout_seconds"):
            for value in (float("nan"), "NaN"):
                with self.subTest(field=field, value_type=type(value).__name__):
                    with self.assertRaises(notion_integration.NotionIntegrationError):
                        notion_integration._limits_from_mapping({field: value})

    def test_rate_limit_retries_once_and_obeys_deadline_and_request_budget(self):
        def make_opener(calls):
            def opener(request, timeout):
                calls.append((request, timeout))
                raise notion_integration.urllib.error.HTTPError(
                    request.full_url,
                    429,
                    "rate limited",
                    {"Retry-After": "1"},
                    io.BytesIO(b'{"code":"rate_limited","message":"private server text"}'),
                )

            return opener

        calls = []
        client = notion_integration.NotionClient("private-value", opener=make_opener(calls))
        with mock.patch.object(notion_integration.time, "sleep") as sleep:
            with self.assertRaises(notion_integration.NotionApiError) as raised:
                client._request(
                    "POST",
                    "/v1/search",
                    body={},
                    deadline=time.monotonic() + 5,
                    max_requests=4,
                )
        self.assertEqual(429, raised.exception.status)
        self.assertEqual(2, len(calls))
        sleep.assert_called_once_with(1.0)

        deadline_calls = []
        deadline_client = notion_integration.NotionClient(
            "private-value",
            opener=make_opener(deadline_calls),
        )
        with mock.patch.object(notion_integration.time, "sleep") as deadline_sleep:
            with self.assertRaises(notion_integration.NotionApiError):
                deadline_client._request(
                    "POST",
                    "/v1/search",
                    body={},
                    deadline=time.monotonic() + 0.25,
                    max_requests=4,
                )
        self.assertEqual(1, len(deadline_calls))
        deadline_sleep.assert_not_called()

        budget_calls = []
        budget_client = notion_integration.NotionClient(
            "private-value",
            opener=make_opener(budget_calls),
        )
        with mock.patch.object(notion_integration.time, "sleep"):
            with self.assertRaises(notion_integration.NotionIntegrationError):
                budget_client._request(
                    "POST",
                    "/v1/search",
                    body={},
                    deadline=time.monotonic() + 5,
                    max_requests=1,
                )
        self.assertEqual(1, len(budget_calls))

    def test_default_opener_rejects_redirect_before_authorization_can_reach_another_host(self):
        real_build_opener = notion_integration.urllib.request.build_opener
        with mock.patch.object(
            notion_integration.urllib.request,
            "build_opener",
            wraps=real_build_opener,
        ) as build_opener:
            notion_integration.NotionClient("private-value")

        build_opener.assert_called_once()
        redirect_handler = build_opener.call_args.args[0]
        self.assertIsInstance(redirect_handler, notion_integration._RejectRedirectHandler)
        original = notion_integration.urllib.request.Request(
            "https://api.notion.com/v1/search",
            headers={"Authorization": "Bearer private-value"},
        )

        with self.assertRaises(notion_integration.urllib.error.HTTPError) as raised:
            redirect_handler.redirect_request(
                original,
                None,
                302,
                "Found",
                {},
                "https://attacker.invalid/collect",
            )

        self.assertEqual(302, raised.exception.code)
        self.assertEqual("https://api.notion.com/v1/search", original.full_url)
        self.assertEqual("Bearer private-value", original.get_header("Authorization"))

    def test_global_character_budget_leaves_unfetched_target_status_unchanged(self):
        class FullBudgetClient(FakeNotionClient):
            def fetch_target_context(self, target, **kwargs):
                fetched = super().fetch_target_context(target, **kwargs)
                return notion_integration._FetchedTarget(
                    text="X" * kwargs["limits"].max_chars_per_target,
                    sources=fetched.sources,
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            notion_integration.save_notion_settings(
                root,
                notion_integration.NotionSettings(
                    targets=(
                        notion_integration.NotionTarget(PAGE_ID, "page", True, "First", ""),
                        notion_integration.NotionTarget(SECOND_PAGE_ID, "page", True, "Second", ""),
                    ),
                    limits=notion_integration.NotionLimits(
                        max_targets_per_request=2,
                        max_chars_per_target=1_000,
                        max_total_chars=1_000,
                    ),
                ),
            )
            client = FullBudgetClient()

            result = notion_integration.retrieve_notion_context(root, "question", client=client)

            self.assertEqual("ok", result.status)
            self.assertEqual([PAGE_ID], client.fetched)
            saved = {target.id: target for target in notion_integration.load_notion_settings(root).targets}
            self.assertEqual("ok", saved[PAGE_ID].last_status)
            self.assertEqual("never", saved[SECOND_PAGE_ID].last_status)
            self.assertIsNone(saved[SECOND_PAGE_ID].last_fetched_at)

    def test_formula_false_and_zero_values_are_retained(self):
        self.assertEqual(
            "False",
            notion_integration._property_value_text(
                {"type": "formula", "formula": {"type": "boolean", "boolean": False}}
            ),
        )
        self.assertEqual(
            "0",
            notion_integration._property_value_text(
                {"type": "formula", "formula": {"type": "number", "number": 0}}
            ),
        )

    def test_data_source_uses_parent_database_url_and_page_only_query(self):
        requests = []

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _maximum):
                return json.dumps(self.payload).encode("utf-8")

        def opener(request, timeout):
            requests.append((request, timeout))
            if request.get_method() == "GET":
                return Response(
                    {
                        "object": "data_source",
                        "id": DATA_SOURCE_ID,
                        "parent": {"type": "database_id", "database_id": CHILD_DATABASE_ID},
                        "title": [{"plain_text": "Tasks"}],
                        "in_trash": False,
                    }
                )
            return Response({"object": "list", "results": [], "has_more": False})

        client = notion_integration.NotionClient("private-value", opener=opener)
        fetched = client.fetch_target_context(
            notion_integration.NotionTarget(DATA_SOURCE_ID, "data_source", True, "Tasks", ""),
            question="open tasks",
            limits=notion_integration.NotionLimits(),
            deadline=time.monotonic() + 5,
            is_cancelled=None,
        )

        self.assertEqual(
            f"https://www.notion.so/{CHILD_DATABASE_ID.replace('-', '')}",
            fetched.sources[0].url,
        )
        query_request = next(request for request, _timeout in requests if request.get_method() == "POST")
        self.assertTrue(query_request.full_url.endswith(f"/v1/data_sources/{DATA_SOURCE_ID}/query"))
        self.assertEqual("page", json.loads(query_request.data.decode("utf-8"))["result_type"])

    def test_malformed_data_source_results_are_rejected(self):
        class Response:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _maximum):
                return json.dumps(self.payload).encode("utf-8")

        def opener(request, timeout):
            if request.get_method() == "GET":
                return Response(
                    {
                        "object": "data_source",
                        "id": DATA_SOURCE_ID,
                        "parent": {"type": "database_id", "database_id": CHILD_DATABASE_ID},
                        "title": [{"plain_text": "Tasks"}],
                        "in_trash": False,
                    }
                )
            return Response({"object": "list", "results": {"unexpected": "mapping"}})

        client = notion_integration.NotionClient("private-value", opener=opener)
        with self.assertRaises(notion_integration.NotionApiError):
            client.fetch_target_context(
                notion_integration.NotionTarget(DATA_SOURCE_ID, "data_source", True, "Tasks", ""),
                question="open tasks",
                limits=notion_integration.NotionLimits(),
                deadline=time.monotonic() + 5,
                is_cancelled=None,
            )

    def test_public_serialization_of_context_has_no_fetched_body(self):
        source = notion_integration.NotionSource(
            id=PAGE_ID,
            object_type="page",
            title="Allowed",
            url="https://www.notion.so/allowed",
            allowed_target_id=PAGE_ID,
            allowed_target_title="Allowed",
            fetched_at="2026-07-24T00:00:00+00:00",
        )
        result = notion_integration.NotionContextResult(
            requested=True,
            used=True,
            status="ok",
            fetched_at=source.fetched_at,
            sources=(source,),
            context_text="PRIVATE_BODY",
        )

        public = {
            "requested": result.requested,
            "used": result.used,
            "status": result.status,
            "sources": [notion_integration.asdict(item) for item in result.sources],
        }

        self.assertNotIn("PRIVATE_BODY", json.dumps(public))


if __name__ == "__main__":
    unittest.main()
