from db.schemas import NutritionAnswer
from services.scope_guard import REFUSAL_MESSAGES, check_request, check_response


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


# --- Refusal messages are static template text, never model output ---


def test_every_category_has_a_static_refusal_message():
    for category in ("calorie_target", "weight_target", "medical_advice"):
        assert category in REFUSAL_MESSAGES
        assert isinstance(REFUSAL_MESSAGES[category], str)
        assert len(REFUSAL_MESSAGES[category]) > 0
