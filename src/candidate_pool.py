import re
from difflib import SequenceMatcher

import pandas as pd


DETAIL_CODE_FIELDS = (
    "industry_subcode",
    "industry_code",
    "csrc_industry_code",
    "wind_industry_code",
)
MAJOR_CODE_FIELDS = (
    "industry_major_code",
    "industry_category_code",
    "industry_major",
    "industry_category",
)
RELATED_INDUSTRY_FIELDS = (
    "related_industries",
    "adjacent_industries",
    "upstream_industries",
    "downstream_industries",
)
BUSINESS_FIELDS = (
    "industry",
    "company_description",
    "main_business",
    "products",
    "application_scenarios",
    "customer_groups",
    "business_model",
)


def _text(value) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _norm(value) -> str:
    return re.sub(r"[\s\-_—·,，。；;、/\\]+", "", _text(value)).casefold()


def _same_value(left: pd.Series, right: pd.Series, fields: tuple[str, ...]) -> bool:
    for field in fields:
        a, b = _norm(left.get(field)), _norm(right.get(field))
        if a and b and a == b:
            return True
    return False


def _same_detailed_industry(investee: pd.Series, candidate: pd.Series) -> bool:
    common_code_fields = [
        field for field in DETAIL_CODE_FIELDS
        if _norm(investee.get(field)) and _norm(candidate.get(field))
    ]
    if common_code_fields:
        return any(_norm(investee.get(field)) == _norm(candidate.get(field)) for field in common_code_fields)
    # Old workbooks do not contain industry codes. Exact industry-name matching is
    # the conservative compatibility fallback and avoids a global Cartesian pool.
    a, b = _norm(investee.get("industry")), _norm(candidate.get("industry"))
    return bool(a and b and a == b)


def _business_similarity(investee: pd.Series, candidate: pd.Series) -> float:
    a = " ".join(_text(investee.get(field)) for field in BUSINESS_FIELDS if _text(investee.get(field)))
    b = " ".join(_text(candidate.get(field)) for field in BUSINESS_FIELDS if _text(candidate.get(field)))
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.casefold(), b.casefold()).ratio()


def _related_industry_match(investee: pd.Series, candidate: pd.Series) -> tuple[bool, str]:
    candidate_values = {
        value
        for field in ("industry",) + DETAIL_CODE_FIELDS + MAJOR_CODE_FIELDS
        if (value := _norm(candidate.get(field)))
    }
    for field in RELATED_INDUSTRY_FIELDS:
        raw = _text(investee.get(field))
        if not raw:
            continue
        values = {_norm(item) for item in re.split(r"[,，;；、/|]", raw) if _norm(item)}
        if values & candidate_values:
            labels = {
                "related_industries": "邻近赛道",
                "adjacent_industries": "邻近赛道",
                "upstream_industries": "上游行业",
                "downstream_industries": "下游行业",
            }
            return True, labels[field]
    return False, ""


def _candidate_key(row: pd.Series) -> str:
    return _norm(row.get("company_id")) or _norm(row.get("company_name"))


def build_candidate_pool(
    investees: pd.DataFrame,
    candidates: pd.DataFrame,
    client_list: pd.DataFrame | None = None,
    min_pool_size: int = 5,
    business_similarity_threshold: float = 0.18,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build one shared relation table while keeping each investee's pool independent.

    Exact-industry candidates are selected first. Broader-industry candidates are
    considered only when both sides provide a matching major-industry field; related
    or upstream/downstream expansion requires explicit relationship fields on the
    investee. Client-provided pairs are always retained when the candidate exists.
    """
    client_list = client_list if client_list is not None else pd.DataFrame()
    candidate_by_name = {
        _norm(row.get("company_name")): row
        for _, row in candidates.iterrows()
        if _norm(row.get("company_name"))
    }
    relations: list[dict] = []
    summaries: list[dict] = []

    for _, investee in investees.iterrows():
        investee_name = _text(investee.get("company_name"))
        investee_key = _candidate_key(investee)
        selected: dict[str, dict] = {}

        def add(candidate: pd.Series, source: str, reason: str, system_matched: bool = True):
            key = _candidate_key(candidate)
            if not key or key == investee_key:
                return
            existing = selected.get(key)
            if existing:
                sources = existing["pool_source"].split("＋")
                if source not in sources:
                    existing["pool_source"] += f"＋{source}"
                if reason and reason not in existing["pool_reason"]:
                    existing["pool_reason"] += f"；{reason}"
                existing["system_matched"] = existing["system_matched"] or system_matched
                existing["client_selected"] = existing["client_selected"] or source == "客户提供"
                return
            selected[key] = {
                "investee_company_name": investee_name,
                "candidate_company_name": _text(candidate.get("company_name")),
                "candidate_company_id": _text(candidate.get("company_id")),
                "pool_source": source,
                "pool_reason": reason,
                "system_matched": system_matched,
                "client_selected": source == "客户提供",
            }

        eligible_candidates = []
        for _, candidate in candidates.iterrows():
            if _candidate_key(candidate) and _candidate_key(candidate) != investee_key:
                eligible_candidates.append(candidate)

        for candidate in eligible_candidates:
            if _same_detailed_industry(investee, candidate):
                add(candidate, "系统匹配", "细分行业代码或行业名称一致")

        exact_count = len(selected)
        if len(selected) < min_pool_size:
            for candidate in eligible_candidates:
                if _candidate_key(candidate) in selected:
                    continue
                if _same_value(investee, candidate, MAJOR_CODE_FIELDS):
                    similarity = _business_similarity(investee, candidate)
                    if similarity >= business_similarity_threshold:
                        add(candidate, "系统扩池", f"行业大类一致且业务资料相似度为{similarity:.0%}")

        broad_count = len(selected)
        if len(selected) < min_pool_size:
            for candidate in eligible_candidates:
                if _candidate_key(candidate) in selected:
                    continue
                matched, relation = _related_industry_match(investee, candidate)
                if matched:
                    add(candidate, "系统扩池", f"同属{relation}范围，需在AI分析中核验估值逻辑")

        related_count = len(selected)
        missing_client_names: list[str] = []
        if not client_list.empty and {"investee_company_name", "comparable_company_name"}.issubset(client_list.columns):
            mask = client_list["investee_company_name"].map(_norm) == _norm(investee_name)
            for raw_name in client_list.loc[mask, "comparable_company_name"]:
                name = _text(raw_name)
                candidate = candidate_by_name.get(_norm(name))
                if candidate is None:
                    missing_client_names.append(name)
                    continue
                add(candidate, "客户提供", "客户原始名单指定", system_matched=False)

        total_count = len(selected)
        system_count = sum(1 for item in selected.values() if item["system_matched"])
        shortage = system_count < min_pool_size
        summaries.append({
            "被投企业": investee_name,
            "同行业匹配数": exact_count,
            "同行业大类扩池后": broad_count,
            "邻近赛道或上下游扩池后": related_count,
            "系统建池候选数": system_count,
            "合并客户名单后候选数": total_count,
            "候选池状态": "候选不足，建议人工补充" if shortage else "候选数量达到设定值",
            "客户名单缺失公司": "、".join(missing_client_names),
        })
        relations.extend(selected.values())

    relation_columns = [
        "investee_company_name",
        "candidate_company_name",
        "candidate_company_id",
        "pool_source",
        "pool_reason",
        "system_matched",
        "client_selected",
    ]
    summary_columns = [
        "被投企业",
        "同行业匹配数",
        "同行业大类扩池后",
        "邻近赛道或上下游扩池后",
        "系统建池候选数",
        "合并客户名单后候选数",
        "候选池状态",
        "客户名单缺失公司",
    ]
    return pd.DataFrame(relations, columns=relation_columns), pd.DataFrame(summaries, columns=summary_columns)
