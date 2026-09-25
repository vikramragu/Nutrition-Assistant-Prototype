"""Code-level enforcement of the three prohibited categories (architecture.md §8).

Deterministic, curated regex/keyword rules -- independent of the system prompt and
independent of any model call. Per problemStatement.md §5, this is the actual
enforcement; the system prompt is reinforcement, not the sole safeguard.

Rules here are a first pass, iterated against phrasings the team writes by hand.
Refining them against real failures is Phase 8's job (the fixed failure-log set),
not this module's.
"""

import re

from pydantic import BaseModel

from db.schemas import NutritionAnswer, ScopeCategory


class ScopeVerdict(BaseModel):
    blocked: bool
    reason: ScopeCategory | None = None


# Fixed, non-model-generated refusal text per category (architecture.md §8.2).
# Static templates so a refusal can never itself violate the boundary it enforces.
REFUSAL_MESSAGES: dict[ScopeCategory, str] = {
    "calorie_target": (
        "I can't provide specific calorie targets or calorie prescriptions. A registered "
        "dietitian or your physician can set a calorie target that accounts for your full "
        "health picture."
    ),
    "weight_target": (
        "I can't recommend a target weight or tell you what you should weigh. A registered "
        "dietitian or physician is best placed to discuss a healthy weight range for you "
        "specifically."
    ),
    "medical_advice": (
        "I can't provide medical advice, including diagnosis, treatment recommendations, or "
        "medication dosing. Please talk to a physician or other qualified healthcare provider "
        "about this."
    ),
}


def _compile_all(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


# --- Request-side: explicit asks (architecture.md §8.1 examples in comments) ---

_CALORIE_REQUEST_PATTERNS = _compile_all(
    [
        r"how many calories (should|do|would) i (eat|consume|need|have)",  # "how many calories should I eat"
        r"how many (calories|kcal) (per day|a day|daily)",
        r"what('s| is)? my (daily )?calorie (intake|target|goal|budget)",
        r"calculate my calorie (target|needs|intake)",
        r"calorie (target|prescription) for me",
        r"how many calories .{0,20}(to|should i) lose weight",
    ]
)

_WEIGHT_REQUEST_PATTERNS = _compile_all(
    [
        r"what should i weigh",  # "what should I weigh"
        r"what('s| is) my (ideal|target|healthy) weight",
        r"what weight should i (be|aim for)",
        r"how much (should|ought) i weigh",
        r"tell me my target weight",
    ]
)

_MEDICAL_REQUEST_PATTERNS = _compile_all(
    [
        r"diagnos(e|is) (my|this)",  # "diagnose my ..."
        r"do i have (a |an )?[a-z\s]+(condition|disease|disorder)",
        r"what (medication|drug|dose|dosage) should i take",
        r"(insulin|medication|drug) (dose|dosage)",
        r"how much (medication|insulin|medicine) should i (take|use)",
        r"treat my [a-z\s]+",
        r"am i (diabetic|anemic|deficient)",
    ]
)

# --- Response-side: prescriptive statements volunteered by the model ---
# Requires a personalizing/prescriptive cue, not just a bare fact, so an informational
# claim like "a banana has about 100 calories" does not trip this (see docs/edge-case.md).

_PRESCRIPTIVE_CUE = (
    r"(you should|you need to|you ought to|aim (for|to)|try to (eat|consume)|"
    r"your (daily )?(calorie|weight) (target|goal|intake|budget) (is|should be)|"
    r"i recommend|no more than|limit (yourself )?to|cut down to)"
)

_CALORIE_RESPONSE_PATTERNS = _compile_all(
    [
        rf"{_PRESCRIPTIVE_CUE}.{{0,60}}\d{{2,5}}\s*(calories|kcal)",
        rf"\d{{2,5}}\s*(calories|kcal).{{0,60}}{_PRESCRIPTIVE_CUE}",
    ]
)

_WEIGHT_RESPONSE_PATTERNS = _compile_all(
    [
        r"you should weigh",
        r"your (ideal|target|healthy) weight (is|should be)",
        r"(aim|try) (to|for) weigh(ing)?",
    ]
)

_MEDICAL_RESPONSE_PATTERNS = _compile_all(
    [
        r"you (have|are diagnosed with) [a-z\s]+(condition|disease|disorder|diabetes|cancer)",
        r"you should take \d+\s*(mg|mcg|ml|units?)",
        r"your diagnosis is",
    ]
)

_REQUEST_RULES: list[tuple[ScopeCategory, list[re.Pattern]]] = [
    ("calorie_target", _CALORIE_REQUEST_PATTERNS),
    ("weight_target", _WEIGHT_REQUEST_PATTERNS),
    ("medical_advice", _MEDICAL_REQUEST_PATTERNS),
]

_RESPONSE_RULES: list[tuple[ScopeCategory, list[re.Pattern]]] = [
    ("calorie_target", _CALORIE_RESPONSE_PATTERNS),
    ("weight_target", _WEIGHT_RESPONSE_PATTERNS),
    ("medical_advice", _MEDICAL_RESPONSE_PATTERNS),
]


def _first_match(text: str, rules: list[tuple[ScopeCategory, list[re.Pattern]]]) -> ScopeCategory | None:
    for category, patterns in rules:
        if any(p.search(text) for p in patterns):
            return category
    return None


def check_request(message: str) -> ScopeVerdict:
    """Pre-model check: does the user's message directly ask for a prohibited category?

    This is the primary, cheap gate -- it runs before the request is allowed to
    proceed to the model, per problemStatement.md §5.
    """
    reason = _first_match(message, _REQUEST_RULES)
    return ScopeVerdict(blocked=reason is not None, reason=reason)


def check_response(answer: NutritionAnswer) -> ScopeVerdict:
    """Post-model check: did the model volunteer a prohibited category unprompted?

    A pre-check on the question cannot catch a model that volunteers, e.g., a
    calorie number unprompted in an otherwise in-scope answer -- this closes that gap.
    """
    reason = _first_match(answer.answer, _RESPONSE_RULES)
    return ScopeVerdict(blocked=reason is not None, reason=reason)
