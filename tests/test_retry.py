"""Conduit calls are retried by one policy, and a write is never sent twice.

The transport is faked one level below requests: urllib3's
`HTTPConnectionPool._make_request` is replaced by a script of outcomes, so
the real HTTPAdapter, the real `urllib3.Retry` machinery and the real
`phabricator` client all run, and no socket is opened. `phabfive.retry._sleep`
is replaced too, so the waits are recorded rather than slept.

The hook works on urllib3 1.26 and 2.x alike. 1.26 expects `_make_request`
to return an `http.client` response and rebuilds it with
`HTTPResponse.from_httplib`, which reads the headers from `.msg`; 2.x uses
the returned `HTTPResponse` as it is. A response carrying its headers in
both places satisfies either.
"""

import io
import json
from unittest import mock
from urllib.parse import parse_qs

import pytest
from urllib3 import HTTPResponse
from urllib3._collections import HTTPHeaderDict
from urllib3.exceptions import NewConnectionError, ReadTimeoutError

from phabfive.conduit import Conduit
from phabfive.exceptions import PhabfiveConfigException, PhabfiveConnectionException
from phabfive.pagination import search_all_pages
from phabfive.retry import (
    Pacer,
    RetryPolicy,
    idempotent_writes,
    is_idempotent_edit,
    is_read,
    retries_writes,
)

HOST = "https://phorge.example.com/api/"


def ok(result):
    return {"status": 200, "body": {"result": result, "error_code": None}}


def status(code, retry_after=None):
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else {}
    return {"status": code, "body": {}, "headers": headers}


READ_TIMEOUT = "read-timeout"
REFUSED = "refused"


class Transport:
    """Answers each request with the next scripted outcome, and records it."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def __call__(self, pool, conn, method, url, body=None, headers=None, **_kw):
        body = body.decode() if isinstance(body, bytes) else body
        params = json.loads(parse_qs(body)["params"][0])
        self.requests.append((url, params))

        outcome = self.outcomes.pop(0)
        if outcome == READ_TIMEOUT:
            raise ReadTimeoutError(pool, url, "Read timed out.")
        if outcome == REFUSED:
            raise NewConnectionError(conn, "Connection refused")
        headers = HTTPHeaderDict(outcome.get("headers", {}))
        return HTTPResponse(
            body=io.BytesIO(json.dumps(outcome["body"]).encode()),
            status=outcome["status"],
            headers=headers,
            msg=headers,
            preload_content=False,
            decode_content=False,
            request_method=method,
        )

    @property
    def urls(self):
        return [url.rsplit("/", 1)[-1] for url, _params in self.requests]


@pytest.fixture
def sleeps(monkeypatch):
    waits = []
    monkeypatch.setattr("phabfive.retry._sleep", waits.append)
    return waits


@pytest.fixture
def wire(monkeypatch):
    """Install a scripted transport: `wire(outcome, ...)` returns it."""

    def install(*outcomes):
        transport = Transport(outcomes)
        monkeypatch.setattr(
            "urllib3.connectionpool.HTTPConnectionPool._make_request",
            lambda pool, conn, method, url, **kw: transport(
                pool, conn, method, url, **kw
            ),
        )
        return transport

    return install


def conduit(policy=None, load_interfaces=False):
    """A Conduit around a real client, its interfaces loaded or skipped."""
    from phabricator import Phabricator

    c = Conduit(lambda: Phabricator(host=HOST, token="t" * 32), policy)
    if not load_interfaces:
        with mock.patch("phabfive.conduit._load_interfaces"):
            c.client
    return c


class TestReads:
    def test_a_success_is_not_waited_on(self, wire, sleeps):
        transport = wire(ok({"userName": "alice"}))

        assert conduit().user.whoami()["userName"] == "alice"
        assert transport.urls == ["user.whoami"]
        assert sleeps == []

    def test_a_5xx_is_retried_after_a_backoff(self, wire, sleeps):
        transport = wire(status(503), status(502), ok({"data": []}))

        assert conduit().maniphest.search()["data"] == []
        assert transport.urls == ["maniphest.search"] * 3
        assert len(sleeps) == 2
        # Full jitter under a ceiling that doubles: 0.25s, then 0.5s.
        assert 0 <= sleeps[0] <= 0.25
        assert 0 <= sleeps[1] <= 0.5

    def test_a_read_timeout_is_retried(self, wire, sleeps):
        transport = wire(READ_TIMEOUT, ok({"data": []}))

        conduit().maniphest.search()

        assert len(transport.requests) == 2

    def test_retry_after_is_honoured(self, wire, sleeps):
        wire(status(429, retry_after=3), ok({}))

        conduit().phid.query(phids=["PHID-X"])

        assert sleeps == [3]

    def test_no_wait_is_longer_than_backoff_max(self, wire, sleeps):
        wire(status(503, retry_after=600), ok({}))

        conduit(RetryPolicy(backoff_max=7)).maniphest.search()

        assert sleeps == [7]

    def test_a_4xx_is_an_answer_and_not_retried(self, wire, sleeps):
        transport = wire(status(404))

        with pytest.raises(PhabfiveConnectionException, match="404"):
            conduit().maniphest.search()
        assert len(transport.requests) == 1

    def test_giving_up_reports_the_last_status(self, wire, sleeps):
        transport = wire(*[status(503)] * 3)

        with pytest.raises(PhabfiveConnectionException, match="503"):
            conduit(RetryPolicy(retries=2)).maniphest.search()
        assert len(transport.requests) == 3

    def test_zero_retries_tries_once(self, wire, sleeps):
        transport = wire(REFUSED)

        with pytest.raises(PhabfiveConnectionException):
            conduit(RetryPolicy(retries=0)).user.whoami()
        assert len(transport.requests) == 1
        assert sleeps == []

    def test_loading_interfaces_is_retried(self, wire, sleeps):
        method_list = {"user.whoami": {"params": {}}}
        transport = wire(status(503), ok(method_list), ok({"userName": "alice"}))

        assert conduit(load_interfaces=True).user.whoami()["userName"] == "alice"
        assert transport.urls == ["conduit.query", "conduit.query", "user.whoami"]


class TestWrites:
    """A write the server may already have applied is not sent again."""

    COMMENT = [{"type": "comment", "value": "hello"}]

    def test_a_read_timeout_is_not_retried(self, wire, sleeps):
        transport = wire(READ_TIMEOUT, ok({}))

        with pytest.raises(PhabfiveConnectionException) as caught:
            conduit().maniphest.edit(objectIdentifier="T1", transactions=self.COMMENT)
        assert len(transport.requests) == 1
        # Not "could not connect": it was sent, and may have been applied.
        assert str(caught.value).startswith(
            "maniphest.edit was sent but no answer came back, so it may have been"
        )

    def test_a_5xx_is_not_retried(self, wire, sleeps):
        transport = wire(status(502), ok({}))

        with pytest.raises(PhabfiveConnectionException) as caught:
            conduit().maniphest.edit(objectIdentifier="T1", transactions=self.COMMENT)
        assert len(transport.requests) == 1
        # A gateway error usually means the backend carried on working.
        assert str(caught.value).startswith(
            "maniphest.edit was sent and the server answered HTTP 502, so it may"
        )

    def test_a_4xx_is_reported_as_it_is(self, wire, sleeps):
        wire(status(403))

        with pytest.raises(PhabfiveConnectionException) as caught:
            conduit().maniphest.edit(objectIdentifier="T1", transactions=self.COMMENT)
        assert "may have been applied" not in str(caught.value)

    def test_a_connection_never_made_is_retried(self, wire, sleeps):
        """Nothing reached the server, so nothing can be applied twice."""
        transport = wire(REFUSED, ok({"object": {"id": 1}}))

        conduit().maniphest.edit(objectIdentifier="T1", transactions=self.COMMENT)

        assert len(transport.requests) == 2

    def test_an_idempotent_write_is_retried_like_a_read(self, wire, sleeps):
        transport = wire(READ_TIMEOUT, status(503), ok({"object": {"id": 1}}))
        policy = [{"type": "view", "value": "users"}]

        with idempotent_writes():
            conduit().maniphest.edit(objectIdentifier="T1", transactions=policy)

        assert len(transport.requests) == 3

    def test_idempotent_writes_can_be_declined_per_call(self, wire, sleeps):
        transport = wire(READ_TIMEOUT)

        with idempotent_writes(False):
            with pytest.raises(PhabfiveConnectionException):
                conduit().paste.edit(transactions=self.COMMENT)
        assert len(transport.requests) == 1


class TestConnections:
    """Mounting the policy keeps a read's pooled connection, not a write's."""

    @staticmethod
    def _adapter(endpoint):
        resource = object.__getattribute__(endpoint, "_resource")
        return resource.session.adapters["https://"]

    def test_the_pages_of_a_search_share_an_adapter(self, wire, sleeps):
        wire(ok({"data": []}), ok({"data": []}))
        search = conduit().maniphest.search

        search()
        first = self._adapter(search)
        search()

        assert self._adapter(search) is first

    def test_a_write_gets_a_fresh_adapter_and_the_old_is_closed(self, wire, sleeps):
        wire(ok({}), ok({}))
        edit = conduit().maniphest.edit

        edit(objectIdentifier="T1", transactions=TestWrites.COMMENT)
        first = self._adapter(edit)
        with mock.patch.object(first, "close") as close:
            edit(objectIdentifier="T1", transactions=TestWrites.COMMENT)

        assert self._adapter(edit) is not first
        close.assert_called_once_with()


class TestPaging:
    def test_a_failed_page_is_asked_for_again_not_the_search(self, wire, sleeps):
        """Page 2 fails once; page 1 is not fetched a second time."""

        def page(ids, after):
            return ok(
                {
                    "data": [{"id": i} for i in ids],
                    "cursor": {"after": after},
                }
            )

        transport = wire(page([1, 2], "2"), status(503), page([3], None))

        records = search_all_pages(conduit().maniphest.search, queryKey="all")

        assert [r["id"] for r in records] == [1, 2, 3]
        afters = [params.get("after") for _url, params in transport.requests]
        assert afters == [None, "2", "2"]


class TestClassification:
    @pytest.mark.parametrize(
        "method",
        [
            "maniphest.search",
            "project.column.search",
            "passphrase.query",
            "phid.query",
            "phid.lookup",
            "user.whoami",
            "maniphest.info",
            "maniphest.querystatuses",
            "maniphest.gettasktransactions",
            "diffusion.branchquery",
            "conduit.query",
        ],
    )
    def test_reads(self, method):
        assert is_read(method)

    @pytest.mark.parametrize(
        "method",
        ["maniphest.edit", "paste.edit", "diffusion.uri.edit", "maniphest.createtask"],
    )
    def test_writes(self, method):
        assert not is_read(method)

    def test_setting_fields_is_idempotent(self):
        assert is_idempotent_edit(
            [{"type": "view", "value": "users"}, {"type": "status", "value": "open"}]
        )

    def test_attaching_a_commit_is_idempotent(self):
        """Adding an edge that is already there changes nothing."""
        assert is_idempotent_edit([{"type": "commits.add", "value": ["PHID-CMIT-1"]}])

    def test_a_comment_is_not(self):
        assert not is_idempotent_edit(
            [{"type": "status", "value": "open"}, {"type": "comment", "value": "x"}]
        )

    def test_an_unknown_type_is_not(self):
        assert not is_idempotent_edit([{"type": "something.new", "value": 1}])

    def test_marking_is_scoped_to_the_block(self):
        assert not retries_writes()
        with idempotent_writes():
            assert retries_writes()
        assert not retries_writes()


class TestApplyTaskEdit:
    def _maniphest(self):
        from phabfive.maniphest import Maniphest

        m = Maniphest.__new__(Maniphest)
        m.phab = mock.MagicMock()
        seen = []
        m.phab.maniphest.edit.side_effect = lambda **kw: seen.append(retries_writes())
        return m, seen

    def test_a_field_edit_is_marked_idempotent(self):
        m, seen = self._maniphest()
        m.apply_task_edit("1", [{"type": "priority", "value": 80}])
        assert seen == [True]

    def test_a_comment_is_not_marked(self):
        m, seen = self._maniphest()
        m.apply_task_edit("1", [{"type": "comment", "value": "hi"}])
        assert seen == [False]


class TestConfiguration:
    def test_defaults(self):
        policy = RetryPolicy.from_conf({})
        assert (policy.retries, policy.backoff_max) == (3, 5.0)

    def test_strings_from_the_environment(self):
        policy = RetryPolicy.from_conf({"PHAB_RETRY": "8", "PHAB_BACKOFF_MAX": "60"})
        assert (policy.retries, policy.backoff_max) == (8, 60.0)

    @pytest.mark.parametrize(
        "conf",
        [
            {"PHAB_RETRY": "lots"},
            {"PHAB_RETRY": "-1"},
            {"PHAB_BACKOFF_MAX": "x"},
            # A nan wait never waits, and an infinite cap is no cap.
            {"PHAB_BACKOFF_MAX": "nan"},
            {"PHAB_BACKOFF_MAX": "inf"},
        ],
    )
    def test_a_bad_value_is_a_config_error(self, conf):
        with pytest.raises(PhabfiveConfigException, match="at least 0"):
            RetryPolicy.from_conf(conf)

    @pytest.mark.parametrize("value", ["abc", "-1", "nan", "inf"])
    def test_a_bad_pace_is_a_config_error(self, value):
        with pytest.raises(PhabfiveConfigException, match="PHAB_PACE"):
            Pacer.from_conf({"PHAB_PACE": value})

    @pytest.mark.parametrize(
        "config, key",
        [({"PHAB_RETRY": "often"}, "PHAB_RETRY"), ({"PHAB_PACE": "abc"}, "PHAB_PACE")],
    )
    def test_constructing_an_app_checks_it(self, config, key):
        from phabfive import Phabfive

        with pytest.raises(PhabfiveConfigException, match=key):
            Phabfive(url=HOST, token="t" * 32, config=config)

    def test_an_app_uses_its_policy(self):
        from phabfive import Phabfive

        app = Phabfive(url=HOST, token="t" * 32, config={"PHAB_RETRY": 7})
        assert object.__getattribute__(app.phab, "_retry").retries == 7


class TestPacer:
    def test_off_by_default(self, sleeps):
        pacer = Pacer.from_conf({})
        pacer.wait()
        pacer.wait()
        assert sleeps == []

    def test_the_first_write_never_waits(self, sleeps):
        Pacer(2).wait()
        assert sleeps == []

    def test_waits_only_what_is_left(self, sleeps, monkeypatch):
        clock = iter([100.0, 100.5, 102.5])
        monkeypatch.setattr("phabfive.retry.time.monotonic", lambda: next(clock))
        pacer = Pacer(2)

        pacer.wait()  # at 100.0
        pacer.wait()  # at 100.5: 1.5s of the 2 are left

        assert sleeps == [1.5]

    def test_a_batch_edit_is_paced(self, sleeps, monkeypatch):
        from phabfive.cli.edit_flow import edit_tasks_batch

        monkeypatch.setattr("phabfive.retry.time.monotonic", lambda: 0.0)
        maniphest = mock.MagicMock()
        maniphest.conf = {"PHAB_PACE": "1.5"}
        maniphest._get_task_data.return_value = {
            "fields": {"name": "t", "description": {"raw": ""}},
            "attachments": {"columns": {"boards": {}}},
        }
        maniphest.build_task_edit.return_value = (
            [{"type": "status", "value": "resolved"}],
            [{"field": "Status", "old": "Open", "new": "Resolved"}],
        )
        tasks = [{"object_id": str(i)} for i in (1, 2, 3)]

        assert edit_tasks_batch(tasks, maniphest, status="resolved", force=True) == 0

        assert maniphest.apply_task_edit.call_count == 3
        assert sleeps == [1.5, 1.5]

    def test_apply_all_paces_only_real_writes(self, sleeps, monkeypatch):
        from phabfive.edit import Edit, EditPlan, TaskEdit

        monkeypatch.setattr("phabfive.retry.time.monotonic", lambda: 0.0)
        edit = Edit.__new__(Edit)
        edit.conf = {"PHAB_PACE": "2"}
        edit.maniphest = mock.MagicMock()
        change = [{"type": "status", "value": "resolved"}]
        plan = EditPlan(
            entries=[
                TaskEdit(task_id="1", transactions=[], changes=[]),
                TaskEdit(task_id="2", transactions=change, changes=[]),
                TaskEdit(task_id="3", transactions=[], changes=[]),
                TaskEdit(task_id="4", transactions=change, changes=[]),
                TaskEdit(task_id="5", transactions=change, changes=[]),
            ]
        )

        results = edit.apply_all(plan)

        assert [r["task_id"] for r in results] == ["1", "2", "3", "4", "5"]
        assert edit.maniphest.apply_task_edit.call_count == 3
        # Nothing before the first real write, one pause before each after it.
        assert sleeps == [2, 2]


class TestAnnouncement:
    def test_each_retry_is_logged_with_its_wait(self, wire, sleeps, caplog):
        wire(status(503), READ_TIMEOUT, REFUSED, ok({}))

        with caplog.at_level("WARNING", logger="phabfive.retry"):
            conduit().maniphest.search()

        messages = [
            r.getMessage() for r in caplog.records if r.name == "phabfive.retry"
        ]
        assert [m.split(", retry")[0] for m in messages] == [
            "maniphest.search: HTTP 503",
            "maniphest.search: timed out",
            "maniphest.search: could not connect",
        ]
        assert messages[0].startswith("maniphest.search: HTTP 503, retry 1 of 3 in ")
