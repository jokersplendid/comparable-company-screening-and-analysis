"""Comparable-company valuation calculations used by the Streamlit workflow."""
from __future__ import annotations

import math
import pandas as pd


VALUATION_FIELDS = [
    "market_cap", "cash", "interest_bearing_debt", "minority_interest",
    "preferred_stock", "non_operating_investments", "revenue", "ebitda",
    "ebitda_margin", "net_profit", "book_equity", "reporting_period",
    "valuation_date", "currency", "amount_unit",
]


def _number(value):
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _first_number(record: dict, *fields):
    for field in fields:
        value = _number(record.get(field))
        if value is not None:
            return value
    return None


def normalized_valuation_record(record: dict) -> dict:
    """Return the numeric values needed for valuation without inventing debt data."""
    revenue = _first_number(record, "revenue")
    ebitda = _first_number(record, "ebitda")
    margin = _first_number(record, "ebitda_margin")
    if ebitda is None and revenue is not None and margin is not None:
        ebitda = revenue * margin
    book_equity = _first_number(record, "book_equity")
    assets = _first_number(record, "total_assets")
    liabilities = _first_number(record, "total_liabilities")
    if book_equity is None and assets is not None and liabilities is not None:
        book_equity = assets - liabilities
    return {
        "market_cap": _first_number(record, "market_cap"),
        "cash": _first_number(record, "cash"),
        "interest_bearing_debt": _first_number(record, "interest_bearing_debt"),
        "minority_interest": _first_number(record, "minority_interest") or 0.0,
        "preferred_stock": _first_number(record, "preferred_stock") or 0.0,
        "non_operating_investments": _first_number(record, "non_operating_investments") or 0.0,
        "revenue": revenue,
        "ebitda": ebitda,
        "net_profit": _first_number(record, "net_profit"),
        "book_equity": book_equity,
    }


def enterprise_value(record: dict):
    values = normalized_valuation_record(record)
    if values["market_cap"] is None or values["cash"] is None or values["interest_bearing_debt"] is None:
        return None
    return (values["market_cap"] + values["interest_bearing_debt"]
            + values["minority_interest"] + values["preferred_stock"]
            - values["cash"] - values["non_operating_investments"])


def peer_multiples(record: dict) -> dict:
    values = normalized_valuation_record(record)
    ev = enterprise_value(record)
    def ratio(numerator, denominator):
        if numerator is None or denominator is None or denominator <= 0:
            return None
        return numerator / denominator
    return {
        "企业价值": ev,
        "EV/Revenue": ratio(ev, values["revenue"]),
        "EV/EBITDA": ratio(ev, values["ebitda"]),
        "P/E": ratio(values["market_cap"], values["net_profit"]),
        "P/B": ratio(values["market_cap"], values["book_equity"]),
    }


def build_peer_valuation_detail(included: pd.DataFrame, candidates: pd.DataFrame) -> pd.DataFrame:
    rows = []
    by_name = {str(row.get("company_name", "")): row.to_dict() for _, row in candidates.iterrows()}
    for _, pair in included.iterrows():
        candidate_name = str(pair.get("company_name", ""))
        record = by_name.get(candidate_name)
        if record is None:
            continue
        values = normalized_valuation_record(record)
        multiples = peer_multiples(record)
        rows.append({
            "被投企业": pair.get("investee_company_name", ""),
            "可比公司": candidate_name,
            "证券代码": record.get("stock_code", ""),
            "市值": values["market_cap"], "现金": values["cash"],
            "有息负债": values["interest_bearing_debt"],
            "少数股东权益": values["minority_interest"], "优先股": values["preferred_stock"],
            "非经营性投资": values["non_operating_investments"],
            "营业收入": values["revenue"], "EBITDA": values["ebitda"],
            "净利润": values["net_profit"], "账面净资产": values["book_equity"],
            "总资产": _number(record.get("total_assets")),
            **multiples,
        })
    return pd.DataFrame(rows)


def multiple_statistics(peer_detail: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for investee_name, group in peer_detail.groupby("被投企业", sort=False):
        for method in ["EV/Revenue", "EV/EBITDA", "P/E", "P/B"]:
            values = pd.to_numeric(group.get(method), errors="coerce").dropna()
            values = values[values > 0]
            if values.empty:
                continue
            rows.append({
                "被投企业": investee_name, "估值方法": method, "有效样本数": int(len(values)),
                "最小值": float(values.min()), "P25": float(values.quantile(.25)),
                "中位数": float(values.median()), "P75": float(values.quantile(.75)),
                "最大值": float(values.max()), "平均值": float(values.mean()),
            })
    return pd.DataFrame(rows)


def target_valuation(investee: dict, statistics: pd.DataFrame,
                     control_premium: float = 0.0, illiquidity_discount: float = 0.0) -> pd.DataFrame:
    values = normalized_valuation_record(investee)
    name = str(investee.get("company_name", ""))
    rows = []
    metric_map = {"EV/Revenue": "revenue", "EV/EBITDA": "ebitda", "P/E": "net_profit", "P/B": "book_equity"}
    for _, stat in statistics.loc[statistics["被投企业"].astype(str).eq(name)].iterrows():
        method = stat["估值方法"]
        metric = values[metric_map[method]]
        if metric is None or metric <= 0:
            continue
        result = {"被投企业": name, "估值方法": method, "被投企业指标": metric, "有效样本数": stat["有效样本数"]}
        for label, stat_col in [("低值", "P25"), ("中值", "中位数"), ("高值", "P75")]:
            implied = metric * float(stat[stat_col])
            if method.startswith("EV/"):
                debt = values["interest_bearing_debt"]
                cash = values["cash"]
                if debt is None or cash is None:
                    bridge = None; equity_before_adjustment = None
                else:
                    bridge = (-debt - values["minority_interest"] - values["preferred_stock"]
                              + cash + values["non_operating_investments"])
                    equity_before_adjustment = implied + bridge
                implied_ev = implied
            else:
                implied_ev = None; bridge = 0.0; equity_before_adjustment = implied
            equity = equity_before_adjustment
            if equity_before_adjustment is not None:
                equity = equity_before_adjustment * (1 + control_premium) * (1 - illiquidity_discount)
            result[f"{label}采用倍数"] = float(stat[stat_col])
            result[f"{label}隐含企业价值"] = implied_ev
            result[f"{label}企业价值转股权调整"] = bridge
            result[f"{label}调整前股权价值"] = equity_before_adjustment
            result[f"{label}股权价值"] = equity
        rows.append(result)
    return pd.DataFrame(rows)


def valuation_calculation_detail(results: pd.DataFrame, control_premium: float = 0.0,
                                 illiquidity_discount: float = 0.0) -> pd.DataFrame:
    """Convert the wide result table into a readable calculation trail."""
    rows=[]
    for _,row in results.iterrows():
        for scenario,source in [("低值（P25）","低值"),("中值（中位数）","中值"),("高值（P75）","高值")]:
            rows.append({
                "被投企业":row["被投企业"], "估值方法":row["估值方法"], "情景":scenario,
                "被投企业估值指标":row["被投企业指标"], "采用倍数":row[f"{source}采用倍数"],
                "隐含企业价值":row.get(f"{source}隐含企业价值"),
                "企业价值转股权调整":row.get(f"{source}企业价值转股权调整"),
                "调整前股权价值":row.get(f"{source}调整前股权价值"),
                "控制权溢价":control_premium, "流动性折价":illiquidity_discount,
                "最终股权价值":row[f"{source}股权价值"],
            })
    return pd.DataFrame(rows)


def apply_valuation_plan(investee: dict, plan: pd.DataFrame,
                         control_premium: float = 0.0,
                         illiquidity_discount: float = 0.0,
                         custom_methods: dict | None = None,
                         adjustments: pd.DataFrame | None = None) -> pd.DataFrame:
    """Apply a user-selected method/scenario/multiple plan to one investee."""
    values = normalized_valuation_record(investee)
    method_definitions = {
        "EV/Revenue": {"metric": "revenue", "basis": "EV", "formula": "企业价值 / 营业收入"},
        "EV/EBITDA": {"metric": "ebitda", "basis": "EV", "formula": "企业价值 / EBITDA"},
        "P/E": {"metric": "net_profit", "basis": "Equity", "formula": "股权价值 / 净利润"},
        "P/B": {"metric": "book_equity", "basis": "Equity", "formula": "股权价值 / 账面净资产"},
    }
    method_definitions.update(custom_methods or {})
    rows = []
    for _, setting in plan.iterrows():
        method = str(setting.get("估值方法", ""))
        definition = method_definitions.get(method)
        if not definition:
            continue
        metric_field = definition["metric"]
        metric = values.get(metric_field) if metric_field in values else _number(investee.get(metric_field))
        multiple = _number(setting.get("采用倍数"))
        if metric is None or metric <= 0 or multiple is None or multiple <= 0:
            continue
        implied = metric * multiple
        adjustment_notes = []
        def apply_adjustments(amount, stage):
            if amount is None or adjustments is None or adjustments.empty:
                return amount
            current = amount
            for _, adjustment in adjustments.loc[adjustments["调整阶段"].astype(str).eq(stage)].iterrows():
                value = _number(adjustment.get("数值"))
                if value is None or value == 0:
                    continue
                direction = 1 if str(adjustment.get("运算方式", "加")) == "加" else -1
                if str(adjustment.get("调整类型", "百分比")) == "百分比":
                    delta = current * value / 100 * direction
                    display_value = f"{value:g}%"
                else:
                    delta = value * direction
                    display_value = f"{value:g}"
                current += delta
                adjustment_notes.append(f"{adjustment.get('调整项名称','自定义调整')}：{adjustment.get('运算方式','加')}{display_value}")
            return current
        if definition["basis"] == "EV":
            implied = apply_adjustments(implied, "企业价值")
            debt, cash = values["interest_bearing_debt"], values["cash"]
            if debt is None or cash is None:
                implied_ev, bridge, equity_before_adjustment = implied, None, None
            else:
                bridge = (-debt - values["minority_interest"] - values["preferred_stock"]
                          + cash + values["non_operating_investments"])
                implied_ev, equity_before_adjustment = implied, implied + bridge
        else:
            implied_ev, bridge, equity_before_adjustment = None, 0.0, implied
        equity_before_custom_adjustments = equity_before_adjustment
        equity_after_custom_adjustments = apply_adjustments(equity_before_adjustment, "股权价值")
        final_equity = None if equity_after_custom_adjustments is None else equity_after_custom_adjustments * (1 + control_premium) * (1 - illiquidity_discount)
        rows.append({
            "被投企业": str(investee.get("company_name", "")), "估值方法": method,
            "估值公式": definition.get("formula", method),
            "情景": str(setting.get("情景", "自定义")), "被投企业估值指标": metric,
            "采用倍数": multiple, "隐含企业价值": implied_ev,
            "企业价值转股权调整": bridge, "调整前股权价值": equity_before_custom_adjustments,
            "自定义调整后股权价值": equity_after_custom_adjustments,
            "控制权溢价": control_premium, "流动性折价": illiquidity_discount,
            "自定义调整": "；".join(adjustment_notes) or "无",
            "最终股权价值": final_equity,
        })
    return pd.DataFrame(rows)


def valuation_summary(results: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for name, group in results.groupby("被投企业", sort=False):
        row = {"被投企业": name, "有效估值方法数": int(len(group)), "汇总方式": "各有效估值方法等权平均"}
        for label in ["低值", "中值", "高值"]:
            values = pd.to_numeric(group[f"{label}股权价值"], errors="coerce").dropna()
            row[f"股权价值{label}"] = float(values.mean()) if not values.empty else None
        rows.append(row)
    return pd.DataFrame(rows)
