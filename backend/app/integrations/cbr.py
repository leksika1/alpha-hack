"""Bank of Russia market data: the key rate (DailyInfo web service) and official exchange rates (XML_dynamic).

Both are public services of cbr.ru. The parsers are pure functions; `CbrClient` only fetches.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import httpx

BASE_URL = "https://cbr.ru"
KEY_RATE_PATH = "/DailyInfoWebServ/DailyInfo.asmx"
FX_PATH = "/scripts/XML_dynamic.asp"

SOAP_KEY_RATE = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<soap12:Envelope xmlns:soap12="http://www.w3.org/2003/05/soap-envelope"><soap12:Body>'
    '<KeyRateXML xmlns="http://web.cbr.ru/"><fromDate>{date_from}</fromDate><ToDate>{date_to}</ToDate>'
    "</KeyRateXML></soap12:Body></soap12:Envelope>"
)


@dataclass(frozen=True, slots=True)
class Indicator:
    code: str
    title: str
    unit: str
    cbr_id: str | None = None  # currency code of XML_dynamic; None for the key rate


INDICATORS: dict[str, Indicator] = {
    i.code: i
    for i in (
        Indicator("key_rate", "Ключевая ставка Банка России", "%"),
        Indicator("usd_rub", "Официальный курс доллара США", "₽", "R01235"),
        Indicator("eur_rub", "Официальный курс евро", "₽", "R01239"),
        Indicator("cny_rub", "Официальный курс юаня", "₽", "R01375"),
    )
}

SOURCE_TITLE = "Банк России"
SOURCE_URLS = {
    "key_rate": "https://cbr.ru/hd_base/KeyRate/",
    "usd_rub": "https://cbr.ru/currency_base/dynamics/",
    "eur_rub": "https://cbr.ru/currency_base/dynamics/",
    "cny_rub": "https://cbr.ru/currency_base/dynamics/",
}


class CbrError(Exception):
    pass


def _parse(raw: bytes) -> ET.Element:
    try:
        return ET.fromstring(raw)  # noqa: S314 - see the import
    except ET.ParseError as exc:
        raise CbrError(f"invalid XML: {exc}") from exc


def parse_key_rate(raw: bytes) -> list[tuple[date, Decimal]]:
    """KeyRateXML response: <KR><DT>2026-10-23T00:00:00+03:00</DT><Rate>14.00</Rate></KR>..."""
    points = []
    for kr in _parse(raw).iter("KR"):
        dt, rate = kr.findtext("DT"), kr.findtext("Rate")
        if dt and rate:
            points.append((datetime.fromisoformat(dt).date(), Decimal(rate)))
    return sorted(points)


def parse_fx_dynamic(raw: bytes) -> list[tuple[date, Decimal]]:
    """XML_dynamic response: <Record Date="01.10.2026"><Nominal>1</Nominal><Value>83,5588</Value></Record>...;
    the value is per one unit of the currency."""
    points = []
    for rec in _parse(raw).iter("Record"):
        day, value, nominal = rec.get("Date"), rec.findtext("Value"), rec.findtext("Nominal") or "1"
        if day and value:
            per_unit = Decimal(value.replace(",", ".")) / Decimal(nominal.replace(",", "."))
            points.append((datetime.strptime(day, "%d.%m.%Y").date(), per_unit.quantize(Decimal("0.0001"))))
    return sorted(points)


class CbrClient:
    def __init__(
        self, user_agent: str, timeout_s: float = 20.0, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.client = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"User-Agent": user_agent},
            timeout=timeout_s,
            transport=transport,
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    async def _get(self, request: httpx.Request) -> bytes:
        try:
            response = await self.client.send(request)
        except httpx.HTTPError as exc:
            raise CbrError(f"{type(exc).__name__}: {exc}") from exc
        if response.status_code != 200:
            raise CbrError(f"HTTP {response.status_code}")
        return response.content

    async def series(self, code: str, date_from: date, date_to: date) -> list[tuple[date, Decimal]]:
        indicator = INDICATORS[code]
        if indicator.cbr_id is None:
            body = SOAP_KEY_RATE.format(date_from=date_from.isoformat(), date_to=date_to.isoformat())
            request = self.client.build_request(
                "POST",
                KEY_RATE_PATH,
                content=body.encode(),
                headers={"Content-Type": "application/soap+xml; charset=utf-8"},
            )
            return parse_key_rate(await self._get(request))
        params = {
            "date_req1": date_from.strftime("%d/%m/%Y"),
            "date_req2": date_to.strftime("%d/%m/%Y"),
            "VAL_NM_RQ": indicator.cbr_id,
        }
        return parse_fx_dynamic(await self._get(self.client.build_request("GET", FX_PATH, params=params)))
