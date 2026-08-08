from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError

import httpx
import pytest

from beer_network.geoip import GeoIPResolver, GeoIPResult


class MutableClock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


@pytest.mark.asyncio
async def test_public_ipv4_uses_default_https_endpoint_and_parses_payload() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "success": True,
                "country_code": "de",
                "country": " Germany ",
                "flag": {"emoji": "\U0001f1e9\U0001f1ea"},
            },
        )

    async with GeoIPResolver(transport=httpx.MockTransport(handler)) as resolver:
        result = await resolver.resolve(" 8.8.8.8 ")

    assert result == GeoIPResult(
        success=True,
        country_code="DE",
        country="Germany",
        flag="\U0001f1e9\U0001f1ea",
    )
    assert len(requests) == 1
    assert str(requests[0].url) == "https://ipwho.is/8.8.8.8"
    assert requests[0].headers["accept"] == "application/json"


@pytest.mark.asyncio
async def test_ipv6_is_canonicalised_encoded_and_gets_generated_flag() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "success": True,
                "country_code": "us",
                "country": "United States",
                "flag": {},
            },
        )

    async with GeoIPResolver(transport=httpx.MockTransport(handler)) as resolver:
        first = await resolver.resolve("2001:4860:4860:0:0:0:0:8888")
        cached = await resolver.resolve("2001:4860:4860::8888")

    assert first == GeoIPResult(
        success=True,
        country_code="US",
        country="United States",
        flag="\U0001f1fa\U0001f1f8",
    )
    assert cached is first
    assert len(requests) == 1
    assert requests[0].url.raw_path == b"/2001%3A4860%3A4860%3A%3A8888"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address",
    [
        "",
        "not-an-ip",
        "0.0.0.0",
        "10.0.0.1",
        "127.0.0.1",
        "169.254.2.3",
        "192.168.1.2",
        "192.0.2.10",
        "224.0.0.1",
        "::",
        "::1",
        "fc00::1",
        "fe80::1",
        "ff02::1",
        "2001:db8::1",
        "2001:4860:4860::8888%eth0",
    ],
)
async def test_non_public_or_invalid_addresses_never_reach_transport(address: str) -> None:
    request_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json={"success": True})

    async with GeoIPResolver(transport=httpx.MockTransport(handler)) as resolver:
        result = await resolver.resolve(address)

    assert result == GeoIPResult()
    assert request_count == 0


@pytest.mark.asyncio
async def test_http_json_api_and_transport_failures_are_neutral() -> None:
    response_number = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal response_number
        response_number += 1
        if response_number == 1:
            return httpx.Response(429, json={"success": True, "country_code": "US"})
        if response_number == 2:
            return httpx.Response(200, content=b"not-json")
        if response_number == 3:
            return httpx.Response(503, json={"success": True, "country_code": "US"})
        if response_number == 4:
            return httpx.Response(200, json=["not", "an", "object"])
        if response_number == 5:
            return httpx.Response(200, json={"success": False, "country_code": "US"})
        raise httpx.ConnectError("offline", request=request)

    addresses = ["8.8.8.1", "8.8.8.2", "8.8.8.3", "8.8.8.4", "8.8.8.5", "8.8.8.6"]
    async with GeoIPResolver(
        transport=httpx.MockTransport(handler),
        negative_ttl=0.0,
    ) as resolver:
        results = [await resolver.resolve(address) for address in addresses]

    assert results == [GeoIPResult()] * len(addresses)


@pytest.mark.asyncio
async def test_whole_request_timeout_is_neutral() -> None:
    never_release = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        await never_release.wait()
        return httpx.Response(200, json={"success": True})

    async with GeoIPResolver(
        transport=httpx.MockTransport(handler),
        timeout=0.01,
    ) as resolver:
        result = await resolver.resolve("8.8.8.8")

    assert result == GeoIPResult()


@pytest.mark.asyncio
async def test_positive_and_negative_results_use_independent_ttls() -> None:
    clock = MutableClock()
    request_counts = {"8.8.8.8": 0, "1.1.1.1": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        address = request.url.path.removeprefix("/")
        request_counts[address] += 1
        if address == "8.8.8.8":
            return httpx.Response(
                200,
                json={"success": True, "country_code": "US", "country": "United States"},
            )
        return httpx.Response(200, json={"success": False})

    async with GeoIPResolver(
        transport=httpx.MockTransport(handler),
        clock=clock,
        positive_ttl=10.0,
        negative_ttl=2.0,
    ) as resolver:
        assert resolver.get_cached("8.8.8.8") is None
        assert resolver.get_cached("127.0.0.1") is None
        positive = await resolver.resolve("8.8.8.8")
        negative = await resolver.resolve("1.1.1.1")
        assert resolver.get_cached("8.8.8.8") is positive
        assert resolver.get_cached("1.1.1.1") is negative
        assert await resolver.resolve("8.8.8.8") is positive
        assert await resolver.resolve("1.1.1.1") is negative

        clock.advance(3.0)
        assert await resolver.resolve("8.8.8.8") is positive
        assert resolver.get_cached("1.1.1.1") is None
        await resolver.resolve("1.1.1.1")

        clock.advance(8.0)
        assert resolver.get_cached("8.8.8.8") is None
        await resolver.resolve("8.8.8.8")

    assert request_counts == {"8.8.8.8": 2, "1.1.1.1": 2}


@pytest.mark.asyncio
async def test_cache_is_lru_bounded() -> None:
    requested_addresses: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested_addresses.append(request.url.path.removeprefix("/"))
        return httpx.Response(200, json={"success": True, "country_code": "US"})

    async with GeoIPResolver(
        transport=httpx.MockTransport(handler),
        max_cache_entries=2,
    ) as resolver:
        await resolver.resolve("8.8.8.1")
        await resolver.resolve("8.8.8.2")
        await resolver.resolve("8.8.8.1")
        await resolver.resolve("8.8.8.3")
        await resolver.resolve("8.8.8.2")

    assert requested_addresses == ["8.8.8.1", "8.8.8.2", "8.8.8.3", "8.8.8.2"]


@pytest.mark.asyncio
async def test_concurrent_calls_for_one_address_share_one_request() -> None:
    request_started = asyncio.Event()
    release_request = asyncio.Event()
    request_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        request_started.set()
        await release_request.wait()
        return httpx.Response(200, json={"success": True, "country_code": "US"})

    resolver = GeoIPResolver(transport=httpx.MockTransport(handler))
    tasks = [asyncio.create_task(resolver.resolve("8.8.8.8")) for _ in range(8)]
    await asyncio.wait_for(request_started.wait(), timeout=1.0)
    await asyncio.sleep(0)

    assert request_count == 1

    release_request.set()
    results = await asyncio.gather(*tasks)
    await resolver.aclose()

    assert all(result is results[0] for result in results)


@pytest.mark.asyncio
async def test_concurrent_requests_are_bounded() -> None:
    two_requests_started = asyncio.Event()
    release_requests = asyncio.Event()
    active_requests = 0
    peak_requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active_requests, peak_requests
        active_requests += 1
        peak_requests = max(peak_requests, active_requests)
        if active_requests == 2:
            two_requests_started.set()
        try:
            await release_requests.wait()
            return httpx.Response(200, json={"success": True, "country_code": "US"})
        finally:
            active_requests -= 1

    resolver = GeoIPResolver(
        transport=httpx.MockTransport(handler),
        max_concurrency=2,
    )
    tasks = [
        asyncio.create_task(resolver.resolve(address))
        for address in ("8.8.8.1", "8.8.8.2", "8.8.8.3", "8.8.8.4")
    ]
    await asyncio.wait_for(two_requests_started.wait(), timeout=1.0)

    assert peak_requests == 2

    release_requests.set()
    results = await asyncio.gather(*tasks)
    await resolver.aclose()

    assert all(result.success for result in results)
    assert peak_requests == 2


@pytest.mark.asyncio
async def test_close_is_safe_and_does_not_close_an_injected_client() -> None:
    request_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json={"success": True, "country_code": "US"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        resolver = GeoIPResolver(client=client)
        await resolver.resolve("8.8.8.8")
        await resolver.aclose()
        await resolver.aclose()

        assert client.is_closed is False
        assert await resolver.resolve("1.1.1.1") == GeoIPResult()

    assert request_count == 1


def test_results_are_immutable() -> None:
    result = GeoIPResult(
        success=True,
        country_code="US",
        country="United States",
        flag="\U0001f1fa\U0001f1f8",
    )

    with pytest.raises(FrozenInstanceError):
        result.country = "Changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("keyword", "value"),
    [
        ("positive_ttl", -1.0),
        ("negative_ttl", float("inf")),
        ("timeout", 0.0),
        ("max_cache_entries", 0),
        ("max_concurrency", 0),
        ("endpoint_template", "http://ipwho.is/{encoded_ip}"),
        ("endpoint_template", "https://ipwho.is/no-placeholder"),
    ],
)
def test_invalid_configuration_is_rejected(keyword: str, value: object) -> None:
    with pytest.raises(ValueError):
        GeoIPResolver(**{keyword: value})  # type: ignore[arg-type]
