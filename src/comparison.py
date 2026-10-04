import pandas as pd
from .rule_filter import screen_candidates

def build_rule_tasks(investee_companies: pd.DataFrame, candidates: pd.DataFrame, thresholds: dict | None = None, candidate_pool: pd.DataFrame | None = None) -> pd.DataFrame:
    """Build review tasks from independent investee pools.

    ``candidate_pool=None`` preserves the legacy all-pairs behavior for callers that
    have not adopted pool construction yet. The application always supplies a pool.
    """
    frames=[]
    for _, investee in investee_companies.iterrows():
        investee_name=str(investee["company_name"])
        pool_rows=None
        selected_candidates=candidates
        if candidate_pool is not None:
            pool_rows=candidate_pool.loc[
                candidate_pool["investee_company_name"].astype(str).str.casefold()==investee_name.casefold()
            ].copy()
            selected_names=set(pool_rows["candidate_company_name"].astype(str).str.casefold())
            selected_candidates=candidates.loc[candidates["company_name"].astype(str).str.casefold().isin(selected_names)].copy()
        if selected_candidates.empty:
            continue
        frame=screen_candidates(investee,selected_candidates,thresholds)
        frame.insert(0,"investee_company_name",investee_name)
        if pool_rows is not None:
            metadata=pool_rows.set_index(pool_rows["candidate_company_name"].astype(str).str.casefold())
            keys=frame["company_name"].astype(str).str.casefold()
            for field in ("pool_source","pool_reason","system_matched","client_selected"):
                frame[field]=keys.map(metadata[field])
        frame["task_id"]=frame["company_name"].map(lambda candidate:f"{investee_name}::{candidate}")
        frames.append(frame)
    if frames:
        return pd.concat(frames,ignore_index=True)
    return pd.DataFrame(columns=["investee_company_name","company_name","rule_eligible","rule_flags","task_id"])

def compare_client_list(investee_name: str, candidates: pd.DataFrame, client_list: pd.DataFrame) -> pd.DataFrame:
    selected=set(client_list.loc[client_list.investee_company_name.astype(str).str.casefold()==str(investee_name).casefold(),"comparable_company_name"].astype(str).str.casefold()) if not client_list.empty else set()
    rows=[]
    for _, c in candidates.iterrows():
        rec=c.get("ai_recommendation","资料不足"); chosen=str(c.company_name).casefold() in selected
        if rec in ("需要人工判断","资料不足"): diff="AI无法判断，需要人工处理"
        elif rec=="建议纳入" and chosen: diff="AI建议纳入，客户已经纳入"
        elif rec=="建议纳入": diff="AI建议纳入，客户没有纳入"
        elif chosen: diff="AI建议排除，客户已经纳入"
        else: diff="AI建议排除，客户没有纳入"
        rows.append({"客户是否已选择":"是" if chosen else "否","差异类型":diff})
    return pd.concat([candidates.reset_index(drop=True),pd.DataFrame(rows)],axis=1)
