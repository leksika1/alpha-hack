"""ML-oriented EDA of the organisers' CSVs (data/raw).

Answers the questions the ranker and the agent depend on: which sensitivity factors active clients actually have,
how rich the profiles are (cold start), how much of the counterparty graph is usable.
Cleaning rules mirror docs/DATA.md (revenue stub 1000, U_428697 collision, self-transfers).

Usage (from ml/):  uv run --group analysis python scripts/eda.py --data-dir ../data/raw --out reports/eda_ml.md
Only aggregates are written; raw data never leaves data/raw.
"""

import argparse
from pathlib import Path

import pandas as pd

COLLISION_ID = "U_428697"
REVENUE_STUB = 1000
LARGE_RUB = 15e9
MID_RUB = 0.8e9

# Coarse OKVED-class -> factor families (for coverage stats only; the backend dictionary is the source of truth).
CONSUMER_CLASSES = {"47", "56", "55", "96", "93"}
COMMODITY_CLASSES = {
    "01": "agri",
    "02": "timber",
    "05": "coal",
    "06": "oil_gas",
    "07": "metals",
    "10": "food_processing",
    "16": "timber",
    "19": "fuel",
    "20": "chemicals",
    "23": "construction_materials",
    "24": "metals",
    "25": "metals",
}
WHOLESALE_COMMODITY = {
    "46.2": "agri",
    "46.3": "food",
    "46.71": "fuel",
    "46.72": "metals",
    "46.73": "construction_materials",
    "46.75": "chemicals",
}
RATE_SENSITIVE_CLASSES = {"41", "42", "43", "68", "77", "64"}


def okved_class(code: str) -> str:
    return str(code).split(".")[0].zfill(2) if pd.notna(code) else ""


def commodity_family(code: str) -> str | None:
    if pd.isna(code):
        return None
    code = str(code)
    for prefix, fam in WHOLESALE_COMMODITY.items():
        if code.startswith(prefix):
            return fam
    return COMMODITY_CLASSES.get(okved_class(code))


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def load(data_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    comp = pd.read_csv(data_dir / "df_Uid_data_hack.csv", sep=";", dtype=str)
    pay = pd.read_csv(
        data_dir / "df_Uid_payment_hack.csv",
        sep=";",
        dtype={"BENEFICIARY": str, "PAYER": str, "PAY_AMT": float, "PAY_CNT": float},
    )
    links = pd.read_csv(data_dir / "df_Fid_Uid_link_hack.csv", sep=";", dtype=str)
    return comp, pay, links


def prepare_companies(comp: pd.DataFrame) -> pd.DataFrame:
    comp = comp[comp["SYNTHETIC_U"] != COLLISION_ID].copy()
    comp["revenue"] = pd.to_numeric(comp["REVENUE_COMPANY"], errors="coerce")
    comp.loc[comp["revenue"] == REVENUE_STUB, "revenue"] = pd.NA
    for col in [c for c in comp.columns if c.endswith("_MARK") or c == "FTS_FLAG"]:
        comp[col] = pd.to_numeric(comp[col], errors="coerce").fillna(0).astype(int).astype(bool)
    comp["has_countries"] = comp["FTS_COUNTRY_NAME"].fillna("").str.strip() != ""
    comp["fx"] = comp["FTS_FLAG"] | comp["has_countries"]
    comp["okved_class"] = comp["MAINOKVED_CODE"].map(okved_class)
    comp["commodity"] = comp["MAINOKVED_CODE"].map(commodity_family)
    comp["consumer"] = comp["okved_class"].isin(CONSUMER_CLASSES)
    comp["rate_products"] = comp["CL_PRODUCT_LOA_MARK"] | comp["CL_PRODUCT_LEA_MARK"] | comp["FACTORING_MARK"]
    comp["rate_industry"] = comp["okved_class"].isin(RATE_SENSITIVE_CLASSES)
    grp = comp.groupby("GROUP_ID").agg(group_size=("SYNTHETIC_U", "size"), group_revenue=("revenue", "sum"))
    comp = comp.join(grp, on="GROUP_ID")
    base = comp["group_revenue"].where(comp["group_revenue"] > 0, comp["revenue"])
    comp["segment"] = pd.cut(
        base.fillna(-1),
        [-2, 0, MID_RUB, LARGE_RUB, float("inf")],
        labels=["unknown", "small", "mid", "large"],
    )
    return comp


def counterparty_stats(pay: pd.DataFrame, known: set[str]) -> pd.DataFrame:
    pay = pay[(pay["PAYER"] != pay["BENEFICIARY"])]
    pay = pay[(pay["PAYER"] != COLLISION_ID) & (pay["BENEFICIARY"] != COLLISION_ID)]
    out = pay.rename(columns={"PAYER": "company", "BENEFICIARY": "cp"})[["company", "cp", "PAY_AMT"]]
    inc = pay.rename(columns={"BENEFICIARY": "company", "PAYER": "cp"})[["company", "cp", "PAY_AMT"]]
    both = pd.concat([out, inc])
    both["cp_known"] = both["cp"].isin(known)
    total = both.groupby("company")["PAY_AMT"].sum()
    known_amt = both[both["cp_known"]].groupby("company")["PAY_AMT"].sum()
    n_known = both[both["cp_known"]].groupby("company")["cp"].nunique()
    n_all = both.groupby("company")["cp"].nunique()
    share = both.groupby(["company", "cp"])["PAY_AMT"].sum() / total
    hhi = (share.pow(2) * 10_000).groupby(level=0).sum()
    top1 = share.groupby(level=0).max()
    return pd.DataFrame(
        {
            "n_cp": n_all,
            "n_cp_known": n_known,
            "known_share": known_amt / total,
            "hhi": hhi,
            "top1_share": top1,
        }
    ).fillna({"n_cp_known": 0, "known_share": 0.0})


def same_industry_share(pay: pd.DataFrame, comp: pd.DataFrame, clients: set[str]) -> float:
    okv = comp.set_index("SYNTHETIC_U")["okved_class"]
    pay = pay[(pay["PAYER"] != pay["BENEFICIARY"])]
    pay = pay[pay["PAYER"].isin(okv.index) & pay["BENEFICIARY"].isin(okv.index)]
    pay = pay[pay["PAYER"].isin(clients) | pay["BENEFICIARY"].isin(clients)]
    same = okv.reindex(pay["PAYER"]).to_numpy() == okv.reindex(pay["BENEFICIARY"]).to_numpy()
    return float(pay.loc[same, "PAY_AMT"].sum() / pay["PAY_AMT"].sum())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", type=Path, default=Path("../data/raw"))
    ap.add_argument("--out", type=Path, default=Path("reports/eda_ml.md"))
    args = ap.parse_args()

    raw_comp, pay, links = load(args.data_dir)
    comp = prepare_companies(raw_comp)
    known = set(comp["SYNTHETIC_U"])
    cl = comp[comp["CL_TYPE"] == "CLIENT"].copy()
    cps = counterparty_stats(pay, known)
    cl = cl.join(cps, on="SYNTHETIC_U")
    cl[["n_cp", "n_cp_known"]] = cl[["n_cp", "n_cp_known"]].fillna(0)
    shared = links.groupby("SYNTHETIC_F")["SYNTHETIC_U"].nunique()
    multi_f = set(shared[shared > 1].index)
    shared_companies = set(links[links["SYNTHETIC_F"].isin(multi_f)]["SYNTHETIC_U"])
    cl["shared_person"] = cl["SYNTHETIC_U"].isin(shared_companies)
    large = cl[cl["segment"] == "large"]

    cl["signals"] = (
        cl["rate_products"].astype(int)
        + cl["fx"].astype(int)
        + cl["CL_PRODUCT_DEP_MARK"].astype(int)
        + (cl["n_cp_known"] >= 5).astype(int)
        + (cl["group_size"] > 1).astype(int)
        + cl["consumer"].astype(int)
        + cl["commodity"].notna().astype(int)
    )

    lines: list[str] = []
    w = lines.append
    w("# EDA для ML: факторы, профили, граф (реальные данные организаторов)\n")
    w("Сгенерировано `ml/scripts/eda.py`. Только агрегаты; правила очистки — как в `docs/DATA.md`.\n")
    w("## 1. Кого обслуживает сервис\n")
    w(f"- Юрлиц (валидных): **{len(comp):,}**, из них действующих клиентов (CLIENT): **{len(cl):,}**.")
    seg = cl["segment"].value_counts()
    w(
        "- Сегменты CLIENT по выручке группы: "
        + ", ".join(f"{k} {v:,} ({pct(v / len(cl))})" for k, v in seg.items())
        + "."
    )
    w(f"- Выручка-заглушка/пропуск у CLIENT: {pct(cl['revenue'].isna().mean())}.")
    w(
        f"- CLIENT в группах из >1 юрлица: {pct((cl['group_size'] > 1).mean())}; "
        f"медиана размера группы у них — {int(cl.loc[cl['group_size'] > 1, 'group_size'].median())}.\n"
    )

    w("## 2. Покрытие факторов чувствительности (что ранжировщик реально может «зацепить»)\n")
    w("| Фактор | Признак | Все CLIENT | Крупные CLIENT |")
    w("|---|---|---|---|")
    factors = {
        "Ставка (долг)": "rate_products",
        "Ставка (депозит)": "CL_PRODUCT_DEP_MARK",
        "Ставка (отрасль: стройка, недвижимость, аренда)": "rate_industry",
        "Курс / санкции (ВЭД)": "fx",
        "Сырьё (по ОКВЭД)": "commodity",
        "Потребительский спрос": "consumer",
        "Рынок труда (зарплатный проект)": "CL_PRODUCT_ZPP_MARK",
        "Гарантии": "CL_PRODUCT_GAR_MARK",
        "Общие физлица с другими компаниями": "shared_person",
    }
    for name, col in factors.items():
        a = cl[col].notna().mean() if col == "commodity" else cl[col].mean()
        b = large[col].notna().mean() if col == "commodity" else large[col].mean()
        w(f"| {name} | `{col}` | {pct(a)} | {pct(b)} |")
    w("")
    fam = cl["commodity"].value_counts().head(10)
    w("Сырьевые семейства у CLIENT: " + ", ".join(f"{k} {v:,}" for k, v in fam.items()) + ".\n")

    w("## 3. Богатство профиля (холодный старт)\n")
    w("Сигналы: долг, ВЭД, депозит, ≥5 известных контрагентов, группа >1, потребспрос, сырьё.\n")
    dist = cl["signals"].value_counts().sort_index()
    w("| Сигналов | CLIENT | Доля |")
    w("|---|---|---|")
    for k, v in dist.items():
        w(f"| {k} | {v:,} | {pct(v / len(cl))} |")
    cold = (cl["signals"] <= 1).mean()
    w(f"\n**Холодные профили (≤1 сигнала): {pct(cold)}** — им нужна отраслевая персонализация и анкета.\n")

    w("## 4. Граф контрагентов\n")
    w(
        f"- CLIENT хотя бы с одним известным контрагентом (без самопереводов): {pct((cl['n_cp_known'] > 0).mean())}."
    )
    w(f"- С ≥5 известными контрагентами: {pct((cl['n_cp_known'] >= 5).mean())}.")
    w(f"- Медианная доля оборота с известными контрагентами: {pct(cl['known_share'].median())}.")
    w(
        f"- Медианный HHI концентрации: {cl['hhi'].median():,.0f}; топ-1 контрагент > 30% оборота у "
        f"{pct((cl['top1_share'] > 0.3).mean())} клиентов."
    )
    w(
        f"- Доля оборота между компаниями одного класса ОКВЭД: {pct(same_industry_share(pay, comp, set(cl['SYNTHETIC_U'])))}.\n"
    )

    w("## 5. ВЭД: страны\n")
    countries = (
        cl.loc[cl["has_countries"], "FTS_COUNTRY_NAME"]
        .str.split(",")
        .explode()
        .str.strip()
        .value_counts()
        .head(12)
    )
    w(", ".join(f"{k} {v:,}" for k, v in countries.items()) + ".\n")

    w("## 6. Отрасли крупных клиентов (класс ОКВЭД)\n")
    names = cl.groupby("okved_class")["MAINOKVED_NAME"].agg(lambda s: s.value_counts().index[0])
    top = large["okved_class"].value_counts().head(15)
    w("| Класс | Пример названия | Крупных CLIENT |")
    w("|---|---|---|")
    for k, v in top.items():
        w(f"| {k} | {str(names.get(k, ''))[:70]} | {v:,} |")

    w("\n## 7. Выводы для ML\n")
    w(
        "1. Факторы «ставка» и «ВЭД» покрывают только часть клиентов, поэтому макро-новости (ставка, курс) "
        "нельзя показывать всем подряд: ранжировщик обязан различать «есть долг» и «нет долга»."
    )
    w(
        "2. Холодные профили — значимая доля: для них ленту держат отрасль (ОКВЭД → теги) и контрагенты, "
        "а агент обязан писать о неполноте данных (`incomplete_data`) и предлагать уточнить профиль."
    )
    w(
        "3. Граф контрагентов есть у большинства клиентов, но известна только часть оборота: связи второго порядка — "
        "сильный, но частичный сигнал; доля неизвестного оборота должна понижать уверенность."
    )
    w(
        "4. Внутриотраслевой оборот заметен: событие в отрасли клиента часто бьёт и по его контрагентам — "
        "это аргумент за компонент `counterparty` в скоринге и в тексте инсайта."
    )
    w(
        "5. Китай, Турция, Индия, ОАЭ доминируют в ВЭД: санкционные и платёжные новости по этим странам — "
        "приоритет для источников и словарей."
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"written {args.out}")


if __name__ == "__main__":
    main()
