"""Bank of Russia market data: parsers of the two cbr.ru services and the client requests (ADR-043)."""

from datetime import date
from decimal import Decimal

import httpx
import pytest

from app.integrations.cbr import CbrClient, CbrError, parse_fx_dynamic, parse_key_rate

KEY_RATE_XML = (
    b'<?xml version="1.0" encoding="utf-8"?><soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope">'
    b'<soap:Body><KeyRateXMLResponse xmlns="http://web.cbr.ru/"><KeyRateXMLResult><KeyRate xmlns="">'
    b"<KR><DT>2026-10-23T00:00:00+03:00</DT><Rate>14.00</Rate></KR>"
    b"<KR><DT>2026-09-12T00:00:00+03:00</DT><Rate>14.50</Rate></KR>"
    b"</KeyRate></KeyRateXMLResult></KeyRateXMLResponse></soap:Body></soap:Envelope>"
)

FX_XML = (
    '<?xml version="1.0" encoding="windows-1251"?>'
    '<ValCurs ID="R01375" name="Foreign Currency Market Dynamic">'
    '<Record Date="02.10.2026" Id="R01375"><Nominal>1</Nominal><Value>11,7012</Value></Record>'
    '<Record Date="01.10.2026" Id="R01375"><Nominal>10</Nominal><Value>116,5000</Value></Record>'
    "</ValCurs>"
).encode("cp1251")


def test_key_rate_is_parsed_and_sorted_by_date() -> None:
    assert parse_key_rate(KEY_RATE_XML) == [
        (date(2026, 9, 12), Decimal("14.50")),
        (date(2026, 10, 23), Decimal("14.00")),
    ]


def test_fx_value_is_per_one_unit_of_the_currency() -> None:
    assert parse_fx_dynamic(FX_XML) == [
        (date(2026, 10, 1), Decimal("11.6500")),
        (date(2026, 10, 2), Decimal("11.7012")),
    ]


def test_invalid_xml_is_a_cbr_error() -> None:
    with pytest.raises(CbrError):
        parse_key_rate(b"<html>maintenance")


async def test_client_asks_the_soap_service_for_the_key_rate_and_xml_dynamic_for_fx() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("DailyInfo.asmx"):
            return httpx.Response(200, content=KEY_RATE_XML)
        return httpx.Response(200, content=FX_XML)

    client = CbrClient("test-agent", transport=httpx.MockTransport(handler))
    try:
        rate = await client.series("key_rate", date(2026, 9, 1), date(2026, 10, 25))
        cny = await client.series("cny_rub", date(2026, 10, 1), date(2026, 10, 2))
    finally:
        await client.aclose()
    assert rate[-1] == (date(2026, 10, 23), Decimal("14.00")) and len(cny) == 2
    soap, fx = seen
    assert soap.method == "POST" and b"<fromDate>2026-09-01</fromDate>" in soap.content
    assert fx.url.params["VAL_NM_RQ"] == "R01375" and fx.url.params["date_req1"] == "01/10/2026"
    assert soap.headers["User-Agent"] == "test-agent"


async def test_http_error_is_a_cbr_error() -> None:
    client = CbrClient("t", transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    try:
        with pytest.raises(CbrError, match="HTTP 503"):
            await client.series("usd_rub", date(2026, 10, 1), date(2026, 10, 2))
    finally:
        await client.aclose()
