"""Privacy-conscious asynchronous Geo-IP lookups for public IP addresses."""

from __future__ import annotations

import asyncio
import math
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address, ip_address
from types import TracebackType
from typing import Any, Final
from urllib.parse import quote

import httpx

_DEFAULT_ENDPOINT: Final = "https://ipwho.is/{encoded_ip}"


@dataclass(frozen=True, slots=True)
class GeoIPResult:
    """An immutable, display-ready result from a Geo-IP lookup."""

    success: bool = False
    country_code: str | None = None
    country: str | None = None
    flag: str | None = None


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    result: GeoIPResult
    expires_at: float


_NEUTRAL_RESULT: Final = GeoIPResult()


class GeoIPResolver:
    """Resolve public IP addresses with bounded, cached HTTPS requests.

    A client supplied by the caller remains owned by the caller. When no client
    is supplied, the resolver creates and closes its own client.
    """

    def __init__(
        self,
        *,
        positive_ttl: float = 86_400.0,
        negative_ttl: float = 300.0,
        max_cache_entries: int = 1_024,
        max_concurrency: int = 4,
        timeout: float = 3.0,
        endpoint_template: str = _DEFAULT_ENDPOINT,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        _validate_non_negative_duration("positive_ttl", positive_ttl)
        _validate_non_negative_duration("negative_ttl", negative_ttl)
        _validate_positive_duration("timeout", timeout)
        if isinstance(max_cache_entries, bool) or max_cache_entries < 1:
            raise ValueError("max_cache_entries must be at least one")
        if isinstance(max_concurrency, bool) or max_concurrency < 1:
            raise ValueError("max_concurrency must be at least one")
        if client is not None and transport is not None:
            raise ValueError("client and transport cannot both be supplied")
        _validate_endpoint_template(endpoint_template)

        self._positive_ttl = float(positive_ttl)
        self._negative_ttl = float(negative_ttl)
        self._max_cache_entries = max_cache_entries
        self._timeout = float(timeout)
        self._endpoint_template = endpoint_template
        self._clock = clock
        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._inflight: dict[str, asyncio.Task[GeoIPResult]] = {}
        self._state_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._request_slots = asyncio.Semaphore(max_concurrency)
        self._closed = False
        self._client_closed = False

        if client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                transport=transport,
                follow_redirects=False,
                trust_env=False,
            )
            self._owns_client = True
        else:
            self._client = client
            self._owns_client = False

    async def resolve(self, address: str) -> GeoIPResult:
        """Return location data for a public IP, or a neutral result on failure."""

        canonical_address = _canonical_public_address(address)
        if canonical_address is None:
            return _NEUTRAL_RESULT

        async with self._state_lock:
            if self._closed:
                return _NEUTRAL_RESULT

            cached = self._cache.get(canonical_address)
            if cached is not None:
                if cached.expires_at > self._clock():
                    self._cache.move_to_end(canonical_address)
                    return cached.result
                del self._cache[canonical_address]

            task = self._inflight.get(canonical_address)
            if task is None:
                task = asyncio.create_task(self._resolve_and_cache(canonical_address))
                self._inflight[canonical_address] = task

        # A cancelled UI consumer must not cancel a request shared by other consumers.
        return await asyncio.shield(task)

    def get_cached(self, address: str) -> GeoIPResult | None:
        """Return a fresh cached result without performing network I/O."""

        canonical_address = _canonical_public_address(address)
        if canonical_address is None or self._closed:
            return None

        cached = self._cache.get(canonical_address)
        if cached is None:
            return None
        if cached.expires_at <= self._clock():
            del self._cache[canonical_address]
            return None

        self._cache.move_to_end(canonical_address)
        return cached.result

    async def aclose(self) -> None:
        """Finish active lookups and release resources owned by this resolver."""

        async with self._close_lock:
            if self._client_closed:
                return

            async with self._state_lock:
                self._closed = True
                active_tasks = tuple(self._inflight.values())

            if active_tasks:
                await asyncio.gather(*active_tasks, return_exceptions=True)

            if self._owns_client:
                await self._client.aclose()

            async with self._state_lock:
                self._cache.clear()
                self._client_closed = True

    async def __aenter__(self) -> GeoIPResolver:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def _resolve_and_cache(self, address: str) -> GeoIPResult:
        current_task = asyncio.current_task()
        try:
            result = await self._fetch(address)
            ttl = self._positive_ttl if result.success else self._negative_ttl

            if ttl > 0.0:
                async with self._state_lock:
                    if not self._closed:
                        self._cache[address] = _CacheEntry(
                            result=result,
                            expires_at=self._clock() + ttl,
                        )
                        self._cache.move_to_end(address)
                        while len(self._cache) > self._max_cache_entries:
                            self._cache.popitem(last=False)
            return result
        finally:
            async with self._state_lock:
                if self._inflight.get(address) is current_task:
                    del self._inflight[address]

    async def _fetch(self, address: str) -> GeoIPResult:
        encoded_address = quote(address, safe="")
        url = self._endpoint_template.format(encoded_ip=encoded_address)

        async with self._request_slots:
            try:
                response = await asyncio.wait_for(
                    self._client.get(
                        url,
                        headers={"Accept": "application/json"},
                        follow_redirects=False,
                        timeout=self._timeout,
                    ),
                    timeout=self._timeout,
                )
            except Exception:
                # HTTPX errors, whole-operation timeouts, and injected transport
                # failures are all ordinary lookup misses for dashboard callers.
                return _NEUTRAL_RESULT

        if response.status_code == httpx.codes.TOO_MANY_REQUESTS or not response.is_success:
            return _NEUTRAL_RESULT

        try:
            payload: Any = response.json()
        except (ValueError, UnicodeError):
            return _NEUTRAL_RESULT

        return _parse_payload(payload)


def _parse_payload(payload: Any) -> GeoIPResult:
    if not isinstance(payload, Mapping) or payload.get("success") is not True:
        return _NEUTRAL_RESULT

    country_code = _normalise_country_code(payload.get("country_code"))
    country = _optional_text(payload.get("country"))
    flag = None
    raw_flag = payload.get("flag")
    if isinstance(raw_flag, Mapping):
        flag = _optional_text(raw_flag.get("emoji"))
    if flag is None and country_code is not None:
        flag = "".join(chr(0x1F1E6 + ord(character) - ord("A")) for character in country_code)

    return GeoIPResult(
        success=True,
        country_code=country_code,
        country=country,
        flag=flag,
    )


def _canonical_public_address(address: str) -> str | None:
    raw_address = address.strip()
    if not raw_address or "%" in raw_address:
        return None

    try:
        parsed = ip_address(raw_address)
    except ValueError:
        return None

    if not _is_public_unicast(parsed):
        return None
    return parsed.compressed


def _is_public_unicast(address: IPv4Address | IPv6Address) -> bool:
    mapped_address = getattr(address, "ipv4_mapped", None)
    if mapped_address is not None and not _is_public_unicast(mapped_address):
        return False

    return bool(
        address.is_global
        and not address.is_private
        and not address.is_reserved
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_multicast
        and not address.is_unspecified
        and not getattr(address, "is_site_local", False)
    )


def _normalise_country_code(value: Any) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    normalised = text.upper()
    if len(normalised) != 2 or not normalised.isascii() or not normalised.isalpha():
        return None
    return normalised


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _validate_non_negative_duration(name: str, value: float) -> None:
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(f"{name} must be a non-negative finite number")


def _validate_positive_duration(name: str, value: float) -> None:
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"{name} must be a positive finite number")


def _validate_endpoint_template(endpoint_template: str) -> None:
    if "{encoded_ip}" not in endpoint_template:
        raise ValueError("endpoint_template must contain {encoded_ip}")
    try:
        endpoint = endpoint_template.format(encoded_ip="8.8.8.8")
        parsed_endpoint = httpx.URL(endpoint)
    except (KeyError, ValueError) as error:
        raise ValueError("endpoint_template must be a valid URL template") from error
    if parsed_endpoint.scheme != "https" or parsed_endpoint.host is None:
        raise ValueError("endpoint_template must produce an HTTPS URL")


__all__ = ["GeoIPResolver", "GeoIPResult"]
