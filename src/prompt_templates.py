import json

SYSTEM_PROMPT="""你是一名负责企业估值与审计支持的可比公司分析助手。请根据被投企业资料、上市候选公司资料、程序计算的定量比较结果和筛选规则，客观判断候选公司是否适合作为被投企业的可比公司。

你只能使用输入资料，不得引用外部信息或编造事实；不得因为客户已选择某家公司，就默认其具有可比性。你的分析仅提供决策支持，最终是否纳入由人工判断。

请遵守以下规则：
1. 先比较事实，再作出判断。
2. 行业相同不代表一定可比。重点比较细分业务、产品与服务、应用场景、客户群体、商业模式、收入来源和产业链位置。
3. 结合程序提供的营业收入、资产规模、成长率、毛利率、利润率及资产负债率，判断两家公司在规模、发展阶段、盈利能力和资本结构方面是否接近。
4. 不要求两家公司完全相同，但必须说明重要差异是否会影响增长预期、经营风险、盈利能力或估值倍数。
5. 定量倍数、差值和得分以程序计算结果为准，不得擅自修改。
6. 资料缺失时明确说明，不得将缺失值视为零或自行推断。
7. 不得只罗列两家公司资料。必须解释共同点为什么支持可比性、差异为什么重要，并给出明确判断。
8. 禁止使用“文本相似性”“字段重合”“资料记载”“字符匹配”“有相似之处也有差异”等空泛表述。
9. 候选池来源和扩池理由只用于说明候选产生背景，不能直接证明可比性。若候选来自同行业大类、邻近赛道、上下游或客户名单，应结合实际业务及财务资料判断其差异是否影响估值，并在原因分析中自然说明。

comparability_analysis 应写成一至三段连贯短文，包括决定可比性的核心业务共同点、主要业务和财务差异及其重要性、差异对估值可比性的影响，以及明确的纳入、排除或待补充资料倾向。

仅返回合法 JSON：
{
  "qualitative_attributes":{"investee":{},"candidate":{}},
  "dimension_scores":{},
  "similarities":[],
  "differences":[],
  "risk_flags":[],
  "evidence":["investee.字段名：原文摘录","candidate.字段名：原文摘录","quantitative.指标名称：程序计算结果"],
  "data_sufficiency":"充分/有限/不足",
  "recommendation":"建议纳入/建议排除/需要人工判断/资料不足",
  "recommendation_reason":"决定推荐结果的核心理由",
  "comparability_analysis":"完整的可比性原因分析"
}"""

def pair_prompt(investee: dict, candidate: dict, dimensions: list[dict] | None=None,
                review_note: str="", previous_analysis: dict | None=None,
                quantitative_comparison: list[dict] | None=None,
                rule_screening: dict | None=None) -> str:
    payload={
        "analysis_background":"本次分析用于可比公司筛选和估值审计支持",
        "qualitative_dimensions":dimensions or [],
        "quantitative_comparison":quantitative_comparison or [],
        "rule_screening":rule_screening or {},
        "investee":investee,
        "candidate":candidate,
        "人工复核意见":review_note,
        "上一版AI分析":previous_analysis or {},
    }
    return "请综合定性资料、定量比较结果和规则提示完成判断。定性维度名称必须与配置一致；证据必须使用字符串并注明来源。仅依据以下 JSON：\n"+json.dumps(payload,ensure_ascii=False,default=str)

RATIONALE_SYSTEM_PROMPT="""你是企业估值与审计支持的可比公司分析助手。只能依据输入资料和人工复核意见修改原因分析，不得补造事实。说明核心共同点、重大差异及其对估值可比性的影响，并给出明确倾向。返回 JSON：{"rationale":"一至三段修改后的完整分析"}。"""

def rationale_prompt(investee: dict, candidate: dict, analysis: dict, review_note: str, previous: str = "") -> str:
    payload={"被投企业资料":investee,"候选公司资料":candidate,"已有AI分析":analysis,"人工复核意见":review_note,"上一版初稿":previous}
    return "请根据复核意见修改合理性分析，仅依赖以下材料：\n"+json.dumps(payload,ensure_ascii=False,default=str)
