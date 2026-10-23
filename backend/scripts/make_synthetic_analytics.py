"""Generate synthetic "bank analytics" PDFs for offline demo and tests (make synthetic).

Clearly labelled as synthetic in the title and on every page: they stand in for the organizers' PDFs
(which are not committed) and must never be presented as real Alfa-Bank analytics. Dev-only dependency: fpdf2.
"""

import sys
from datetime import UTC, datetime
from pathlib import Path

from fpdf import FPDF

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "synthetic" / "analytics"
FONT = ROOT / "data" / "reference" / "fonts" / "Roboto-Regular.ttf"
FOOTER = (
    "Синтетический демонстрационный материал хакатона. Не является аналитикой Альфа-Банка "
    "и не является инвестиционной рекомендацией."
)

DOCS = [
    {
        "file": "demo_key_rate_outlook.pdf",
        "date": "20 октября 2026 г.",
        "title": "ДЕМО (синтетика): Ключевая ставка",
        "subtitle": "Ожидаем сохранения ставки 14% до конца года",
        "pages": [
            [
                "Регулятор сохранил ключевую ставку на уровне 14%: в демонстрационном сценарии "
                "решение объясняется "
                "ускорением устойчивой инфляции и высокими инфляционными ожиданиями. Риторика "
                "регулятора стала "
                "жёстче, сигнал о возможном снижении ставки в ближайшие месяцы не прозвучал.",
                "Наш базовый сценарий: мы ожидаем сохранения ключевой ставки на уровне 14% до конца года. "
                "Снижение ставки возможно не раньше следующего года при условии замедления базовой инфляции "
                "ниже 4% в годовом выражении и нейтральной бюджетной политики.",
                "Последствия для заёмщиков: стоимость корпоративных кредитов и лизинга останется высокой. "
                "Компаниям с плавающей ставкой по кредитам стоит закладывать в бюджет текущий "
                "уровень процентных "
                "расходов и рассмотреть фиксацию ставки по новым траншам.",
            ],
            [
                "Депозиты и ликвидность: высокие ставки сохраняют доходность размещения свободной "
                "ликвидности "
                "на коротких сроках. Мы ожидаем, что ставки по депозитам для бизнеса останутся близкими к "
                "ключевой ставке.",
                "Риски сценария: ускорение инфляции из-за ослабления рубля или роста бюджетных "
                "расходов может "
                "потребовать ещё более жёсткой политики. Замедление кредитования, напротив, ускорит "
                "возврат к "
                "снижению ставки.",
            ],
        ],
    },
    {
        "file": "demo_steel_market.pdf",
        "date": "15 октября 2026 г.",
        "title": "ДЕМО (синтетика): Рынок стали и металлопроката",
        "subtitle": "Цены на арматуру растут, импорт из Китая дорожает",
        "pages": [
            [
                "Цены на сортовой прокат и арматуру выросли: в демонстрационном сценарии рост за "
                "месяц составил "
                "около 8% на фоне сезонного спроса строительных компаний и сокращения складских запасов у "
                "трейдеров металлопроката.",
                "Мы ожидаем, что цены на сталь останутся высокими до конца года. Для металлотрейдеров это "
                "поддерживает маржу по текущим запасам, для строительных компаний означает рост "
                "себестоимости "
                "объектов и пересмотр смет.",
            ],
            [
                "Импорт и логистика: удорожание юаня и задержки на сухопутных переходах с Китаем увеличивают "
                "стоимость импортного проката и сроки поставок. Компаниям с закупками в Китае стоит "
                "учитывать "
                "валютный риск и увеличенный оборотный капитал.",
                "Риски сценария: снижение экспортных цен китайских производителей стали может "
                "оказать давление "
                "на внутренние цены в следующем квартале.",
            ],
        ],
    },
]


class _Pdf(FPDF):
    def footer(self) -> None:
        self.set_y(-18)
        self.set_font("Roboto", size=7)
        self.multi_cell(0, 3.5, FOOTER)


def build(doc: dict[str, object], out_dir: Path) -> Path:
    pdf = _Pdf(format="A4")
    pdf.set_creation_date(datetime(2026, 10, 20, tzinfo=UTC))  # deterministic output
    pdf.add_font("Roboto", fname=str(FONT))
    pdf.set_margins(18, 18, 18)
    pages = doc["pages"]
    assert isinstance(pages, list)
    for i, paragraphs in enumerate(pages):
        pdf.add_page()
        pdf.set_font("Roboto", size=10)
        if i == 0:
            pdf.cell(0, 6, str(doc["date"]), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)
            pdf.set_font("Roboto", size=16)
            pdf.cell(0, 8, str(doc["title"]), new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Roboto", size=12)
            pdf.cell(0, 7, str(doc["subtitle"]), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(4)
            pdf.set_font("Roboto", size=10)
        for para in paragraphs:
            pdf.multi_cell(0, 5, para)
            pdf.ln(4)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / str(doc["file"])
    pdf.output(str(path))
    return path


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    for doc in DOCS:
        print("written", build(doc, out))


if __name__ == "__main__":
    main()
