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
    "personalised_guidance": (
        "I can only report what the guidance documents say for a population, not turn it into "
        "a personal recommendation or target for you. A registered dietitian can work out what "
        "these figures mean for your situation."
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
        # The rule is about the assistant *asserting* a condition -- "you have diabetes".
        # The lookbehinds exclude the hypothetical, which means the opposite thing: the
        # corpus's own referral advice is phrased that way, and blocking it is over_refusal
        # on the single safest sentence in the document.
        #
        # Found by measurement, Phase 2.8: of the 159 second-person sentences in the corpus,
        # exactly one tripped this guard -- the Dietary Guidelines' "If you have a chronic
        # disease, talk with your health care professional...". A faithful quotation of a
        # referral was being classified as giving medical advice.
        # `[a-z\s]*`, not `+`. With `+` the filler was mandatory, so "you have a thyroid
        # condition" was caught but the blunter "you have diabetes" was **not** -- a
        # missed_scope_restriction hole, and the more serious of the two defects this
        # pattern had. Found while verifying the lookbehind fix above.
        r"(?<!if )(?<!whether )(?<!when )(?<!should )"
        r"you (have|are diagnosed with) [a-z\s]*(condition|disease|disorder|diabetes|cancer)",
        r"you should take \d+\s*(mg|mcg|ml|units?)",
        r"your diagnosis is",
    ]
)

# --- Response-side: population guidance converted into a personal prescription ---
#
# New in Phase 2.6 (architecture.md §9.1). The brief adds a rule Phase 1 had no check for:
# *population-level guidance stays population-level.* Retrieval is what makes this a live
# risk rather than a theoretical one -- the corpus is full of sentences like "adults should
# limit free sugars to less than 10% of total energy intake". Restating that is **correct**.
# The violation is converting it: "so you should keep your sugar under 50 g."
#
# **The discriminator is second person, not the quantity.** A rule keyed on
# "prescriptive cue + number" would block the corpus's own phrasing, because population
# guidance is itself written prescriptively ("adults should consume no more than 10%").
# So every pattern below requires an explicit second-person marker, which is the one thing
# the document never says and a personalised restatement always does.
_SECOND_PERSON_PRESCRIPTIVE = (
    r"(you should|you need to|you must|you ought to|you'?re advised to|"
    r"your (daily |weekly |own )?(target|goal|limit|allowance|budget|intake|requirement)|"
    r"(for|in) your (case|situation|diet|body)|for you personally|"
    r"(keep|limit|cut|reduce) your [a-z\s]{0,20}(to|under|below)|"
    r"you can (safely )?(have|eat|consume)|i recommend (that )?you)"
)

# Deliberately **intake** units only. Times, temperatures and durations are excluded
# because second-person food-safety instruction is normal and not what §9.1 prohibits:
# "refrigerate leftovers within two hours" is the guidance, stated the way the guidance
# states it. Including `hours` here would have turned most of the FSANZ document into a
# refusal -- over_refusal, on the half of the corpus that exists to answer storage
# questions.
_INTAKE_QUANTITY = (
    r"\d+(?:[.,]\d+)?\s*"
    r"(%|per ?cent|g\b|grams?|mg\b|milligrams?|mcg\b|micrograms?|kcal|calories|"
    r"portions?|servings?|teaspoons?|tsp\b|tablespoons?|tbsp\b|cups?)"
)

_PERSONALISATION_RESPONSE_PATTERNS = _compile_all(
    [
        rf"{_SECOND_PERSON_PRESCRIPTIVE}.{{0,80}}{_INTAKE_QUANTITY}",
        rf"{_INTAKE_QUANTITY}.{{0,80}}{_SECOND_PERSON_PRESCRIPTIVE}",
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

# Personalisation goes **last** on purpose. "You should eat 1800 calories a day" matches
# both rule sets, and `calorie_target` is the more specific, more serious finding -- it is
# also the category Phase 1's eval history is recorded against, so the label should not
# change under it.
_GROUNDED_RESPONSE_RULES: list[tuple[ScopeCategory, list[re.Pattern]]] = [
    *_RESPONSE_RULES,
    ("personalised_guidance", _PERSONALISATION_RESPONSE_PATTERNS),
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
    """Post-model check for the Phase 1 answer path: three categories, unchanged.

    A pre-check on the question cannot catch a model that volunteers, e.g., a
    calorie number unprompted in an otherwise in-scope answer -- this closes that gap.

    **Deliberately does not include the personalisation rule set.** This function is now
    used only by `eval/run_regression.py` and `eval/run_failure_log.py`, which are the
    recorded baseline that Phase 2.8 reads its outcome-type flips against. Adding a fourth
    category here would change what that baseline blocks, and the comparison 2.8 exists to
    make would be against a moved target. The grounded path uses
    `check_document_answer()` below.
    """
    reason = _first_match(answer.answer, _RESPONSE_RULES)
    return ScopeVerdict(blocked=reason is not None, reason=reason)


def check_document_answer(answer: str) -> ScopeVerdict:
    """Post-model check for one per-document answer: the three categories **plus**
    personalisation (architecture.md §9.1).

    Runs on each `DocumentAnswer.answer` *before* anything is persisted. A trip discards
    the whole response -- every document's answer, not just the offending one -- and
    returns a policy refusal. That is Phase 1's post-check behaviour and the reason is
    unchanged: a blocked answer must never be a bypass path, and shipping the other
    documents' answers from a turn that produced a prohibited one would leak the context
    that made it prohibited.
    """
    reason = _first_match(answer, _GROUNDED_RESPONSE_RULES)
    return ScopeVerdict(blocked=reason is not None, reason=reason)
