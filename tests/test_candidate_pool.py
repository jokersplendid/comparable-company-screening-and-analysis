import pandas as pd

from src.candidate_pool import build_candidate_pool
from src.comparison import build_rule_tasks


def test_each_investee_gets_only_its_own_industry_pool():
    investees=pd.DataFrame([
        {"company_id":"I1","company_name":"目标软件","industry":"企业软件","revenue":10,"total_assets":10},
        {"company_id":"I2","company_name":"目标包装","industry":"包装材料","revenue":20,"total_assets":20},
    ])
    candidates=pd.DataFrame([
        {"company_id":"C1","company_name":"软件甲","industry":"企业软件","revenue":10,"total_assets":10},
        {"company_id":"C2","company_name":"软件乙","industry":"企业软件","revenue":12,"total_assets":12},
        {"company_id":"C3","company_name":"包装甲","industry":"包装材料","revenue":20,"total_assets":20},
    ])
    pool,summary=build_candidate_pool(investees,candidates,pd.DataFrame(),min_pool_size=1)
    pairs=set(zip(pool.investee_company_name,pool.candidate_company_name))
    assert pairs=={("目标软件","软件甲"),("目标软件","软件乙"),("目标包装","包装甲")}
    tasks=build_rule_tasks(investees,candidates,candidate_pool=pool)
    assert set(zip(tasks.investee_company_name,tasks.company_name))==pairs
    assert not (summary["候选池状态"]=="候选不足，建议人工补充").any()


def test_client_pairs_are_merged_deduplicated_and_cross_industry_is_retained():
    investees=pd.DataFrame([{"company_id":"I1","company_name":"目标软件","industry":"企业软件","revenue":10,"total_assets":10}])
    candidates=pd.DataFrame([
        {"company_id":"C1","company_name":"软件甲","industry":"企业软件","revenue":10,"total_assets":10},
        {"company_id":"C2","company_name":"包装甲","industry":"包装材料","revenue":10,"total_assets":10},
    ])
    client=pd.DataFrame([
        {"investee_company_name":"目标软件","comparable_company_name":"软件甲"},
        {"investee_company_name":"目标软件","comparable_company_name":"包装甲"},
    ])
    pool,_=build_candidate_pool(investees,candidates,client,min_pool_size=5)
    assert len(pool)==2
    exact=pool.loc[pool.candidate_company_name=="软件甲"].iloc[0]
    cross=pool.loc[pool.candidate_company_name=="包装甲"].iloc[0]
    assert exact.pool_source=="系统匹配＋客户提供"
    assert bool(exact.client_selected)
    assert cross.pool_source=="客户提供"
    assert bool(cross.client_selected)


def test_shortage_is_only_a_warning_and_does_not_block_tasks():
    investees=pd.DataFrame([{"company_id":"I1","company_name":"小众目标","industry":"小众行业","revenue":10,"total_assets":10}])
    candidates=pd.DataFrame([{"company_id":"C1","company_name":"唯一同行","industry":"小众行业","revenue":10,"total_assets":10}])
    pool,summary=build_candidate_pool(investees,candidates,pd.DataFrame(),min_pool_size=5)
    assert summary.iloc[0]["候选池状态"]=="候选不足，建议人工补充"
    tasks=build_rule_tasks(investees,candidates,candidate_pool=pool)
    assert len(tasks)==1
    assert bool(tasks.iloc[0].rule_eligible)


def test_client_additions_do_not_hide_system_pool_shortage():
    investees=pd.DataFrame([{"company_id":"I1","company_name":"小众目标","industry":"小众行业","revenue":10,"total_assets":10}])
    candidates=pd.DataFrame([
        {"company_id":"C1","company_name":"唯一同行","industry":"小众行业","revenue":10,"total_assets":10},
        {"company_id":"C2","company_name":"客户跨行指定","industry":"其他行业","revenue":10,"total_assets":10},
    ])
    client=pd.DataFrame([{"investee_company_name":"小众目标","comparable_company_name":"客户跨行指定"}])
    pool,summary=build_candidate_pool(investees,candidates,client,min_pool_size=5)
    assert len(pool)==2
    assert summary.iloc[0]["系统建池候选数"]==1
    assert summary.iloc[0]["候选池状态"]=="候选不足，建议人工补充"


def test_different_detailed_codes_do_not_fall_back_to_same_name():
    investees=pd.DataFrame([{"company_id":"I1","company_name":"目标","industry":"软件","industry_code":"A01"}])
    candidates=pd.DataFrame([{"company_id":"C1","company_name":"候选","industry":"软件","industry_code":"A02"}])
    pool,summary=build_candidate_pool(investees,candidates,pd.DataFrame(),min_pool_size=1)
    assert pool.empty
    assert summary.iloc[0]["候选池状态"]=="候选不足，建议人工补充"


def test_major_industry_and_explicit_chain_fields_expand_only_when_needed():
    investees=pd.DataFrame([{
        "company_id":"I1","company_name":"目标","industry":"细分甲","industry_major_code":"M1",
        "related_industries":"邻近乙","main_business":"工业控制软件平台","revenue":10,"total_assets":10,
    }])
    candidates=pd.DataFrame([
        {"company_id":"C1","company_name":"大类相近","industry":"细分乙","industry_major_code":"M1","main_business":"工业控制软件系统","revenue":10,"total_assets":10},
        {"company_id":"C2","company_name":"邻近公司","industry":"邻近乙","industry_major_code":"M2","main_business":"设备制造","revenue":10,"total_assets":10},
        {"company_id":"C3","company_name":"无关公司","industry":"其他","industry_major_code":"M3","main_business":"消费零售","revenue":10,"total_assets":10},
    ])
    pool,_=build_candidate_pool(investees,candidates,pd.DataFrame(),min_pool_size=3,business_similarity_threshold=.1)
    assert set(pool.candidate_company_name)=={"大类相近","邻近公司"}
    assert "无关公司" not in set(pool.candidate_company_name)
