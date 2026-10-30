"""FR-12: the broker only talks TLS 1.2+, with a certificate from our own CA."""

from __future__ import annotations

import asyncio
import ssl

import pytest

from app.config import get_settings

from ..mqtt_helpers import tls


async def _handshake(context: ssl.SSLContext) -> str:
    settings = get_settings()
    _, writer = await asyncio.wait_for(
        asyncio.open_connection(settings.mqtt_host, settings.mqtt_port, ssl=context), 5
    )
    try:
        return writer.get_extra_info("ssl_object").version()
    finally:
        writer.close()
        await writer.wait_closed()


async def test_tls_13_with_a_certificate_signed_by_our_ca():
    """Criterion 1. create_default_context checks the chain AND the host name."""
    assert await _handshake(tls()) == "TLSv1.3"


async def test_tls_12_still_works_for_older_devices():
    context = tls()
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    assert await _handshake(context) == "TLSv1.2"


@pytest.mark.filterwarnings("ignore:ssl.TLSVersion:DeprecationWarning")
async def test_tls_11_and_older_are_refused():
    context = tls()
    context.minimum_version = ssl.TLSVersion.TLSv1
    context.maximum_version = ssl.TLSVersion.TLSv1_1
    with pytest.raises((ssl.SSLError, ConnectionError)):
        await _handshake(context)


async def test_a_client_that_does_not_know_our_ca_refuses_the_broker():
    """A client checking against the usual internet CAs does not accept our
    certificate, so every client must be given our CA (and checks it)."""
    with pytest.raises(ssl.SSLCertVerificationError):
        await _handshake(ssl.create_default_context())
