import json, os
import requests
from .models import SimilarityResult
from .prompt_templates import SYSTEM_PROMPT, pair_prompt, RATIONALE_SYSTEM_PROMPT, rationale_prompt

class LLMClient:
    def __init__(self, api_key=None, base_url=None, model=None, thinking=None):
        self.api_key=api_key or os.getenv("LLM_API_KEY","")
        self.base_url=(base_url or os.getenv("LLM_BASE_URL","https://api.openai.com/v1")).rstrip("/")
        self.model=model or os.getenv("LLM_MODEL","")
        self.thinking=(thinking or os.getenv("LLM_THINKING","disabled")).strip().lower()
    @property
    def configured(self): return bool(self.api_key and self.model)

    def validate_config(self) -> None:
        if not self.api_key:
            raise ValueError("API Key 不能为空。")
        if not self.model:
            raise ValueError("模型名称不能为空。")
        if any(separator in self.model for separator in (";", "；", ",", "，", "\n")):
            raise ValueError("模型名称只能填写一个，不能同时填写多个模型。")
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("接口地址必须以 http:// 或 https:// 开头。")
        if self.thinking not in {"enabled","disabled"}:
            raise ValueError("推理模式只能是 enabled 或 disabled。")

    def test_connection(self) -> str:
        """Use the same Chat Completions path as analysis to verify the configuration."""
        self.validate_config()
        response=requests.post(
            self.base_url+"/chat/completions",
            headers={"Authorization":f"Bearer {self.api_key}","Content-Type":"application/json"},
            json={
                "model":self.model,
                "temperature":0,
                "thinking":{"type":self.thinking},
                "max_tokens":200,
                "response_format":{"type":"json_object"},
                "messages":[
                    {"role":"system","content":"你是接口连通性测试助手，只返回 JSON。"},
                    {"role":"user","content":"请返回 {\"status\":\"ok\"}。"},
                ],
            },
            timeout=45,
        )
        if not response.ok:
            raise RuntimeError(self._http_error(response))
        data=response.json()
        content=data.get("choices",[{}])[0].get("message",{}).get("content","")
        if not content:
            raise RuntimeError("接口已响应，但没有返回 Chat Completions 内容。")
        return content

    @staticmethod
    def _http_error(response) -> str:
        try:
            detail=response.json()
        except Exception:
            detail=response.text
        detail=str(detail).replace("\n"," ")[:500]
        return f"模型接口调用失败（HTTP {response.status_code}）：{detail}"
    def generate_rationale(self, investee: dict, candidate: dict, analysis: dict, review_note: str, previous: str = "") -> str:
        """Revise a human-in-review rationale using only the supplied records and reviewer note."""
        if not self.configured:
            result=demo_analysis(investee,candidate,review_note=review_note)
            return result.comparability_analysis+"\n\n本段为虚构数据的本地演示分析，正式项目应由经批准的模型生成并由人工复核。"
        self.validate_config()
        response=requests.post(self.base_url+"/chat/completions",headers={"Authorization":f"Bearer {self.api_key}"},json={"model":self.model,"temperature":0,"thinking":{"type":self.thinking},"max_tokens":2200,"response_format":{"type":"json_object"},"messages":[{"role":"system","content":RATIONALE_SYSTEM_PROMPT},{"role":"user","content":rationale_prompt(investee,candidate,analysis,review_note,previous)}]},timeout=90)
        if not response.ok: raise RuntimeError(self._http_error(response))
        payload=json.loads(response.json()["choices"][0]["message"]["content"])
        rationale=payload.get("rationale","")
        if not isinstance(rationale,str) or not rationale.strip(): raise ValueError("模型未返回有效的 rationale 字段。")
        return rationale.strip()

    def analyze(self, investee: dict, candidate: dict, dimensions: list[dict] | None=None, review_note: str="", previous_analysis: dict | None=None, quantitative_comparison: list[dict] | None=None, rule_screening: dict | None=None) -> SimilarityResult:
        if not self.configured:
            return demo_analysis(investee,candidate,dimensions,review_note)
        self.validate_config()
        response=requests.post(self.base_url+"/chat/completions",headers={"Authorization":f"Bearer {self.api_key}"},json={"model":self.model,"temperature":0,"thinking":{"type":self.thinking},"max_tokens":3200,"response_format":{"type":"json_object"},"messages":[{"role":"system","content":SYSTEM_PROMPT},{"role":"user","content":pair_prompt(investee,candidate,dimensions,review_note,previous_analysis,quantitative_comparison,rule_screening)}]},timeout=90)
        if not response.ok: raise RuntimeError(self._http_error(response))
        content=response.json()["choices"][0]["message"].get("content","")
        if not isinstance(content,str) or not content.strip():
            raise ValueError("模型接口已响应，但没有返回可写入的文字内容。")
        try:
            payload=json.loads(content)
        except (json.JSONDecodeError,TypeError):
            # Some OpenAI-compatible providers ignore response_format. Preserve a
            # non-empty plain-text response instead of charging the call and then
            # discarding the analysis.
            result=SimilarityResult(
                recommendation="需要人工判断",
                recommendation_reason="模型返回了普通文本，请人工复核其结论。",
                comparability_analysis=content.strip(),
            )
        else:
            if not isinstance(payload,dict):
                payload={"comparability_analysis":content.strip()}
            analysis=payload.get("comparability_analysis")
            if not isinstance(analysis,str) or not analysis.strip():
                for key in ("rationale","analysis","reason","recommendation_reason"):
                    value=payload.get(key)
                    if isinstance(value,str) and value.strip():
                        analysis=value.strip()
                        break
            if not isinstance(analysis,str) or not analysis.strip():
                # The response contains text but does not follow the requested
                # schema. Keep the raw response visible for human review.
                analysis=content.strip()
            payload["comparability_analysis"]=analysis
            result=SimilarityResult.model_validate(payload)
        result.model=self.model
        # Require each evidence excerpt to appear verbatim in one of the supplied text fields.
        corpus=" ".join(str(v) for d in (investee,candidate) for v in d.values() if v is not None)
        result.evidence=[e for e in result.evidence if isinstance(e,str) and any(part.strip('“”\"') in corpus for part in e.split("：") if len(part.strip())>6)]
        if not result.evidence:
            result.data_sufficiency="不足"
            if not result.recommendation_reason.strip():
                result.recommendation_reason="模型未提供可在输入资料中核验的原文证据，请人工复核。"
            if "模型未提供可核验原文证据" not in result.risk_flags:
                result.risk_flags.append("模型未提供可核验原文证据")
        return result

    def revise_comparability_analysis(self, investee: dict, candidate: dict, analysis: dict, review_note: str, dimensions: list[dict] | None=None) -> SimilarityResult:
        """Re-run one pair using the human review note and previous AI output."""
        if not review_note.strip(): raise ValueError("请先填写该条任务的人工复核意见。")
        return self.analyze(investee,candidate,dimensions,review_note,analysis)

def demo_analysis(a: dict,b: dict,dimensions: list[dict] | None=None,review_note: str="") -> SimilarityResult:
    from .scoring import QUALITATIVE_DIMENSIONS, weighted_score
    dimensions=QUALITATIVE_DIMENSIONS if dimensions is None else dimensions
    scores={}
    sims=[]; diffs=[]; evidence=[]
    attributes={"investee":{},"candidate":{}}
    for dimension in dimensions:
        dim=dimension["name"]; field_list=dimension.get("fields",[])
        av=" ".join(str(a.get(field,"")) for field in field_list if a.get(field) is not None and str(a.get(field)).strip()); bv=" ".join(str(b.get(field,"")) for field in field_list if b.get(field) is not None and str(b.get(field)).strip())
        attributes["investee"][dim]=av or "资料未说明"; attributes["candidate"][dim]=bv or "资料未说明"
        if av and bv:
            if "industry" in field_list:
                similarity=1.0 if av==bv else 0.0
            else:
                compact_a="".join(av.lower().split()); compact_b="".join(bv.lower().split())
                at={compact_a[i:i+2] for i in range(max(1,len(compact_a)-1))} if len(compact_a)>1 else {compact_a}
                bt={compact_b[i:i+2] for i in range(max(1,len(compact_b)-1))} if len(compact_b)>1 else {compact_b}
                similarity=len(at&bt)/max(1,len(at|bt))
            scores[dim]=round(40+similarity*60)
            if similarity>0.08 or av==bv: sims.append(f"{dim}：资料文本存在一定重合，建议核对其业务含义。")
            else: diffs.append(f"{dim}：输入资料文本存在差异，需判断差异对可比性的影响。")
        else: diffs.append(f"{dim}信息不足，无法比较。")
        for field in field_list:
            if a.get(field): evidence.append(f"被投企业.{field}：{str(a[field])[:160]}")
            if b.get(field): evidence.append(f"候选公司.{field}：{str(b[field])[:160]}")
    sufficient=sum(1 for d in dimensions if attributes["investee"].get(d["name"])!="资料未说明" and attributes["candidate"].get(d["name"])!="资料未说明")
    weights={d["name"]:d.get("weight",1) for d in dimensions}; total=weighted_score(scores,weights)
    investee_name=a.get("company_name","被投企业"); candidate_name=b.get("company_name","候选公司")
    def text(record,*fields):
        return "；".join(str(record.get(field)).strip() for field in fields if record.get(field) is not None and str(record.get(field)).strip())
    def number(record,key):
        try:return float(record.get(key))
        except (TypeError,ValueError):return None
    a_business=text(a,"main_business","products","application_scenarios") or text(a,"company_description") or "现有资料未说明"
    b_business=text(b,"main_business","products","application_scenarios") or text(b,"company_description") or "现有资料未说明"
    corpus_a=text(a,"company_description","main_business","products","application_scenarios","customer_groups","business_model").lower()
    corpus_b=text(b,"company_description","main_business","products","application_scenarios","customer_groups","business_model").lower()
    concept_map=[("云端运营管理与流程数字化",("云端","运营","流程")),("SaaS订阅软件",("saas","订阅")),("食品及零售包装",("食品","包装","餐盒")),("环保包装材料",("环保","包装","可降解")),("芯片与半导体",("芯片","mcu","半导体")),("金融服务",("金融","银行","保险")),("农业与种业",("农业","种业")),("光学及激光技术",("光学","激光","光束"))]
    shared=[]
    for label,keywords in concept_map:
        if any(k in corpus_a for k in keywords) and any(k in corpus_b for k in keywords): shared.append(label)
    industry_a=str(a.get("industry") or "").strip(); industry_b=str(b.get("industry") or "").strip()
    industry_same=bool(industry_a and industry_a==industry_b)
    model_a=str(a.get("business_model") or "资料未说明"); model_b=str(b.get("business_model") or "资料未说明")
    recurring_a=any(k in model_a.lower() for k in ("saas","订阅")); recurring_b=any(k in model_b.lower() for k in ("saas","订阅"))
    financial=[]
    for key,label,scale in [("revenue_growth","收入增长率",100),("gross_margin","毛利率",100),("ebitda_margin","EBITDA利润率",100),("debt_to_assets","资产负债率",100)]:
        av,bv=number(a,key),number(b,key)
        if av is not None and bv is not None:
            financial.append((label,av,bv,abs(av-bv),scale))
    close=sorted((x for x in financial if x[3]<=.05),key=lambda x:x[3])
    far=sorted(financial,key=lambda x:x[3],reverse=True)
    revenue_a,revenue_b=number(a,"revenue"),number(b,"revenue")
    scale_close=bool(revenue_a and revenue_b and max(revenue_a,revenue_b)/min(revenue_a,revenue_b)<=3)
    core_overlap=industry_same and (bool(shared) or total>=55)
    if core_overlap and (len(close)>=2 or scale_close): recommendation="建议纳入"
    elif not industry_same or (not shared and total<48): recommendation="建议排除"
    else: recommendation="需要人工判断"
    common_basis="、".join(shared[:3]) if shared else (industry_a if industry_same else "现有资料尚未显示明确的核心业务重合")
    if close:
        finance_sentence="、".join(f"{label}为{av*scale:.1f}%和{bv*scale:.1f}%" for label,av,bv,_,scale in close[:2])
    else: finance_sentence="现有财务指标未显示足够接近的经营特征"
    material=[x for x in far if x[3]>.05]
    if material:
        label,av,bv,gap,scale=material[0]
        difference_sentence=f"但{candidate_name}的{label}为{bv*scale:.1f}%，与被投企业的{av*scale:.1f}%相差{gap*scale:.1f}个百分点，该差异可能反映增长阶段、定价能力或资本结构不同"
    else:
        difference_sentence="各项已提供的盈利与资本结构指标差异均不超过5个百分点，未显示明显的经营特征背离"
    if revenue_a and revenue_b:
        ratio=revenue_b/revenue_a
        if ratio>=1.35: scale_sentence=f"{candidate_name}营业收入约为被投企业的{ratio:.1f}倍，规模较大可能带来成本摊薄和议价能力差异"
        elif ratio<=.75: scale_sentence=f"{candidate_name}营业收入约为被投企业的{ratio:.1%}，规模较小可能导致客户集中度和成本结构不同"
        else: scale_sentence=f"{candidate_name}营业收入约为被投企业的{ratio:.1%}，收入规模处于相近区间"
    else: scale_sentence="现有资料不足以比较两家公司的收入规模"
    if recommendation=="建议纳入":
        model_sentence=(f"两家公司均采用订阅制软件模式，但被投企业还包含实施服务，可能影响收入确认节奏。" if recurring_a and recurring_b else f"两家公司当前披露的商业模式分别为“{model_a}”和“{model_b}”。")
        comparability=(f"{investee_name}与{candidate_name}同属{industry_a}行业，均围绕{common_basis}开展业务。{candidate_name}的主要产品和应用场景为“{b_business}”，与被投企业的“{a_business}”共同服务于相近的客户需求，这是支持可比性的核心依据。{model_sentence}经营指标方面，{finance_sentence}；{difference_sentence}；同时，{scale_sentence}。综合业务重合程度和上述差异，现有差异尚不足以否定其可比性，因此选择{candidate_name}作为可比公司具有合理性，但应针对该公司最显著的差异补充核实其收入构成及持续性。")
    elif recommendation=="建议排除":
        comparability=(f"{investee_name}的核心业务为“{a_business}”，而{candidate_name}主要从事“{b_business}”。两家公司在行业定位、主要产品、目标客户或收入形成方式上缺乏共同的业务逻辑；即使个别规模或利润指标接近，也不足以抵消主营业务和风险驱动的实质差异。因此，不建议将{candidate_name}作为可比公司。")
    else:
        comparability=(f"{investee_name}与{candidate_name}在{common_basis}方面具备一定共同基础，候选公司的主要业务为“{b_business}”。但现有资料尚不能证明该业务与被投企业的“{a_business}”具有相同的主要收入来源和盈利模式。经营指标方面，{finance_sentence}；{difference_sentence}；{scale_sentence}。这些差异可能影响利润率、增长预期和估值倍数，因此当前仅将{candidate_name}保留为待核实候选，需进一步取得其主要产品收入占比、客户结构和收费方式后再作判断。")
    if review_note.strip(): comparability+=f" 针对复核意见“{review_note.strip()}”，本次演示分析已将其列为重点核实事项。"
    return SimilarityResult(dimension_scores=scores,qualitative_attributes=attributes,similarities=sims,differences=diffs,risk_flags=["演示模式：字符文本重合启发式仅展示自定义维度流程，不代表真实模型判断"],evidence=evidence,data_sufficiency="有限" if sufficient else "不足",recommendation=recommendation,recommendation_reason=f"本地演示定性启发式得分 {total:.1f}；定量相似度由程序另行计算。本模拟仅用于演示排序，不代表模型判断或审计结论。",comparability_analysis=comparability,model="Demo heuristic")
