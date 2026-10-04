import pandas as pd
from unittest.mock import Mock, patch
from src.rule_filter import screen_candidates, valuation_outliers
from src.scoring import weighted_score, score_breakdown, quantitative_scores
from src.comparison import compare_client_list, build_rule_tasks
from src.llm_client import LLMClient
from src.models import SimilarityResult

def test_revenue_screen_and_missing_data_flag():
    investee=pd.Series({"industry":"软件","revenue":100,"total_assets":100,"revenue_growth":.2,"gross_margin":.5,"ebitda_margin":.1})
    candidates=pd.DataFrame([{"company_name":"in-range","industry":"软件","revenue":100,"total_assets":100,"revenue_growth":.2,"gross_margin":.5,"ebitda_margin":.1},{"company_name":"out","industry":"软件","revenue":500,"total_assets":100,"revenue_growth":.2,"gross_margin":.5,"ebitda_margin":.1},{"company_name":"missing","industry":"软件","revenue":None,"total_assets":None,"revenue_growth":None,"gross_margin":None,"ebitda_margin":None}])
    result=screen_candidates(investee,candidates)
    assert result.rule_eligible.tolist()==[True,False,True]
    assert "缺失" in result.loc[2,"rule_flags"]

def test_industry_exact_and_iqr_outlier():
    investee=pd.Series({"industry":"软件","revenue":10,"total_assets":10,"revenue_growth":0,"gross_margin":.5,"ebitda_margin":.1})
    candidates=pd.DataFrame([{"company_name":str(i),"industry":"其他","revenue":10,"total_assets":10,"revenue_growth":0,"gross_margin":.5,"ebitda_margin":.1,"valuation_multiple":v} for i,v in enumerate([8,9,10,11,100])])
    result=screen_candidates(investee,candidates,{"industry_exact":True})
    assert not result.rule_eligible.any()
    assert "4" in valuation_outliers(candidates)

def test_weighted_score_normalizes_weights():
    assert weighted_score({"a":100,"b":0},{"a":1,"b":1})==50
    assert weighted_score({"a":100},{"a":0})==0

def test_client_list_discrepancy_types():
    c=pd.DataFrame([{"company_name":"A","ai_recommendation":"建议纳入"},{"company_name":"B","ai_recommendation":"建议排除"},{"company_name":"C","ai_recommendation":"需要人工判断"}])
    client=pd.DataFrame([{"investee_company_name":"Target","comparable_company_name":"B"}])
    out=compare_client_list("Target",c,client)
    assert out["差异类型"].tolist()==["AI建议纳入，客户没有纳入","AI建议排除，客户已经纳入","AI无法判断，需要人工处理"]

def test_build_rule_tasks_shows_pairs_for_all_investee_companies():
    investees=pd.DataFrame([{"company_name":"Target A","industry":"软件","revenue":10,"total_assets":10},{"company_name":"Target B","industry":"软件","revenue":20,"total_assets":20}])
    candidates=pd.DataFrame([{"company_name":"Peer 1","industry":"软件","revenue":10,"total_assets":10},{"company_name":"Peer 2","industry":"软件","revenue":20,"total_assets":20}])
    tasks=build_rule_tasks(investees,candidates)
    assert len(tasks)==4
    assert set(tasks.investee_company_name)=={"Target A","Target B"}
    assert tasks.task_id.nunique()==4

def test_demo_rationale_rewrite_works_without_api():
    client=LLMClient(api_key="",model="")
    text=client.generate_rationale({"company_name":"Target","industry":"软件","main_business":"云端运营管理软件","business_model":"SaaS订阅","gross_margin":.6,"ebitda_margin":.1},{"company_name":"Peer","industry":"软件","main_business":"云端运营管理平台","business_model":"订阅制SaaS","gross_margin":.58,"ebitda_margin":.11},{"similarities":["产品描述有交集"]},"补充分析客户集中度")
    assert "补充分析客户集中度" in text
    assert "因此" in text
    assert "本地演示分析" in text

def test_qualitative_and_quantitative_weights_are_separate_and_missing_is_unknown():
    breakdown=score_breakdown({"主营业务":80,"经营规模":None},{"主营业务":100},{"经营规模":100},{"定性分析":70,"定量分析":30})
    assert breakdown["定性得分"]==80
    assert breakdown["定量覆盖率"]==0
    assert breakdown["综合得分"]==80
    assert breakdown["资料覆盖率"]==70

def test_quantitative_similarity_is_deterministic_and_preserves_missing():
    investee={"revenue":100,"total_assets":200,"revenue_growth":.2,"gross_margin":.4,"ebitda_margin":.1,"debt_to_assets":.4}
    peer={"revenue":100,"total_assets":200,"revenue_growth":.2,"gross_margin":.4,"ebitda_margin":.1,"debt_to_assets":.4}
    scores=quantitative_scores(investee,peer)
    assert all(value==100.0 for value in scores.values())
    missing=quantitative_scores({"revenue":None,"total_assets":None},{"revenue":None,"total_assets":None},[{"name":"自定义指标","fields":["margin"],"method":"tolerance","tolerance":.2}])
    assert missing=={"自定义指标":None}

def test_capital_structure_can_be_calculated_from_liabilities_and_assets():
    dims=[{"name":"资本结构","fields":["debt_to_assets"],"method":"tolerance","tolerance":.2}]
    scores=quantitative_scores({"total_liabilities":40,"total_assets":100},{"debt_to_assets":.4},dims)
    assert scores["资本结构"]==100.0

def test_demo_analysis_accepts_custom_qualitative_dimensions():
    from src.llm_client import demo_analysis
    dims=[{"name":"渠道模式","fields":["business_model"],"weight":100}]
    result=demo_analysis({"company_name":"虚构目标","business_model":"SaaS订阅收费"},{"company_name":"虚构候选","business_model":"SaaS订阅收费"},dims,"请说明收入结构仍需人工核实")
    assert set(result.dimension_scores)=={"渠道模式"}
    assert result.qualitative_attributes["investee"]["渠道模式"]=="SaaS订阅收费"
    assert len(result.comparability_analysis)>150
    assert "收入结构仍需人工核实" in result.comparability_analysis
    assert "因此" in result.comparability_analysis
    assert "有相似处，也有差异" not in result.comparability_analysis

def test_demo_analysis_is_candidate_specific():
    from scripts.make_sample import build
    from src.llm_client import demo_analysis
    investees,candidates,_=build()
    target=investees.iloc[1].to_dict()
    first=demo_analysis(target,candidates.iloc[12].to_dict()).comparability_analysis
    fifth=demo_analysis(target,candidates.iloc[16].to_dict()).comparability_analysis
    assert "纸浆模塑餐盒" in first
    assert "外卖餐盒、杯具" in fifth
    assert "45.0%" in first
    assert "2.2倍" in fifth
    assert first!=fifth

def test_structured_model_evidence_is_normalized_to_text():
    result=SimilarityResult.model_validate({
        "evidence":[{"dimension":"行业匹配","side":"investee","field":"industry","excerpt":"企业软件"}],
        "similarities":[{"dimension":"主营业务","text":"均提供云端运营软件"}],
    })
    assert result.evidence==["investee.industry：企业软件"]
    assert result.similarities==["主营业务：均提供云端运营软件"]


def _mock_model_response(content):
    response=Mock()
    response.ok=True
    response.json.return_value={"choices":[{"message":{"content":content}}]}
    return response


def test_model_analysis_is_kept_without_required_connector_words():
    content='{"recommendation":"建议纳入","recommendation_reason":"两家公司业务接近","comparability_analysis":"两家公司主营业务及客户群体接近，可作为可比公司使用。"}'
    client=LLMClient(api_key="key",base_url="https://example.test/v1",model="model")
    with patch("src.llm_client.requests.post",return_value=_mock_model_response(content)):
        result=client.analyze({"company_name":"目标"},{"company_name":"候选"})
    assert result.recommendation=="建议纳入"
    assert result.comparability_analysis=="两家公司主营业务及客户群体接近，可作为可比公司使用。"


def test_plain_text_model_analysis_is_preserved_for_human_review():
    content="两家公司服务相同客户群体，但产品收入结构仍需进一步核实。"
    client=LLMClient(api_key="key",base_url="https://example.test/v1",model="model")
    with patch("src.llm_client.requests.post",return_value=_mock_model_response(content)):
        result=client.analyze({"company_name":"目标"},{"company_name":"候选"})
    assert result.recommendation=="需要人工判断"
    assert result.comparability_analysis==content
