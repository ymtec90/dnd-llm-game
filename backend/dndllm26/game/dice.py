import random
import re
from dataclasses import dataclass


ROLL_RE = re.compile(r"^\s*(?:(\d{1,2})d)?(\d{1,4})([+-]\d{1,3})?\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class RollResult:
    formula: str
    rolls: list[int]
    modifier: int
    total: int


def normalize_formula(formula: str) -> str:
    match = ROLL_RE.match(formula)
    if not match:
        raise ValueError("Use dice notation like d20, 1d20+3, or 2d6.")
    count = int(match.group(1) or "1")
    sides = int(match.group(2))
    modifier = int(match.group(3) or "0")
    if count < 1 or count > 20:
        raise ValueError("Roll count must be between 1 and 20.")
    if sides < 2 or sides > 1000:
        raise ValueError("Dice sides must be between 2 and 1000.")
    mod_text = f"{modifier:+d}" if modifier else ""
    return f"{count}d{sides}{mod_text}"


def roll_formula(formula: str) -> RollResult:
    normalized = normalize_formula(formula)
    match = ROLL_RE.match(normalized)
    if not match:
        raise ValueError("Invalid dice formula.")
    count = int(match.group(1) or "1")
    sides = int(match.group(2))
    modifier = int(match.group(3) or "0")
    rolls = [random.randint(1, sides) for _ in range(count)]
    return RollResult(
        formula=normalized,
        rolls=rolls,
        modifier=modifier,
        total=sum(rolls) + modifier,
    )


def outcome_for(total: int, dc: int | None) -> str:
    if dc is None:
        return "rolled"
    return "success" if total >= dc else "failure"

