QUALITATIVE_DIMENSIONS=[
    {"name":"行业匹配","fields":["industry"],"weight":20},
    {"name":"主营业务","fields":["main_business","company_description"],"weight":25},
    {"name":"产品及应用场景","fields":["products","application_scenarios"],"weight":25},
    {"name":"客户群体","fields":["customer_groups"],"weight":15},
    {"name":"商业模式","fields":["business_model"],"weight":15},
]
QUANTITATIVE_DIMENSIONS=[
    {"name":"经营规模","fields":["revenue","total_assets"],"method":"relative","tolerance":1.0,"weight":35},
    {"name":"收入增长率","fields":["revenue_growth"],"method":"tolerance","tolerance":0.5,"weight":20},
    {"name":"毛利率","fields":["gross_margin"],"method":"tolerance","tolerance":0.3,"weight":20},
    {"name":"EBITDA利润率","fields":["ebitda_margin"],"method":"tolerance","tolerance":0.3,"weight":15},
    {"name":"资本结构（资产负债率）","fields":["debt_to_assets"],"method":"tolerance","tolerance":0.3,"weight":10},
]
QUALITATIVE_DEFAULT_WEIGHTS={d["name"]:d["weight"] for d in QUALITATIVE_DIMENSIONS}
QUANTITATIVE_DEFAULT_WEIGHTS={d["name"]:d["weight"] for d in QUANTITATIVE_DIMENSIONS}
GROUP_DEFAULT_WEIGHTS={"定性分析":70,"定量分析":30}
DEFAULT_WEIGHTS={**QUALITATIVE_DEFAULT_WEIGHTS,**QUANTITATIVE_DEFAULT_WEIGHTS}

def weighted_score(scores: dict, weights: dict | None=None) -> float:
    w=weights or DEFAULT_WEIGHTS
    observed={k:float(v) for k,v in scores.items() if k in w and v is not None}
    total=sum(max(0,float(w[k])) for k in observed)
    if not total:return 0.0
    return round(sum(observed[k]*max(0,float(w[k])) for k in observed)/total,2)

def score_breakdown(scores: dict, qualitative_weights: dict | None=None, quantitative_weights: dict | None=None, group_weights: dict | None=None) -> dict:
    """Score the qualitative and quantitative groups independently; unknown dimensions don't score as zero."""
    qw=QUALITATIVE_DEFAULT_WEIGHTS if qualitative_weights is None else qualitative_weights
    nw=QUANTITATIVE_DEFAULT_WEIGHTS if quantitative_weights is None else quantitative_weights
    gw=GROUP_DEFAULT_WEIGHTS if group_weights is None else group_weights
    q_score=weighted_score(scores,qw); n_score=weighted_score(scores,nw)
    q_coverage=sum(max(0,float(v)) for k,v in qw.items() if scores.get(k) is not None)/max(1,sum(max(0,float(v)) for v in qw.values()))
    n_coverage=sum(max(0,float(v)) for k,v in nw.items() if scores.get(k) is not None)/max(1,sum(max(0,float(v)) for v in nw.values()))
    available=[]
    if q_coverage>0: available.append((q_score,max(0,float(gw.get("定性分析",0)))))
    if n_coverage>0: available.append((n_score,max(0,float(gw.get("定量分析",0)))))
    total_weight=sum(weight for _,weight in available)
    overall=sum(score*weight for score,weight in available)/total_weight if total_weight else 0.0
    coverage=(q_coverage*max(0,float(gw.get("定性分析",0)))+n_coverage*max(0,float(gw.get("定量分析",0))))/max(1,sum(max(0,float(v)) for v in gw.values()))
    return {"定性得分":round(q_score,2),"定量得分":round(n_score,2),"综合得分":round(overall,2),"资料覆盖率":round(coverage*100,1),"定性覆盖率":round(q_coverage*100,1),"定量覆盖率":round(n_coverage*100,1)}

def quantitative_scores(investee: dict, candidate: dict, dimensions: list[dict] | None=None, growth_tolerance: float=.5, margin_tolerance: float=.3) -> dict:
    """Calculate configured quantitative similarities deterministically. Ratios use absolute differences."""
    import math
    dimensions=QUANTITATIVE_DIMENSIONS if dimensions is None else dimensions
    def number(record,key):
        try:
            value=float(record.get(key))
            return value if math.isfinite(value) else None
        except (TypeError,ValueError): return None
    scores={}
    for dim in dimensions:
        name=dim["name"]; fields=dim.get("fields",[]); method=dim.get("method","tolerance")
        tol=float(dim.get("tolerance",growth_tolerance if "增长" in name else margin_tolerance))
        pairs=[]
        # Allow debt-to-assets ratio to be derived where the source workbook has liabilities and assets.
        if fields==["debt_to_assets"]:
            def leverage(record):
                direct=number(record,"debt_to_assets")
                if direct is not None:return direct
                liabilities,assets=number(record,"total_liabilities"),number(record,"total_assets")
                return liabilities/assets if liabilities is not None and assets and assets>0 else None
            pairs=[(leverage(investee),leverage(candidate))]
        else:
            pairs=[(number(investee,key),number(candidate,key)) for key in fields]
        values=[]
        for a,b in pairs:
            if a is None or b is None:continue
            if method=="relative":
                if a<=0 or b<=0:continue
                values.append(max(0.0,min(100.0,100*math.exp(-abs(math.log(b/a))))))
            else:
                values.append(100.0 if tol<=0 and a==b else 0.0 if tol<=0 else max(0.0,min(100.0,100*(1-abs(a-b)/tol))))
        scores[name]=round(sum(values)/len(values),2) if values else None
    return scores
