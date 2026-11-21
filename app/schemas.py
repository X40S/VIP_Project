"""Единая схема результатов анализа. Используется для AI-режима и для импорта analysis_results.json."""
from typing import Literal, Optional

from pydantic import BaseModel, Field

SCHEMA_VERSION = 2

Score = Optional[int]   # 0-100, null = критерий не применим

Category = Literal["rg_safety", "operational", "communication"]
Severity = Literal["low", "medium", "high", "critical"]
RgSignal = Literal["none", "self_exclusion_request", "loss_of_control", "serious_harm",
                   "self_harm_risk", "ambiguous_distress"]
Handled = Literal["yes", "partly", "no", "not_applicable"]
EmotionState = Literal["neutral", "irritation", "dissatisfaction", "disappointment",
                       "detachment", "positive", "formal_agreement", "resolved"]
RecKind = Literal["protective", "service", "commercial", "coaching"]
RiskFlag = Literal["chargeback_threat", "churn_threat"]
ViolationCode = Literal[
    # rg_safety
    "IGNORED_SELF_EXCLUSION", "IGNORED_HARM_SIGNAL", "INCENTIVE_AFTER_STOP_REQUEST",
    # operational
    "FALSE_PROMISE", "CHASING_INCENTIVE", "MISINFORMATION", "UNRESOLVED_REQUEST",
    "NO_ESCALATION", "BONUS_RULE_BREACH", "CONSENT_BREACH", "COMPLIANCE_RISK",
    # communication
    "LOW_EMPATHY", "DRY_TONE", "RUDE_TONE", "INCOMPLETE_ANSWER", "NO_FOLLOW_UP",
    "SLOW_RESPONSE", "PERSONALIZATION_ERROR",
]

CODE_CATEGORY: dict[str, str] = {
    "IGNORED_SELF_EXCLUSION": "rg_safety", "IGNORED_HARM_SIGNAL": "rg_safety",
    "INCENTIVE_AFTER_STOP_REQUEST": "rg_safety",
    "FALSE_PROMISE": "operational", "CHASING_INCENTIVE": "operational", "MISINFORMATION": "operational",
    "UNRESOLVED_REQUEST": "operational", "NO_ESCALATION": "operational", "BONUS_RULE_BREACH": "operational",
    "CONSENT_BREACH": "operational", "COMPLIANCE_RISK": "operational",
    "LOW_EMPATHY": "communication", "DRY_TONE": "communication", "RUDE_TONE": "communication",
    "INCOMPLETE_ANSWER": "communication", "NO_FOLLOW_UP": "communication", "SLOW_RESPONSE": "communication",
    "PERSONALIZATION_ERROR": "communication",
}
STOP_SIGNALS = ("self_exclusion_request", "loss_of_control", "serious_harm", "self_harm_risk")


class PlayerProfile(BaseModel):
    summary: Optional[str] = None   # 1-2 коротких предложения


class CommQuality(BaseModel):
    tone: Score = Field(default=None, ge=0, le=100)
    empathy: Score = Field(default=None, ge=0, le=100)
    engagement: Score = Field(default=None, ge=0, le=100)
    personalization: Score = Field(default=None, ge=0, le=100)
    problem_understanding: Score = Field(default=None, ge=0, le=100)
    completeness: Score = Field(default=None, ge=0, le=100)
    follow_up: Score = Field(default=None, ge=0, le=100)
    next_steps_clarity: Score = Field(default=None, ge=0, le=100)
    overall: int = Field(ge=0, le=100)
    summary: str


class PlayerPoint(BaseModel):
    message_id: str
    dissatisfaction: int = Field(ge=0, le=100)
    state: EmotionState


class ManagerPoint(BaseModel):
    message_id: str
    engagement: int = Field(ge=0, le=100)


class Rg(BaseModel):
    signal: RgSignal
    handled: Handled
    message_refs: list[str]


class Violation(BaseModel):
    title: str
    category: Category
    code: ViolationCode
    severity: Severity
    explanation: str
    message_refs: list[str]


class Recommendation(BaseModel):
    text: str
    kind: RecKind
    message_refs: list[str]


class Analysis(BaseModel):
    profile: PlayerProfile
    quality: CommQuality
    player_points: list[PlayerPoint]
    manager_points: list[ManagerPoint]
    rg: Rg
    risk_flags: list[RiskFlag]
    violations: list[Violation]
    recommendations: list[Recommendation]


# ---------- импорт готовых результатов (режим B) ----------

class SourceInfo(BaseModel):
    type: Literal["claude_code", "app"]
    model: Optional[str] = None


class ImportResult(BaseModel):
    dialog_id: str
    player_id: str
    manager_id: str
    analysis: Analysis
    meta: Optional[dict] = None   # заполняется только файлами, сохранёнными приложением


class ImportFile(BaseModel):
    schema_version: int
    rules_version: str
    source: SourceInfo
    generated_at: Optional[str] = None
    results: list[ImportResult]
