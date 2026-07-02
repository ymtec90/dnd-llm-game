import pytest
from dndllm26.game.dice import normalize_formula, roll_formula, outcome_for

def test_normalize_formula():
    assert normalize_formula("1d20") == "1d20"
    assert normalize_formula("20") == "1d20"
    assert normalize_formula("1d20+3") == "1d20+3"
    assert normalize_formula("2d6-1") == "2d6-1"
    
    with pytest.raises(ValueError):
        normalize_formula("invalid")
    with pytest.raises(ValueError):
        normalize_formula("21d20")  # count > 20
    with pytest.raises(ValueError):
        normalize_formula("1d1001")  # sides > 1000

def test_roll_formula():
    res = roll_formula("2d6+3")
    assert res.formula == "2d6+3"
    assert len(res.rolls) == 2
    assert all(1 <= r <= 6 for r in res.rolls)
    assert res.modifier == 3
    assert res.total == sum(res.rolls) + 3

def test_outcome_for():
    assert outcome_for(15, 15) == "success"
    assert outcome_for(16, 15) == "success"
    assert outcome_for(14, 15) == "failure"
    assert outcome_for(10, None) == "rolled"
