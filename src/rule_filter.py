import numpy as np
import pandas as pd

NUMERIC = ["revenue", "total_assets", "revenue_growth", "gross_margin", "ebitda_margin"]

def valuation_outliers(candidates: pd.DataFrame) -> set:
    if "valuation_multiple" not in candidates: return set()
    x=pd.to_numeric(candidates["valuation_multiple"], errors="coerce").dropna()
    if len(x)<4: return set()
    q1,q3=x.quantile(.25),x.quantile(.75); iqr=q3-q1
    if iqr==0: return set()
    return set(candidates.loc[(pd.to_numeric(candidates.valuation_multiple, errors="coerce") < q1-1.5*iqr) | (pd.to_numeric(candidates.valuation_multiple, errors="coerce") > q3+1.5*iqr), "company_name"])

def screen_candidates(investee: pd.Series, candidates: pd.DataFrame, thresholds: dict | None = None) -> pd.DataFrame:
    t={"revenue_min":.3,"revenue_max":3.0,"asset_ratio_min":.2,"asset_ratio_max":5.0,"growth_diff":.5,"margin_diff":.3,"industry_exact":False}
    t.update(thresholds or {})
    out=candidates.copy().reset_index(drop=True)
    flags=[]; eligible=[]; ratios=[]
    outlier_names=valuation_outliers(out)
    for _, c in out.iterrows():
        rowflags=[]; passes=True
        ind=str(investee.get("industry", "")).strip().lower(); ci=str(c.get("industry", "")).strip().lower()
        if ind and ci and ind!=ci:
            rowflags.append("行业不同")
            if t["industry_exact"]: passes=False
        for field, low_key, high_key, label in [("revenue","revenue_min","revenue_max","收入规模倍数超出范围"),("total_assets","asset_ratio_min","asset_ratio_max","总资产规模倍数超出范围")]:
            a=pd.to_numeric(pd.Series([investee.get(field)]),errors="coerce").iloc[0]; b=pd.to_numeric(pd.Series([c.get(field)]),errors="coerce").iloc[0]
            if pd.notna(a) and pd.notna(b) and a>0:
                ratio=float(b/a)
                if field=="revenue": ratios.append(ratio)
                if ratio<t[low_key] or ratio>t[high_key]: rowflags.append(label); passes=False
            else: rowflags.append(f"{field}缺失，需关注")
        for field,label in [("revenue_growth","增速差异较大"),("gross_margin","毛利率差异较大"),("ebitda_margin","EBITDA利润率差异较大")]:
            a=pd.to_numeric(pd.Series([investee.get(field)]),errors="coerce").iloc[0]; b=pd.to_numeric(pd.Series([c.get(field)]),errors="coerce").iloc[0]
            if pd.isna(a) or pd.isna(b): rowflags.append(f"{field}缺失，需关注")
            elif abs(float(a)-float(b))>t["growth_diff" if field=="revenue_growth" else "margin_diff"]: rowflags.append(label)
        if c.get("company_name") in outlier_names: rowflags.append("估值倍数IQR异常值，单独复核")
        eligible.append(passes); flags.append("；".join(dict.fromkeys(rowflags)) or "当前规则未发现需提示事项")
    out["rule_eligible"]=eligible; out["rule_flags"]=flags
    return out
