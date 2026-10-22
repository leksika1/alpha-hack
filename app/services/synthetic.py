"""Deterministic synthetic dataset in the exact format of the organizers' CSVs (make synthetic).

Reproduces the real data's quirks so the pipeline is exercised the same way: a pseudo-id collision,
revenue stubs (1000), self-transfers, unknown counterparties, FTS flag/country mismatches, blank
ECO labels, garbage RC_NAME values and quoted city names. Five rich CLIENT demo companies from
different industries are linked to each other through payments and groups.
"""

import csv
import json
import random
from dataclasses import dataclass, field
from pathlib import Path

SEED = 2026
ID_BASE = 700_000
UNKNOWN_BASE = 800_000
PSEUDO_ID = "U_799999"
N_BACKGROUND = 2400
STUB_REVENUE = 1000.0

COMPANY_HEADER = [
    "SYNTHETIC_U", "ORG_TYPE", "OKFS_NAME", "COMPANY_AGE_YEARS_GRADATION", "ECO_MARK_LABEL",
    "MAINOKVED_CODE", "MAINOKVED_NAME", "NEGATIVEMARK", "AMOUNT_WORKERS_FNS_GRADATION",
    "AMOUNT_WORKERS_SPARK_GRADATION", "CL_TYPE", "CL_PRODUCT_RKO_MARK", "CL_PRODUCT_GAR_MARK",
    "CL_PRODUCT_LOA_MARK", "CL_PRODUCT_LEA_MARK", "CL_PRODUCT_DEP_MARK", "CL_PRODUCT_ZPP_MARK",
    "FTS_FLAG", "FTS_COUNTRY_NAME", "FACTORING_MARK", "REVENUE_COMPANY", "GROUP_ID", "CITY", "REGION",
]  # fmt: skip
PAYMENT_HEADER = ["BENEFICIARY", "PAYER", "PAY_AMT", "PAY_CNT"]
LINK_HEADER = ["SYNTHETIC_F", "CITY_NAME", "RC_NAME", "SYNTHETIC_U"]

OKVED: dict[str, str] = {
    "01.11": "Выращивание зерновых (кроме риса), зернобобовых культур и семян масличных культур",
    "10.11": "Переработка и консервирование мяса",
    "10.61": "Производство продуктов мукомольной и крупяной промышленности",
    "11.07": "Производство безалкогольных напитков",
    "16.10": "Распиловка и строгание древесины",
    "20.15": "Производство удобрений и азотных соединений",
    "23.51": "Производство цемента",
    "24.10": "Производство чугуна, стали и ферросплавов",
    "25.11": "Производство строительных металлических конструкций",
    "28.22": "Производство подъемно-транспортного оборудования",
    "41.20": "Строительство жилых и нежилых зданий",
    "43.21": "Производство электромонтажных работ",
    "45.31.1": "Торговля оптовая автомобильными деталями, узлами и"
    " принадлежностями, кроме деятельности агентов",
    "46.21": "Торговля оптовая зерном, необработанным табаком, семенами и"
    " кормами для сельскохозяйственных животных",
    "46.34": "Торговля оптовая напитками",
    "46.38": "Торговля оптовая прочими пищевыми продуктами, включая рыбу, ракообразных и моллюсков",
    "46.51": "Торговля оптовая компьютерами, периферийными устройствами к"
    " компьютерам и программным обеспечением",
    "46.71": "Торговля оптовая твердым, жидким и газообразным топливом и подобными продуктами",
    "46.72": "Торговля оптовая металлами и металлическими рудами",
    "46.73": "Торговля оптовая лесоматериалами, строительными материалами"
    " и санитарно-техническим оборудованием",
    "46.75": "Торговля оптовая химическими продуктами",
    "46.90": "Торговля оптовая неспециализированная",
    "47.11": "Торговля розничная преимущественно пищевыми продуктами в неспециализированных магазинах",
    "47.19": "Торговля розничная прочая в неспециализированных магазинах",
    "49.41": "Деятельность автомобильного грузового транспорта",
    "52.10.9": "Хранение и складирование прочих грузов",
    "52.29": "Деятельность вспомогательная прочая, связанная с перевозками",
    "56.10": "Деятельность ресторанов и услуги по доставке продуктов питания",
    "62.01": "Разработка компьютерного программного обеспечения",
    "68.10": "Покупка и продажа собственного недвижимого имущества",
    "68.20": "Аренда и управление собственным или арендованным недвижимым имуществом",
    "70.22": "Консультирование по вопросам коммерческой деятельности и управления",
    "71.12": "Деятельность в области инженерных изысканий, инженерно-технического проектирования",
    "77.39": "Аренда и лизинг прочих машин и оборудования",
}
OKVED_WEIGHTS = {
    "46.90": 6,
    "68.20": 6,
    "41.20": 5,
    "56.10": 4,
    "62.01": 3,
    "46.72": 2,
    "47.19": 3,
    "49.41": 3,
}

REGIONS = [
    ("г. Москва", "Москва", "Москва", "МОСКОВСКИЙ"),
    ("г. Санкт-Петербург", "Санкт-Петербург", "Санкт-Петербург", "Санкт-Петербург"),
    ("г. Екатеринбург", "Свердловская область", "Екатеринбург", "УРАЛЬСКИЙ"),
    ("г. Новосибирск", "Новосибирская область", "Новосибирск", "СИБИРСКИЙ"),
    ("г. Краснодар", "Краснодарский край", "Краснодар", "ЮЖНЫЙ"),
    ("г. Владивосток", "Приморский край", "Владивосток", "ДАЛЬНЕВОСТОЧНЫЙ"),
    ("г. Казань", "Республика Татарстан", "Казань", "ПОВОЛЖСКИЙ"),
    ("г. Челябинск", "Челябинская область", "Челябинск", "УРАЛЬСКИЙ"),
]
COUNTRIES = [
    "КИТАЙ",
    "ТУРЦИЯ",
    "ИНДИЯ",
    "ОАЭ",
    "КАЗАХСТАН",
    "УЗБЕКИСТАН",
    "БЕЛАРУСЬ",
    "ЕГИПЕТ",
    "ИРАН",
    "МОНГОЛИЯ",
]
OKFS = ["ЧАСТНАЯ СОБСТВЕННОСТЬ"] * 30 + [
    "СОВМЕСТНАЯ ЧАСТНАЯ И ИНОСТРАННАЯ СОБСТВЕННОСТЬ",
    "СОБСТВЕННОСТЬ ИНОСТРАННЫХ ЮРИДИЧЕСКИХ ЛИЦ",
]
AGES = ["0-3", "4-7", "8-15", "16-25"]
ECO = ["Оптимизация", "Оптимизация", "Бизнес в новой реальности", "Рождение", "Стремительный рост"]
STAFF = ["Менее 15", "16-100", "101-250", "Более 251"]


@dataclass
class Company:
    id: str
    okved: str
    region_idx: int
    group_id: str
    cl_type: str = "CLIENT"
    revenue: float = 0.0
    products: dict[str, bool] = field(default_factory=dict)
    countries: list[str] = field(default_factory=list)
    fts_flag: bool | None = None  # None -> derived from countries
    eco: str = "Оптимизация"
    age: str = "8-15"
    staff: str = "16-100"
    okfs: str = "ЧАСТНАЯ СОБСТВЕННОСТЬ"
    negative: bool = False


@dataclass(frozen=True)
class DemoSpec:
    id: str
    title: str
    okved: str
    region_idx: int
    revenue: float
    products: tuple[str, ...]
    countries: tuple[str, ...]
    group_members: tuple[tuple[str, float], ...]  # okved, revenue of other group members
    supplier_okveds: tuple[str, ...]
    customer_okveds: tuple[str, ...]


DEMO: tuple[DemoSpec, ...] = (
    DemoSpec("U_700001", "Металлоторговля (Урал): кредит, ВЭД с Китаем и Турцией", "46.72", 2, 4.2e9,
             ("rko", "loan", "payroll"), ("КИТАЙ", "ТУРЦИЯ", "КАЗАХСТАН"),
             (("24.10", 7.9e9), ("52.29", 3.1e8), ("25.11", 1.2e9), ("70.22", 4.0e7), ("77.39", 2.2e8)),
             ("24.10", "25.11", "49.41"), ("41.20", "43.21", "25.11", "28.22")),
    DemoSpec("U_700002", "Продуктовая розница (Москва): потребительский спрос, депозиты", "47.11", 0, 9.8e9,
             ("rko", "payroll", "deposit", "guarantee"), (),
             (("46.38", 2.4e9), ("68.20", 3.0e8)),
             ("10.11", "10.61", "11.07", "46.34", "46.38", "49.41"), ()),
    DemoSpec("U_700003", "Грузоперевозки (Владивосток): лизинг, импорт из Китая", "49.41", 5, 2.1e9,
             ("rko", "leasing", "loan", "payroll"), ("КИТАЙ", "КАЗАХСТАН", "МОНГОЛИЯ"),
             (("52.10.9", 4.5e8),),
             ("46.71", "45.31.1", "77.39"), ("46.90", "46.51", "52.29", "46.72")),
    DemoSpec("U_700004", "Зерновой агрохолдинг (Кубань): экспорт, удобрения", "01.11", 4, 3.5e9,
             ("rko", "loan", "guarantee", "factoring"), ("ТУРЦИЯ", "ЕГИПЕТ", "ИРАН"),
             (("10.61", 1.6e9), ("46.21", 2.9e9)),
             ("20.15", "46.75", "46.71", "28.22"), ("46.21", "10.61", "10.11")),
    DemoSpec("U_700005", "Девелопер (Москва): крупная группа, проектное финансирование", "41.20", 0, 6.4e9,
             ("rko", "loan", "guarantee", "payroll"), (),
             (("68.10", 5.5e9), ("41.20", 3.3e9), ("43.21", 9.0e8), ("71.12", 2.0e8), ("68.20", 6.0e8),
              ("23.51", 1.9e9)),
             ("46.72", "23.51", "46.73", "43.21", "25.11", "71.12"), ("68.10",)),
)  # fmt: skip


def _flag(value: bool) -> str:
    return "1.0" if value else "0.0"


def _revenue(rng: random.Random) -> float:
    return float(round(rng.lognormvariate(17.3, 2.2)))


class _Builder:
    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)  # noqa: S311 — reproducible test data, not cryptography
        self.companies: list[Company] = []
        self.by_okved: dict[str, list[str]] = {}
        self.next_id = ID_BASE + 10
        self.next_group = 500_000
        self.payments: dict[tuple[str, str], tuple[float, int]] = {}

    def new_id(self) -> str:
        self.next_id += 1
        return f"U_{self.next_id}"

    def new_group(self) -> str:
        self.next_group += 1
        return f"group_{self.next_group}_sb"

    def add(self, c: Company) -> Company:
        self.companies.append(c)
        self.by_okved.setdefault(c.okved, []).append(c.id)
        return c

    def background(self, n: int) -> None:
        rng = self.rng
        codes = list(OKVED)
        weights = [OKVED_WEIGHTS.get(code, 1) for code in codes]
        i = 0
        while i < n:
            size = 1 if rng.random() < 0.7 else rng.choice([2, 2, 3, 4, 6, 10, 25])
            gid = self.new_group()
            region = rng.randrange(len(REGIONS))
            for _ in range(min(size, n - i)):
                cl = rng.choices(["CLIENT", "PROSPECT", "CLOSE_CLIENT"], [50, 42, 8])[0]
                is_client = cl == "CLIENT"
                countries = rng.sample(COUNTRIES, rng.randint(1, 3)) if rng.random() < 0.1 else []
                c = Company(
                    id=self.new_id(),
                    okved=rng.choices(codes, weights)[0],
                    region_idx=region if rng.random() < 0.8 else rng.randrange(len(REGIONS)),
                    group_id=gid,
                    cl_type=cl,
                    revenue=STUB_REVENUE if rng.random() < 0.015 else _revenue(rng),
                    products={
                        "rko": is_client and rng.random() < 0.94,
                        "guarantee": rng.random() < 0.05,
                        "loan": rng.random() < (0.11 if is_client else 0.02),
                        "leasing": rng.random() < 0.2,
                        "deposit": is_client and rng.random() < 0.09,
                        "payroll": is_client and rng.random() < 0.55,
                        "factoring": rng.random() < 0.003,
                    },
                    countries=countries,
                    eco="" if rng.random() < 0.05 else rng.choice(ECO),
                    age=rng.choice(AGES),
                    staff=rng.choices(STAFF, [69, 27, 3, 1])[0],
                    okfs=rng.choice(OKFS),
                    negative=rng.random() < 0.13,
                )
                self.add(c)
                i += 1
        # FTS flag / countries mismatches like in the real data
        with_countries = [c for c in self.companies if c.countries]
        without = [c for c in self.companies if not c.countries]
        for c in rng.sample(with_countries, 3):
            c.fts_flag = False
        for c in rng.sample(without, 3):
            c.fts_flag = True

    def demo(self) -> None:
        for spec in DEMO:
            gid = self.new_group()
            self.add(
                Company(
                    id=spec.id, okved=spec.okved, region_idx=spec.region_idx, group_id=gid,
                    revenue=spec.revenue,
                    products={p: True for p in spec.products}, countries=list(spec.countries),
                    eco="Оптимизация", age="16-25", staff="Более 251", negative=True,
                )
            )  # fmt: skip
            for okved, revenue in spec.group_members:
                self.add(
                    Company(
                        id=self.new_id(), okved=okved, region_idx=spec.region_idx, group_id=gid,
                        revenue=revenue,
                        products={"rko": True, "payroll": self.rng.random() < 0.6}, age="8-15",
                    )
                )  # fmt: skip

    def pay(self, payer: str, beneficiary: str, amount: float, count: int) -> None:
        amount = max(1000.0, round(amount / 1000) * 1000)
        prev = self.payments.get((payer, beneficiary), (0.0, 0))
        self.payments[(payer, beneficiary)] = (prev[0] + amount, prev[1] + max(1, count))

    def unknown_id(self) -> str:
        return f"U_{UNKNOWN_BASE + self.rng.randrange(20_000)}"

    def payment_graph(self) -> None:
        rng = self.rng
        ids = [c.id for c in self.companies]
        for c in self.companies:
            scale = max(c.revenue, 5e6) / 40
            for _ in range(min(60, int(rng.paretovariate(1.3) * 3))):
                other = self.unknown_id() if rng.random() < 0.35 else rng.choice(ids)
                if other == c.id:
                    continue
                if rng.random() < 0.5:
                    self.pay(c.id, other, scale * rng.random() * 0.2, rng.randint(1, 40))
                else:
                    self.pay(other, c.id, scale * rng.random() * 0.2, rng.randint(1, 40))
            if rng.random() < 0.03:  # self-transfers between own accounts: few rows, large amounts
                self.pay(c.id, c.id, scale * rng.uniform(5, 30), rng.randint(1, 5))
        for spec in DEMO:
            demo = next(x for x in self.companies if x.id == spec.id)
            volume = demo.revenue * 0.6
            suppliers = [s for code in spec.supplier_okveds for s in self.by_okved.get(code, [])[:4]]
            customers = [s for code in spec.customer_okveds for s in self.by_okved.get(code, [])[:5]]
            for rank, s in enumerate(rng.sample(suppliers, min(len(suppliers), 14))):
                self.pay(spec.id, s, volume * 0.25 / (rank + 1), rng.randint(5, 120))
            for rank, b in enumerate(rng.sample(customers, min(len(customers), 16))):
                self.pay(b, spec.id, volume * 0.3 / (rank + 1), rng.randint(5, 200))
            for _ in range(25):
                self.pay(spec.id, self.unknown_id(), volume * 0.01 * rng.random(), rng.randint(1, 20))
                self.pay(self.unknown_id(), spec.id, volume * 0.02 * rng.random(), rng.randint(1, 30))
            self.pay(spec.id, spec.id, volume * 3, 4)
        # cross-links between demo companies: developer buys metal from the metals trader, etc.
        self.pay("U_700005", "U_700001", 6.2e8, 140)
        self.pay("U_700002", "U_700003", 1.1e8, 260)
        self.pay("U_700001", "U_700003", 7.5e7, 95)


def generate(seed: int = SEED) -> dict[str, list[list[str]]]:
    b = _Builder(seed)
    b.demo()
    b.background(N_BACKGROUND)
    b.payment_graph()
    rng = b.rng

    company_rows: list[list[str]] = []
    for c in b.companies:
        city, region, _, _ = REGIONS[c.region_idx]
        fts = c.fts_flag if c.fts_flag is not None else bool(c.countries)
        company_rows.append(
            [
                c.id, "КОММ_ОРГ", c.okfs, c.age, c.eco, c.okved, OKVED[c.okved], _flag(c.negative), c.staff,
                "Менее 15" if rng.random() < 0.002 else "", c.cl_type,
                _flag(c.products.get("rko", False)), _flag(c.products.get("guarantee", False)),
                _flag(c.products.get("loan", False)), _flag(c.products.get("leasing", False)),
                _flag(c.products.get("deposit", False)), _flag(c.products.get("payroll", False)),
                _flag(fts), ", ".join(c.countries), _flag(c.products.get("factoring", False)),
                f"{c.revenue:.1f}", c.group_id, city, region,
            ]
        )  # fmt: skip
    # anonymization artifact: one technical id shared by several almost unrelated rows
    for code in ("46.90", "41.20", "62.01"):
        company_rows.append(
            [PSEUDO_ID, "КОММ_ОРГ", "ЧАСТНАЯ СОБСТВЕННОСТЬ", "0-3", "", code, OKVED[code], "0.0",
             "Менее 15", "",
             "PROSPECT", "0.0", "0.0", "0.0", "0.0", "0.0", "0.0", "0.0", "", "0.0",
             f"{_revenue(rng):.1f}", b.new_group(), "г. Москва", "Москва"]
        )  # fmt: skip
        b.pay(PSEUDO_ID, "U_700002", 640000.0, 3)
    company_rows.sort(key=lambda r: int(r[0][2:]))

    payment_rows = [
        [ben, payer, f"{amount:.1f}", str(count)]
        for (payer, ben), (amount, count) in sorted(b.payments.items())
    ]
    # duplicated pair rows (the real file has a few) — must be aggregated by the ingest
    payment_rows.append(["U_700002", PSEUDO_ID, "632000.0", "6"])

    link_rows: list[list[str]] = []
    known = [c.id for c in b.companies]
    demo_ids = [d.id for d in DEMO]
    for i in range(1, 601):
        pid = f"F_{900000 + i}"
        n = rng.choices([1, 2, 3, 5], [80, 14, 5, 1])[0]
        for _ in range(n):
            cid = rng.choice(known) if rng.random() < 0.4 else b.unknown_id()
            _, _, city_name, rc = REGIONS[rng.randrange(len(REGIONS))]
            if rng.random() < 0.01:
                rc = rng.choice(["", "ИНН не определён", "Не определено"])
            if city_name == "Москва" and rng.random() < 0.05:
                city_name = 'Мск "Юг"'
            link_rows.append([pid, city_name, rc, cid])
    # shared persons between demo companies and companies outside their groups (affiliation hypotheses)
    link_rows.append(["F_999001", "Екатеринбург", "УРАЛЬСКИЙ", "U_700001"])
    link_rows.append(["F_999001", "Екатеринбург", "УРАЛЬСКИЙ", known[200]])
    link_rows.append(["F_999002", "Москва", "МОСКОВСКИЙ", "U_700005"])
    link_rows.append(["F_999002", "Москва", "МОСКОВСКИЙ", demo_ids[1]])
    # deduplicate exact duplicates (the real file has none)
    seen: set[tuple[str, ...]] = set()
    unique_links = []
    for row in link_rows:
        key = (row[0], row[3])
        if key not in seen:
            seen.add(key)
            unique_links.append(row)
    return {"companies": company_rows, "payments": payment_rows, "links": unique_links}


def demo_manifest() -> list[dict[str, object]]:
    return [
        {
            "company_id": d.id,
            "title": d.title,
            "okved": d.okved,
            "region": REGIONS[d.region_idx][1],
            "products": list(d.products),
            "fts_countries": list(d.countries),
        }
        for d in DEMO
    ]


def write(out_dir: Path, seed: int = SEED) -> dict[str, int]:
    data = generate(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "companies": ("df_Uid_data_hack.csv", COMPANY_HEADER),
        "payments": ("df_Uid_payment_hack.csv", PAYMENT_HEADER),
        "links": ("df_Fid_Uid_link_hack.csv", LINK_HEADER),
    }
    for key, (name, header) in files.items():
        with (out_dir / name).open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
            writer.writerow(header)
            writer.writerows(data[key])
    (out_dir / "demo_companies.json").write_text(
        json.dumps(demo_manifest(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {key: len(rows) for key, rows in data.items()}
