"""Request pacing and throttling through the real FatSecret gateway and its HTTP transport."""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import date

import anyio
import httpx2
import pytest
from mcp import Client

from fatsecret_mcp.config.models import Household
from fatsecret_mcp.fatsecret.budget import daily_budget
from fatsecret_mcp.fatsecret.client import FatSecretApi
from fatsecret_mcp.fatsecret.errors import DailyQuotaExceeded, FatSecretError, TooManyActions
from fatsecret_mcp.fatsecret.food_cache import FoodCache
from fatsecret_mcp.fatsecret.request_limits import RequestLimits
from fatsecret_mcp.server import build_server
from tests.conftest import FakeClock, FakeRequestClock
from tests.fatsecret.recorded import CONSUMER, USER, fixture

pytestmark = pytest.mark.anyio
DAY = date(2026, 10, 14)

RESPONSES = {
    "food_entries.get.v2": "food_entries_get_empty.json",
    "food_entries.get_month.v2": "food_entries_get_month_empty.json",
    "foods.search": "foods_search_empty.json",
    "food.get.v5": "food_get_generic.json",
}


def answer(request: httpx2.Request) -> httpx2.Response:
    recorded = fixture(RESPONSES[request.url.params["method"]])

    return httpx2.Response(200, json=recorded["response"]["json"])


def catalog_answer(request: httpx2.Request) -> httpx2.Response:
    """Any search finds as many Foods as requested, each fetched as the recorded chicken breast."""
    params = request.url.params

    if params["method"] == "foods.search":
        query = params["search_expression"]
        matches = [
            {"food_id": f"{query}/{index}", "food_name": f"{query} {index}", "food_type": "Generic"}
            for index in range(int(params["max_results"]))
        ]

        return httpx2.Response(200, json={"foods": {"food": matches}})

    if params["method"] == "food.get.v5":
        recorded = fixture("food_get_generic.json")["response"]["json"]
        recorded["food"]["food_id"] = params["food_id"]

        return httpx2.Response(200, json=recorded)

    return answer(request)


@pytest.mark.parametrize("response_seconds", [0.0, 2.5])
async def test_all_gateway_operations_share_pacing_and_sign_after_waiting(
    request_clock: FakeRequestClock,
    request_limits: RequestLimits,
    monkeypatch: pytest.MonkeyPatch,
    response_seconds: float,
) -> None:
    sent: list[tuple[str, float, int]] = []
    in_flight = 0
    most_in_flight = 0

    monkeypatch.setattr("fatsecret_mcp.fatsecret.oauth.time.time", lambda: 1000 + request_clock.elapsed)

    async def respond(request: httpx2.Request) -> httpx2.Response:
        nonlocal in_flight, most_in_flight
        in_flight += 1
        most_in_flight = max(most_in_flight, in_flight)
        sent.append(
            (request.url.params["method"], request_clock.elapsed, int(request.url.params["oauth_timestamp"]))
        )
        request_clock.elapsed += response_seconds

        await anyio.lowlevel.checkpoint()

        in_flight -= 1

        return answer(request)

    async def call_gateway(call: Callable[[], Awaitable[object]]) -> None:
        await call()

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        gateway = FatSecretApi(CONSUMER, http, request_limits=request_limits)
        calls: list[Callable[[], Awaitable[object]]] = [
            lambda: gateway.list_food_entries(USER, DAY),
            lambda: gateway.get_month_totals(USER, DAY),
            lambda: gateway.search_catalog("skyr"),
            lambda: gateway.get_food("1641"),
        ]

        async with anyio.create_task_group() as group:
            for call in calls:
                group.start_soon(call_gateway, call)

    methods = {method for method, _, _ in sent}
    started_times = [started for _, started, _ in sent]
    timestamps = [timestamp for _, _, timestamp in sent]
    expected_times = [index * max(1.0, response_seconds) for index in range(4)]
    expected_timestamps = [int(1000 + started) for started in expected_times]

    assert methods == set(RESPONSES)
    assert most_in_flight == 1
    assert started_times == expected_times
    assert timestamps == expected_timestamps


async def test_a_burst_goes_a_second_apart_then_each_request_waits_for_its_refill(
    request_clock: FakeRequestClock, request_limits: RequestLimits
) -> None:
    sent: list[float] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        sent.append(request_clock.elapsed)

        return answer(request)

    async def search_many(gateway: FatSecretApi, searches: int) -> None:
        async with anyio.create_task_group() as group:
            for _ in range(searches):
                group.start_soon(gateway.search_catalog, "skyr")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        gateway = FatSecretApi(CONSUMER, http, request_limits=request_limits)

        await search_many(gateway, 16)

        # Half a minute without requests refills the whole burst.
        request_clock.elapsed = 48.0
        await search_many(gateway, 10)

    # Ten requests held, plus four refilled while those go, start a second apart; the 15th waits
    # for the rest of its refill, and each one after it for a whole refill of three seconds.
    burst = [float(second) for second in range(14)]
    refilled = [15.0, 18.0]
    after_idling = [48.0 + second for second in range(10)]

    assert sent == pytest.approx(burst + refilled + after_idling)


async def test_a_plate_of_seven_components_is_searched_and_drafted_without_a_burst_of_requests(
    household: Household, clock: FakeClock, request_clock: FakeRequestClock, request_limits: RequestLimits
) -> None:
    components = ["white rice", "beef", "fried egg", "pak choi", "bean sprouts", "carrot", "gochujang"]
    sent: list[tuple[str, float]] = []
    search_seconds: list[float] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        sent.append((request.url.params["method"], request_clock.elapsed))

        return catalog_answer(request)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        gateway = FatSecretApi(household.consumer, http, request_limits=request_limits)
        server = build_server(
            user=household.user("jakub"),
            fatsecret=gateway,
            clock=clock,
            signing_key=household.signing_key,
            daily_requests=daily_budget(len(household.users)),
            food_cache=FoodCache(clock=clock),
        )

        async with Client(server) as client:
            for component in components:
                started = request_clock.elapsed
                found = await client.call_tool("search_foods", {"description": component})

                assert not found.is_error
                search_seconds.append(request_clock.elapsed - started)

            items = [
                {"food_id": f"{component}/0", "serving_id": "50321", "number_of_units": 100}
                for component in components
            ]
            started = request_clock.elapsed
            drafted = await client.call_tool("draft_entries", {"items": items})
            draft_seconds = request_clock.elapsed - started

    sent_methods = [method for method, _ in sent]
    methods = Counter(sent_methods)
    sent_at = [seconds for _, seconds in sent]
    burst = [float(second) for second in range(14)]
    refilled = [float(second) for second in range(15, 58, 3)]
    rounded_search_seconds = [round(seconds) for seconds in search_seconds]

    assert not drafted.is_error, drafted.content
    # Each search is one catalog search and three Foods; the Draft reads only the Diary Day.
    assert methods == {"foods.search": 7, "food.get.v5": 21, "food_entries.get.v2": 1}
    # Ten held plus four refilled go a second apart, then one every three seconds: 29 in 57 s.
    assert sent_at == pytest.approx(burst + refilled)
    # The first searches use the burst; later ones wait for refills, twelve seconds at most.
    assert rounded_search_seconds == [3, 4, 4, 7, 12, 12, 12]
    assert round(draft_seconds) == 3


async def test_error_12_stops_queued_calls_until_the_cooldown_without_extending_it(
    request_clock: FakeRequestClock, request_limits: RequestLimits
) -> None:
    sent: list[float] = []
    failures: list[TooManyActions] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        sent.append(request_clock.elapsed)

        if len(sent) == 1:
            return httpx2.Response(200, json={"error": {"code": 12}})

        return answer(request)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        gateway = FatSecretApi(CONSUMER, http, request_limits=request_limits)

        async def search_food() -> None:
            try:
                await gateway.search_catalog("skyr")
            except TooManyActions as error:
                failures.append(error)

        async with anyio.create_task_group() as group:
            for _ in range(6):
                group.start_soon(search_food)

        assert len(failures) == 6
        assert sent == [0.0]

        request_clock.elapsed = 29.9

        with pytest.raises(TooManyActions):
            await gateway.search_catalog("skyr")

        assert sent == [0.0]

        request_clock.elapsed = 30.0
        found = await gateway.search_catalog("skyr")

    assert found == []
    assert sent == [0.0, 30.0]


async def test_logs_tell_fatsecrets_error_12_from_requests_the_cooldown_did_not_send(
    household: Household, clock: FakeClock, request_limits: RequestLimits, caplog: pytest.LogCaptureFixture
) -> None:
    def respond(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"error": {"code": 12}})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        gateway = FatSecretApi(household.consumer, http, request_limits=request_limits)
        server = build_server(
            user=household.user("jakub"),
            fatsecret=gateway,
            clock=clock,
            signing_key=household.signing_key,
            daily_requests=daily_budget(len(household.users)),
            food_cache=FoodCache(clock=clock),
        )

        async with Client(server) as client:
            await client.call_tool("search_foods", {"description": "skyr"})
            await client.call_tool("get_diary_day", {})

    client_records = [record for record in caplog.records if record.name == "fatsecret_mcp.fatsecret.client"]
    messages = [record.getMessage() for record in client_records]

    assert messages == [
        "FatSecret foods.search failed: TooManyActions (code 12)",
        "FatSecret food_entries.get.v2 not sent: TooManyActions (code 12)",
    ]


@pytest.mark.parametrize("code", [9, 20])
async def test_other_errors_are_not_retried_and_do_not_start_a_throttle_cooldown(
    request_clock: FakeRequestClock, request_limits: RequestLimits, code: int
) -> None:
    sent: list[float] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        sent.append(request_clock.elapsed)

        if len(sent) == 1:
            return httpx2.Response(200, json={"error": {"code": code}})

        return answer(request)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        gateway = FatSecretApi(CONSUMER, http, request_limits=request_limits)

        with pytest.raises(FatSecretError) as raised:
            await gateway.search_catalog("skyr")

        assert raised.value.code == code
        assert sent == [0.0]

        assert await gateway.search_catalog("skyr") == []

    assert sent == [0.0, 1.0]


async def test_an_exhausted_daily_quota_pauses_sending_for_ten_minutes(
    request_clock: FakeRequestClock, request_limits: RequestLimits
) -> None:
    sent: list[float] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        sent.append(request_clock.elapsed)

        if len(sent) == 1:
            return httpx2.Response(200, json={"error": {"code": 11}})

        return answer(request)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        gateway = FatSecretApi(CONSUMER, http, request_limits=request_limits)

        with pytest.raises(DailyQuotaExceeded):
            await gateway.search_catalog("skyr")

        request_clock.elapsed = 599.9

        with pytest.raises(DailyQuotaExceeded) as paused:
            await gateway.search_catalog("skyr")

        assert sent == [0.0]
        assert not paused.value.sent

        request_clock.elapsed = 600.0

        assert await gateway.search_catalog("skyr") == []

    assert sent == [0.0, 600.0]


async def test_cancelling_a_pacing_wait_releases_the_slot_without_sending_the_cancelled_request(
    request_clock: FakeRequestClock,
) -> None:
    waiting = anyio.Event()
    never_release = anyio.Event()
    cancelled = anyio.CancelScope()
    sent: list[float] = []

    async def pause_wait(_: float) -> None:
        waiting.set()
        await never_release.wait()

    limits = RequestLimits(
        now=request_clock,
        sleep=pause_wait,
    )

    def respond(request: httpx2.Request) -> httpx2.Response:
        sent.append(request_clock.elapsed)

        return answer(request)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        gateway = FatSecretApi(CONSUMER, http, request_limits=limits)

        async def cancel_search() -> None:
            with cancelled:
                await gateway.search_catalog("skyr")

        await gateway.search_catalog("skyr")

        with anyio.fail_after(2):
            async with anyio.create_task_group() as group:
                group.start_soon(cancel_search)
                await waiting.wait()
                cancelled.cancel()

            request_clock.elapsed = 1.0

            assert await gateway.search_catalog("skyr") == []

    assert sent == [0.0, 1.0]


async def test_cancelling_an_in_flight_request_releases_the_slot_for_the_next_call(
    request_clock: FakeRequestClock, request_limits: RequestLimits
) -> None:
    reading = anyio.Event()
    never_release = anyio.Event()
    cancelled = anyio.CancelScope()
    sent: list[float] = []

    async def respond(request: httpx2.Request) -> httpx2.Response:
        sent.append(request_clock.elapsed)

        if len(sent) == 1:
            reading.set()
            await never_release.wait()

        return answer(request)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        gateway = FatSecretApi(CONSUMER, http, request_limits=request_limits)

        async def cancel_search() -> None:
            with cancelled:
                await gateway.search_catalog("skyr")

        with anyio.fail_after(2):
            async with anyio.create_task_group() as group:
                group.start_soon(cancel_search)
                await reading.wait()
                cancelled.cancel()

            assert await gateway.search_catalog("skyr") == []

    assert sent == [0.0, 1.0]


@pytest.mark.parametrize("code", [9, 11, 20])
async def test_a_failed_history_scan_cancels_remaining_reads_instead_of_sending_the_whole_window(
    household: Household, clock: FakeClock, request_limits: RequestLimits, code: int
) -> None:
    requests = 0

    def respond(_: httpx2.Request) -> httpx2.Response:
        nonlocal requests
        requests += 1

        return httpx2.Response(200, json={"error": {"code": code}})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        gateway = FatSecretApi(household.consumer, http, request_limits=request_limits)
        server = build_server(
            user=household.user("jakub"),
            fatsecret=gateway,
            clock=clock,
            signing_key=household.signing_key,
            daily_requests=daily_budget(len(household.users)),
            food_cache=FoodCache(clock=clock),
        )

        async with Client(server) as client:
            result = await client.call_tool("search_foods", {"description": "skyr"})

    assert result.is_error
    assert requests == 1


async def test_throttling_a_search_also_stops_other_tools_during_the_same_cooldown(
    household: Household,
    clock: FakeClock,
    request_clock: FakeRequestClock,
    request_limits: RequestLimits,
) -> None:
    requests = 0

    def respond(request: httpx2.Request) -> httpx2.Response:
        nonlocal requests
        requests += 1

        if requests == 1:
            return httpx2.Response(200, json={"error": {"code": 12}})

        return answer(request)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        gateway = FatSecretApi(household.consumer, http, request_limits=request_limits)
        server = build_server(
            user=household.user("jakub"),
            fatsecret=gateway,
            clock=clock,
            signing_key=household.signing_key,
            daily_requests=daily_budget(len(household.users)),
            food_cache=FoodCache(clock=clock),
        )

        async with Client(server) as client:
            failed_search = await client.call_tool("search_foods", {"description": "skyr"})
            failed_day = await client.call_tool("get_diary_day", {})
            failed_period = await client.call_tool(
                "get_period_summary", {"from_day": DAY.isoformat(), "to_day": DAY.isoformat()}
            )

            for result in (failed_search, failed_day, failed_period):
                assert result.is_error
                assert str(TooManyActions()) in str(result.content)

            assert requests == 1

            request_clock.elapsed = 30.0
            recovered = await client.call_tool("get_diary_day", {})

    assert not recovered.is_error
    assert requests == 2
