"""Company contexts straight from the organisers' CSVs, without the backend database (offline evaluation).

Reproduces the backend ingest (backend/app/services/ingest_companies.py, company_context.py) with pandas and
then calls the backend's own `build_context`, so feature paths and values are the ones the Tools API serves.
Used by eval/run_eval.py and for picking demo companies on the real data.
"""

import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.domain.cleaning import clean_company_rows  # noqa: E402
from app.domain.company_context import CompanyFacts, Counterparty, DirectionStats, build_context  # noqa: E402
from app.domain.okved import classify, load_reference, normalize_code  # noqa: E402

TOP_N = 10


def _read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, sep=";", dtype=str, keep_default_na=False)


class CsvCompanies:
    def __init__(self, data_dir: Path) -> None:
        self.ref = load_reference(ROOT / "backend" / "data" / "reference")
        raw = _read(data_dir / "df_Uid_data_hack.csv")
        raw.columns = [c.lower() for c in raw.columns]
        self.companies: dict[str, Any] = {}
        self.okved: dict[str, Any] = {}
        by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for rec in raw.to_dict("records"):
            by_id[str(rec["synthetic_u"])].append(rec)
        okved_cache: dict[tuple[str | None, str | None], Any] = {}
        for cid, records in by_id.items():
            code = normalize_code(records[0].get("mainokved_code"))
            c = clean_company_rows(cid, records, code)
            if c.is_valid:
                self.companies[cid] = c
                key = (c.okved_code, c.okved_name)
                if key not in okved_cache:
                    okved_cache[key] = classify(c.okved_code, c.okved_name, self.ref)
                self.okved[cid] = okved_cache[key]
        groups: dict[str, list[str]] = defaultdict(list)
        for cid, c in self.companies.items():
            if c.group_id:
                groups[c.group_id].append(cid)
        self.groups = groups
        pay = _read(data_dir / "df_Uid_payment_hack.csv")
        pay["amt"] = pd.to_numeric(pay["PAY_AMT"], errors="coerce").fillna(0.0)
        pay["cnt"] = pd.to_numeric(pay["PAY_CNT"], errors="coerce").fillna(0).astype(int)
        pay["payer"] = pay["PAYER"].str.strip()
        pay["ben"] = pay["BENEFICIARY"].str.strip()
        pay = pay[(pay["payer"] != "") & (pay["ben"] != "")]
        agg = pay.groupby(["payer", "ben"], as_index=False).agg(amt=("amt", "sum"), cnt=("cnt", "sum"))
        self.self_transfer = agg[agg["payer"] == agg["ben"]].set_index("payer")["amt"].to_dict()
        agg = agg[agg["payer"] != agg["ben"]].copy()
        known = set(self.companies)
        agg["ben_known"] = agg["ben"].isin(known)
        agg["payer_known"] = agg["payer"].isin(known)
        self.out_by = {k: v for k, v in agg.groupby("payer")}
        self.in_by = {k: v for k, v in agg.groupby("ben")}
        links = _read(data_dir / "df_Fid_Uid_link_hack.csv")
        by_person = links.groupby("SYNTHETIC_F")["SYNTHETIC_U"].apply(
            lambda s: sorted(set(s) & set(self.companies))
        )
        shared: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for comps in by_person:
            for a in comps:
                for b in comps:
                    if a != b:
                        shared[a][b] += 1
        self.shared = shared

    def _direction(self, cid: str, df: pd.DataFrame | None, cp_col: str) -> DirectionStats:
        if df is None or df.empty:
            return DirectionStats()
        own_group = self.companies[cid].group_id
        total = float(df["amt"].sum())
        if total <= 0:
            return DirectionStats()
        known_mask = df["ben_known"] if cp_col == "ben" else df["payer_known"]
        known_rub = float(df.loc[known_mask, "amt"].sum())
        hhi = float(((df["amt"] / total) ** 2).sum() * 10000)
        top = []
        for r in df.sort_values(["amt", cp_col], ascending=[False, True]).head(TOP_N).itertuples():
            other = getattr(r, cp_col)
            c2 = self.companies.get(other)
            o2 = self.okved.get(other)
            top.append(
                Counterparty(
                    counterparty_id=other,
                    amount_rub=float(r.amt),
                    tx_count=int(r.cnt),
                    share=float(r.amt) / total,
                    known=c2 is not None,
                    okved_code=c2.okved_code if c2 else None,
                    okved_name=c2.okved_name if c2 else None,
                    industry_tags=tuple(o2.industry_tags) if o2 else (),
                    region=c2.region if c2 else None,
                    same_group=bool(c2 and c2.group_id and c2.group_id == own_group),
                )
            )
        shares: dict[str, float] = defaultdict(float)
        for r in df[known_mask].itertuples():
            for tag in self.okved[getattr(r, cp_col)].industry_tags:
                shares[tag] += float(r.amt) / total
        return DirectionStats(
            total_rub=total,
            known_rub=known_rub,
            n_known=int(known_mask.sum()),
            n_unknown=int((~known_mask).sum()),
            hhi=hhi,
            top=tuple(top),
            industry_shares=dict(shares),
        )

    def facts(self, cid: str, user_provided: dict[str, Any] | None = None) -> CompanyFacts:
        cache: dict[str, CompanyFacts] = self.__dict__.setdefault("_facts_cache", {})
        if cid in cache and not user_provided:
            return cache[cid]
        cache[cid] = self._facts(cid, user_provided)
        return cache[cid]

    def _facts(self, cid: str, user_provided: dict[str, Any] | None = None) -> CompanyFacts:
        c = self.companies[cid]
        o = self.okved[cid]
        members = self.groups.get(c.group_id or "", [cid]) if c.group_id else [cid]
        revenues = [
            self.companies[m].revenue_rub for m in members if self.companies[m].revenue_rub is not None
        ]
        shared = sorted(self.shared.get(cid, {}).items(), key=lambda kv: (-kv[1], kv[0]))
        return CompanyFacts(
            company_id=cid,
            is_valid=True,
            client_type=c.client_type,
            okved_code=c.okved_code,
            okved_name=c.okved_name,
            okved_section=o.section,
            okved_section_name=o.section_name,
            industry_tags=tuple(o.industry_tags),
            okved_commodities=tuple(o.commodities),
            okved_flags={
                k: bool(o.flags.get(k))
                for k in ("consumer_demand", "import_dependent", "export_oriented", "rate_sensitive_industry")
            },
            region=c.region,
            city=c.city,
            age_bucket=c.age_bucket,
            staff_bucket=c.staff_bucket,
            eco_stage=c.eco_stage,
            okfs_name=c.okfs_name,
            foreign_ownership=c.foreign_ownership,
            owner_info_reliable=c.owner_info_reliable,
            revenue_rub=c.revenue_rub,
            revenue_stub=c.revenue_stub,
            group_id=c.group_id,
            group_size=len(members) if c.group_id else None,
            group_revenue_rub=sum(revenues, Decimal(0)) if revenues else None,
            group_client_count=sum(1 for m in members if self.companies[m].client_type == "CLIENT")
            if c.group_id
            else None,
            products=tuple(sorted(c.products)),
            fts_flag=c.fts_flag,
            fts_countries=tuple(sorted(c.countries)),
            incoming=self._direction(cid, self.in_by.get(cid), "payer"),
            outgoing=self._direction(cid, self.out_by.get(cid), "ben"),
            self_transfer_rub=float(self.self_transfer.get(cid, 0.0)),
            shared_person_companies=tuple(x for x, _ in shared),
            user_provided=user_provided or {},
        )

    def context(self, cid: str) -> dict[str, Any]:
        cache: dict[str, dict[str, Any]] = self.__dict__.setdefault("_ctx_cache", {})
        if cid in cache:
            return cache[cid]
        res = build_context(self.facts(cid))
        cache[cid] = {
            "company_id": cid,
            "context_version": 1,
            "is_valid": True,
            "features": res.features,
            "completeness": res.completeness,
        }
        return cache[cid]

    def group(self, cid: str, limit: int = 10) -> dict[str, Any]:
        c = self.companies[cid]
        members = self.groups.get(c.group_id or "", []) if c.group_id else []
        industries: dict[str, int] = defaultdict(int)
        for m in members:
            cls = self.okved[m].class_code
            if cls:
                industries[cls] += 1
        rev = [self.companies[m].revenue_rub for m in members if self.companies[m].revenue_rub is not None]
        total = float(sum(rev, Decimal(0))) if rev else None
        seg = (
            "large"
            if total and total > 15e9
            else "mid"
            if total and total >= 0.8e9
            else "small"
            if total
            else "unknown"
        )
        return {
            "group_id": c.group_id or cid,
            "size": max(1, len(members)),
            "revenue_rub": total,
            "segment": seg,
            "industries": dict(industries),
            "members": [
                {
                    "company_id": m,
                    "okved_code": self.companies[m].okved_code,
                    "okved_name": self.companies[m].okved_name,
                    "revenue_rub": float(self.companies[m].revenue_rub)
                    if self.companies[m].revenue_rub
                    else None,
                    "region": self.companies[m].region,
                    "is_client": self.companies[m].client_type == "CLIENT",
                }
                for m in members[:limit]
            ],
        }

    def counterparties(self, cid: str, limit: int = 10) -> dict[str, Any]:
        f = self.facts(cid)
        items = []
        for direction, d in (("out", f.outgoing), ("in", f.incoming)):
            for cp in d.top:
                items.append(
                    {
                        "counterparty_id": cp.counterparty_id,
                        "known": cp.known,
                        "direction": direction,
                        "amount_rub": cp.amount_rub,
                        "tx_count": cp.tx_count,
                        "share": round(cp.share, 4),
                        "okved_code": cp.okved_code,
                        "okved_name": cp.okved_name,
                        "industry_tags": list(cp.industry_tags),
                        "region": cp.region,
                        "same_group": cp.same_group,
                        "relation_hypothesis": None,
                    }
                )
        items.sort(key=lambda x: -x["amount_rub"])
        return {
            "company_id": cid,
            "totals": {
                "unknown_share_in": f.incoming.unknown_share,
                "unknown_share_out": f.outgoing.unknown_share,
                "hhi_in": f.incoming.hhi,
                "hhi_out": f.outgoing.hhi,
            },
            "items": items[:limit],
        }
