from typing import Literal, Any
import json
from pydantic import BaseModel, Field, field_validator

class SimilarityResult(BaseModel):
    dimension_scores: dict[str, float] = Field(default_factory=dict)
    qualitative_attributes: dict[str, Any] = Field(default_factory=dict)
    similarities: list[str] = Field(default_factory=list)
    differences: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    data_sufficiency: Literal["充分", "有限", "不足"] = "不足"
    recommendation: Literal["建议纳入", "建议排除", "需要人工判断", "资料不足"] = "资料不足"
    recommendation_reason: str = ""
    comparability_analysis: str = ""
    model: str = ""

    @field_validator("similarities", "differences", "risk_flags", "evidence", mode="before")
    @classmethod
    def normalize_text_lists(cls, value):
        """Accept structured evidence returned by OpenAI-compatible models."""
        if value is None:
            return []
        if not isinstance(value,list):
            value=[value]
        normalized=[]
        for item in value:
            if isinstance(item,str):
                normalized.append(item)
                continue
            if isinstance(item,dict):
                side=item.get("side") or item.get("company") or item.get("source") or ""
                field=item.get("field") or item.get("field_name") or ""
                excerpt=item.get("excerpt") or item.get("text") or item.get("quote") or item.get("evidence") or ""
                dimension=item.get("dimension") or item.get("category") or ""
                source=".".join(str(x) for x in (side,field) if x)
                prefix=f"{source}：" if source else (f"{dimension}：" if dimension else "")
                text=f"{prefix}{excerpt}".strip()
                normalized.append(text or json.dumps(item,ensure_ascii=False,default=str))
                continue
            normalized.append(str(item))
        return normalized
