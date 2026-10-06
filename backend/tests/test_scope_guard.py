import pytest

from db.schemas import NutritionAnswer
from services.scope_guard import (
    REFUSAL_MESSAGES,
    check_document_answer,
    check_request,
    check_response,
)


# --- check_request: architecture.md §8.1's own example phrasings ---


def test_blocks_calorie_target_question():
    verdict = check_request("How many calories should I eat to lose 10 pounds?")
    assert verdict.blocked is True
    assert verdict.reason == "calorie_target"


def test_blocks_weight_target_question():
    verdict = check_request("What should I weigh at 5'6\"?")
    assert verdict.blocked is True
    assert verdict.reason == "weight_target"


def test_blocks_medical_advice_question():
    verdict = check_request("Can you diagnose my rash?")
    assert verdict.blocked is True
    assert verdict.reason == "medical_advice"


def test_blocks_medication_dosage_question():
    verdict = check_request("I have diabetes, what should my insulin dosage be?")
    assert verdict.blocked is True
    assert verdict.reason == "medical_advice"


# --- check_request: in-scope questions must not be blocked ---


def test_does_not_block_nutrient_requirement_question():
    verdict = check_request("How much protein do I need per day?")
    assert verdict.blocked is False
    assert verdict.reason is None


def test_does_not_block_food_safety_question():
    verdict = check_request("How long can cooked chicken sit out at room temperature?")
    assert verdict.blocked is False


def test_does_not_block_informational_calorie_question():
    # Asking about a food's calorie content is not a personal calorie-target request.
    verdict = check_request("About how many calories are in a medium banana?")
    assert verdict.blocked is False


def test_does_not_block_negated_calorie_question():
    verdict = check_request("I'm not asking for a diet plan, just -- what is a calorie?")
    assert verdict.blocked is False


# --- check_response: the model volunteers a prohibited category unprompted ---


def test_blocks_response_with_unprompted_calorie_target():
    answer = NutritionAnswer(
        answer="You should eat about 1800 calories a day to reach your goals.",
        claims=[],
    )
    verdict = check_response(answer)
    assert verdict.blocked is True
    assert verdict.reason == "calorie_target"


def test_blocks_response_with_unprompted_weight_target():
    answer = NutritionAnswer(answer="Your ideal weight should be around 140 lbs.", claims=[])
    verdict = check_response(answer)
    assert verdict.blocked is True
    assert verdict.reason == "weight_target"


def test_blocks_response_with_unprompted_medical_advice():
    answer = NutritionAnswer(
        answer="You should take 10mg of the medication twice daily.", claims=[]
    )
    verdict = check_response(answer)
    assert verdict.blocked is True
    assert verdict.reason == "medical_advice"


def test_does_not_block_response_with_informational_calorie_fact():
    # A bare nutrition fact, not a personalized prescription -- must not trip the guard.
    answer = NutritionAnswer(
        answer="A medium banana has about 100 calories and is a good source of potassium.",
        claims=[{"claim": "A medium banana has about 100 calories.", "source": None}],
    )
    verdict = check_response(answer)
    assert verdict.blocked is False


def test_does_not_block_ordinary_in_scope_response():
    answer = NutritionAnswer(
        answer="Cooked chicken should not sit out for more than two hours at room temperature.",
        claims=[],
    )
    verdict = check_response(answer)
    assert verdict.blocked is False


# --- check_document_answer: personalisation (Phase 2.6, architecture.md §9.1) ---
#
# Retrieval is what makes this a live risk. The corpus is full of population-level
# quantities; restating one is the correct behaviour, and converting it into an instruction
# for the person asking is the violation. So these tests come in pairs, and the
# *non*-blocking half is the one that would do real damage if it regressed -- it would
# refuse the corpus's own phrasing and show up as `over_refusal`.


@pytest.mark.parametrize(
    "answer",
    [
        "You should keep your free sugars under 50 g a day.",
        "In your case, aim for about 25 g of fibre.",
        "Your daily target is 2300 mg of sodium.",
        "For your diet, that works out to 5 portions a day.",
        "You can safely have 30 g of free sugars.",
        "Limit your sodium to 1500 mg.",
    ],
)
def test_blocks_population_guidance_converted_into_a_personal_prescription(answer):
    verdict = check_document_answer(answer)
    assert verdict.blocked is True
    assert verdict.reason in ("personalised_guidance", "calorie_target")


@pytest.mark.parametrize(
    "answer",
    [
        # The corpus's own phrasing. Population guidance is written prescriptively, which
        # is exactly why the rule keys on second person rather than on "should + number".
        "Adults should limit free sugars to less than 10% of total energy intake.",
        "The guidance recommends no more than 5 g of salt per day for adults.",
        "Adults are advised to eat at least 400 g of fruit and vegetables daily.",
        "Children should consume less than 10% of total energy from free sugars.",
        # Second person with no quantity: an instruction, but not a personal target.
        "You should wash your hands before preparing food.",
        # Food-safety instruction in second person, with a time and a temperature. Normal
        # guidance phrasing, and half the corpus exists to answer questions like this.
        "You should refrigerate cooked food within two hours.",
        "You should keep your fridge at or below 5 degrees Celsius.",
        # An informational quantity, not a prescription.
        "A medium banana has about 100 calories.",
    ],
)
def test_does_not_block_population_level_or_food_safety_phrasing(answer):
    assert check_document_answer(answer).blocked is False


def test_calorie_target_wins_over_personalisation_for_the_same_sentence():
    """Both rule sets match "you should eat 1800 calories". `calorie_target` is the more
    specific and more serious finding, and it is the label Phase 1's eval history is
    recorded against -- so it must not silently change under it."""
    verdict = check_document_answer("You should eat about 1800 calories a day.")
    assert verdict.reason == "calorie_target"


def test_phase_one_check_response_does_not_gain_the_personalisation_rule():
    """Deliberate. `check_response` is used only by the eval harnesses, which are the
    recorded baseline Phase 2.8 reads its outcome-type flips against. Widening what it
    blocks would make that comparison a moving target."""
    personalised = "You should keep your free sugars under 50 g a day."

    assert check_document_answer(personalised).blocked is True
    assert check_response(NutritionAnswer(answer=personalised, claims=[])).blocked is False


# --- The medical rule: assertion vs hypothetical (both defects found in Phase 2.8) ---


@pytest.mark.parametrize(
    "answer",
    [
        "You have a thyroid condition that explains this.",
        "Based on that, you have diabetes.",
        "You have cancer.",
        "You have an autoimmune disorder.",
        "You are diagnosed with celiac disease.",
        "Your diagnosis is iron-deficiency anaemia.",
    ],
)
def test_asserting_a_condition_is_blocked(answer):
    """"you have diabetes" -- the bare form -- was **missed** until Phase 2.8, because the
    pattern required filler between "have" and the condition. The wordier
    "you have a thyroid condition" was caught, so the hole was invisible in the obvious
    test case. This is a `missed_scope_restriction`, the more serious direction."""
    verdict = check_document_answer(answer)
    assert verdict.blocked is True
    assert verdict.reason == "medical_advice"


@pytest.mark.parametrize(
    "answer",
    [
        # The Dietary Guidelines' own words, verbatim. One of 159 second-person sentences
        # in the corpus, and the only one that tripped this guard before 2.8 -- a referral
        # to a clinician classified as giving medical advice, which is the opposite of what
        # it is. Faithfully quoting it would have been `over_refusal`.
        "If you have a chronic disease, talk with your health care professional to see if "
        "you need to adapt the Dietary Guidelines to meet your specific needs.",
        "When you have a medical condition, a registered dietitian can advise you.",
        "Whether you have a condition like diabetes is for a clinician to determine.",
        "Should you have a chronic disease, speak to your physician.",
        # Third-person description of a condition is information, not diagnosis.
        "Diabetes is a condition affecting blood sugar regulation.",
        "People with coeliac disease must avoid gluten.",
    ],
)
def test_hypothetical_or_third_person_mention_of_a_condition_is_not_blocked(answer):
    assert check_document_answer(answer).blocked is False


# --- Refusal messages are static template text, never model output ---


def test_every_category_has_a_static_refusal_message():
    for category in (
        "calorie_target",
        "weight_target",
        "medical_advice",
        "personalised_guidance",
    ):
        assert category in REFUSAL_MESSAGES
        assert isinstance(REFUSAL_MESSAGES[category], str)
        assert len(REFUSAL_MESSAGES[category]) > 0
