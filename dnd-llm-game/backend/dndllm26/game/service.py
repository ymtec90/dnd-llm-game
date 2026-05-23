import json
import re
from typing import Any

from sqlmodel import Session, select

from dndllm26.db.models import (
    Campaign,
    CampaignLore,
    Character,
    DiceRoll,
    GameEvent,
    Hero,
    LoreDocument,
    PendingRoll,
    Turn,
    WorldState,
    now_utc,
)
from dndllm26.game.dice import normalize_formula, outcome_for, roll_formula
from dndllm26.llm.ollama_client import ollama_service
from dndllm26.rag.store import rag_store

MAX_DM_CHARS = 1000
MAX_DM_WORDS = 200


def create_campaign(session: Session, title: str, setting: str, tone: str) -> Campaign:
    campaign = Campaign(title=title, setting=setting, tone=tone)
    session.add(campaign)
    session.commit()
    session.refresh(campaign)
    intro = Turn(
        campaign_id=campaign.id,
        speaker="System",
        content=f"Campaign created. Setting: {setting} Tone: {tone}",
    )
    session.add(intro)
    state = WorldState(
        campaign_id=campaign.id,
        current_location="Campaign opening",
        active_objective="Establish the first scene.",
        scene_summary=f"{setting} Tone: {tone}",
        choices_json=json.dumps(
            [
                "Look for work or rumors.",
                "Find a safe place to rest.",
                "Study the local trouble.",
            ]
        ),
    )
    session.add(state)
    add_event(
        session,
        campaign.id,
        "campaign_created",
        {"title": title, "setting": setting, "tone": tone},
        commit=False,
    )
    session.commit()
    return campaign


def set_campaign_lore(session: Session, campaign_id: int, lore_document_ids: list[int]) -> None:
    existing = session.exec(
        select(CampaignLore).where(CampaignLore.campaign_id == campaign_id)
    ).all()
    for row in existing:
        session.delete(row)
    seen: set[int] = set()
    for document_id in lore_document_ids:
        if document_id in seen or not session.get(LoreDocument, document_id):
            continue
        seen.add(document_id)
        session.add(CampaignLore(campaign_id=campaign_id, lore_document_id=document_id))
    session.commit()


def get_campaign_lore_ids(session: Session, campaign_id: int) -> list[int]:
    rows = session.exec(
        select(CampaignLore).where(CampaignLore.campaign_id == campaign_id)
    ).all()
    return [row.lore_document_id for row in rows]


def seed_default_heroes(session: Session) -> None:
    if session.exec(select(Hero)).first():
        return
    hero = Hero(
        name="Mira Voss",
        ancestry="Human",
        character_class="Rogue",
        backstory="A careful scout who knows the city roofs and owes a debt to a vanished archivist.",
        inventory_json=json.dumps(["shortbow", "lockpicks", "hooded lantern"]),
        is_human=True,
    )
    session.add(hero)
    session.commit()


def clone_hero_to_campaign(session: Session, campaign_id: int, hero: Hero) -> Character:
    try:
        inventory = json.loads(hero.inventory_json)
    except json.JSONDecodeError:
        inventory = []
    return add_character(
        session,
        campaign_id,
        hero.name,
        hero.ancestry,
        hero.character_class,
        hero.backstory,
        inventory if isinstance(inventory, list) else [],
        hero.is_human,
    )


def trim_dm_text(text: str) -> str:
    clean = "\n".join(line.strip() for line in text.replace("\r\n", "\n").splitlines())
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    clean = strip_dm_meta_output(clean)
    words = clean.split()
    if len(words) > MAX_DM_WORDS:
        clean = " ".join(words[:MAX_DM_WORDS]).rstrip()
    if len(clean) > MAX_DM_CHARS:
        clean = clean[:MAX_DM_CHARS].rsplit(" ", 1)[0].rstrip()
    return clean


def strip_dm_meta_output(text: str) -> str:
    clean = text.strip()
    quoted = re.search(
        r"""(?:here(?:'s| is)|this is|a possible|possible first|first dm message).*?["“](?P<body>.+?)["”]""",
        clean,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if quoted and len(quoted.group("body").split()) > 20:
        clean = quoted.group("body").strip()
    clean = re.sub(
        r"^\s*(?:here(?:'s| is)|this is|a possible|possible)\b[^:\n]*:\s*",
        "",
        clean,
        flags=re.IGNORECASE,
    )
    clean = re.split(
        r"\n?\s*this message establishes\b|\n?\s*the message establishes\b",
        clean,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    clean = re.split(
        r"\n?\s*(?:it establishes|this establishes) the following\b",
        clean,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    return clean.strip()


async def generate_campaign_intro(session: Session, campaign_id: int) -> str:
    campaign = session.get(Campaign, campaign_id)
    characters = get_characters(session, campaign_id)
    party = "\n".join(
        f"- {char.name}, {char.ancestry} {char.character_class}: {char.backstory}"
        for char in characters
    )
    lore = await rag_store.search(
        f"{campaign.title if campaign else campaign_id}\n{campaign.setting if campaign else ''}",
        limit=4,
        document_ids=get_campaign_lore_ids(session, campaign_id),
    )
    lore_text = "\n".join(
        f"[{item['filename']}#{item['chunk_index']}] {item['text']}" for item in lore
    )
    system = (
        "You are the main Dungeon Master for a local D&D web game. Create the opening "
        "scene from the campaign brief. Output only the in-world DM message that the "
        "player sees. Never say 'here is', 'possible', 'this message establishes', or "
        "explain your writing. Be concrete, playable, and concise. Do not use markdown "
        "headings. Do not invent dice results. End with exactly one Choices: section "
        "containing 2-4 numbered player actions."
    )
    user = f"""
Campaign title: {campaign.title if campaign else campaign_id}
Setting: {campaign.setting if campaign else ""}
Tone: {campaign.tone if campaign else ""}

Selected heroes:
{party or "No selected heroes."}

Relevant indexed lore:
{lore_text or "No retrieved lore."}

Write the first DM message for the campaign. It must establish where the heroes are,
what immediate tension is visible, and what they can do next.
Do not describe the message, analyze it, or wrap it in quotes. Start directly with the scene.
Hard limits: under {MAX_DM_CHARS} characters and under {MAX_DM_WORDS} words total.
""".strip()
    intro = await ollama_service.chat_text(
        system,
        user,
        model=ollama_service.chat_model,
        temperature=0.8,
        num_predict=260,
    )
    return trim_dm_text(intro)


def list_campaigns(session: Session) -> list[Campaign]:
    return list(session.exec(select(Campaign).order_by(Campaign.updated_at.desc())).all())


def add_character(
    session: Session,
    campaign_id: int,
    name: str,
    ancestry: str,
    character_class: str,
    backstory: str,
    inventory: list[str],
    is_human: bool,
) -> Character:
    character = Character(
        campaign_id=campaign_id,
        name=name,
        ancestry=ancestry,
        character_class=character_class,
        backstory=backstory,
        inventory_json=json.dumps(inventory),
        is_human=is_human,
    )
    session.add(character)
    add_event(
        session,
        campaign_id,
        "character_added",
        {
            "name": name,
            "ancestry": ancestry,
            "class": character_class,
            "is_human": is_human,
        },
        commit=False,
    )
    session.commit()
    session.refresh(character)
    return character


def add_turn(session: Session, campaign_id: int, speaker: str, content: str) -> Turn:
    turn = Turn(campaign_id=campaign_id, speaker=speaker, content=content)
    session.add(turn)
    campaign = session.get(Campaign, campaign_id)
    if campaign:
        campaign.updated_at = now_utc()
        session.add(campaign)
    session.commit()
    session.refresh(turn)
    return turn


def add_event(
    session: Session,
    campaign_id: int,
    event_type: str,
    payload: dict[str, Any],
    *,
    commit: bool = True,
) -> GameEvent:
    event = GameEvent(
        campaign_id=campaign_id,
        event_type=event_type,
        payload_json=json.dumps(payload),
    )
    session.add(event)
    if commit:
        session.commit()
        session.refresh(event)
    return event


def get_turns(session: Session, campaign_id: int) -> list[Turn]:
    return list(
        session.exec(
            select(Turn).where(Turn.campaign_id == campaign_id).order_by(Turn.created_at)
        ).all()
    )


def get_characters(session: Session, campaign_id: int) -> list[Character]:
    return list(session.exec(select(Character).where(Character.campaign_id == campaign_id)).all())


def available_lore_documents(session: Session, campaign_id: int | None = None) -> str:
    statement = select(LoreDocument).where(LoreDocument.status == "ready")
    if campaign_id is not None:
        lore_ids = get_campaign_lore_ids(session, campaign_id)
        if not lore_ids:
            return ""
        statement = statement.where(LoreDocument.id.in_(lore_ids))
    docs = session.exec(statement.order_by(LoreDocument.filename)).all()
    names = [doc.filename for doc in docs if doc.chunks > 0]
    return ", ".join(names[:6])


def get_world_state(session: Session, campaign_id: int) -> WorldState:
    state = session.exec(
        select(WorldState).where(WorldState.campaign_id == campaign_id)
    ).first()
    if state:
        return state
    state = WorldState(campaign_id=campaign_id)
    session.add(state)
    session.commit()
    session.refresh(state)
    return state


def choices_from_state(state: WorldState) -> list[str]:
    try:
        value = json.loads(state.choices_json)
        if isinstance(value, list):
            return clean_choice_list(value)
    except json.JSONDecodeError:
        return []
    return []


def clean_choice_value(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("action", "choice", "text", "label", "description"):
            if key in value:
                return clean_choice_value(value[key])
        return ""
    text = str(value or "").strip()
    object_match = re.search(
        r"""["']?(?:action|choice|text|label)["']?\s*:\s*["'](?P<value>.+?)["']\s*[},]?$""",
        text,
        flags=re.IGNORECASE,
    )
    if object_match:
        text = object_match.group("value")
    text = re.sub(r"^\s*(?:\d+[\).\:]|-|\*)\s+", "", text).strip()
    text = re.sub(r"^\{+|\}+$", "", text).strip()
    text = re.sub(r"^['\"]+|['\"]+$", "", text).strip()
    text = re.sub(r"\s+", " ", text)
    return text


def clean_choice_list(value: Any) -> list[str]:
    if isinstance(value, dict):
        value = value.get("choices") or value.get("actions") or value.get("options") or []
    if not isinstance(value, list):
        return []
    choices = [clean_choice_value(item) for item in value]
    return [choice for choice in choices if choice and not is_placeholder_choice(choice)]


def get_pending_roll(session: Session, campaign_id: int) -> PendingRoll | None:
    return session.exec(
        select(PendingRoll)
        .where(PendingRoll.campaign_id == campaign_id)
        .where(PendingRoll.status == "pending")
        .order_by(PendingRoll.created_at.desc())
    ).first()


async def build_dm_prompt(session: Session, campaign_id: int, action: str) -> str:
    campaign = session.get(Campaign, campaign_id)
    turns = get_turns(session, campaign_id)[-10:]
    characters = get_characters(session, campaign_id)
    recent = "\n".join(f"{turn.speaker}: {turn.content}" for turn in turns)
    party = "\n".join(
        f"- {char.name}, {char.ancestry} {char.character_class}: {char.backstory}"
        for char in characters
    )
    lore = await rag_store.search(
        action + "\n" + recent,
        limit=4,
        document_ids=get_campaign_lore_ids(session, campaign_id),
    )
    lore_text = "\n".join(
        f"[{item['filename']}#{item['chunk_index']}] {item['text']}" for item in lore
    )
    return f"""
Campaign: {campaign.title if campaign else campaign_id}
Setting: {campaign.setting if campaign else ""}
Tone: {campaign.tone if campaign else ""}

Party:
{party or "No party members have been added yet."}

Recent turns:
{recent}

Relevant local lore:
{lore_text or "No retrieved lore."}

Selected campaign references:
{available_lore_documents(session, campaign_id) or "No campaign lore selected. Use D&D 5e-style rulings."}

Player action:
{action}

Continue the scene. Include any needed ability check or consequence.
Write 120-180 words and stay under 1000 characters. Avoid markdown headings, bold markers, and separators.
Output only the DM narration and Choices section. Do not explain what your response does.
End with a Choices section containing 2-4 numbered options that are specific to the
current scene, named NPCs, locations, threats, or clues. Each choice must be a
playable action written in one sentence. Do not copy instructions or use generic
placeholder wording.
""".strip()


def _context_block(session: Session, campaign_id: int) -> str:
    campaign = session.get(Campaign, campaign_id)
    state = get_world_state(session, campaign_id)
    turns = get_turns(session, campaign_id)[-12:]
    characters = get_characters(session, campaign_id)
    party = "\n".join(
        f"- {char.name}: {char.ancestry} {char.character_class}. {char.backstory}"
        for char in characters
    )
    recent = "\n".join(f"{turn.speaker}: {turn.content}" for turn in turns)
    return f"""
Campaign: {campaign.title if campaign else campaign_id}
Setting: {campaign.setting if campaign else ""}
Tone: {campaign.tone if campaign else ""}
Current location: {state.current_location}
Objective: {state.active_objective}
Scene summary: {state.scene_summary}
Selected references: {available_lore_documents(session, campaign_id) or "None"}

Party:
{party or "No party members."}

Recent turns:
{recent or "No turns yet."}
""".strip()


def extract_choices(text: str) -> list[str]:
    cleaned = text.replace("\r\n", "\n")
    choices_section = re.search(
        r"(?:^|\n)\s*(?:choices|what do you do)[\?:]?\s*\n(?P<body>.*)$",
        cleaned,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if choices_section:
        cleaned = choices_section.group("body")
    patterns = [
        r"^\s*(?:\d+[\).\:]|-|\*)\s+(?:\*\*)?(.*?)(?:\*\*)?\s*$",
        r"^\s*(?:Option\s+\d+[\:\).-])\s+(.*?)\s*$",
    ]
    choices: list[str] = []
    for line in cleaned.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        for pattern in patterns:
            match = re.match(pattern, stripped, flags=re.IGNORECASE)
            if match:
                choice = clean_choice_value(match.group(1))
                choice = re.sub(r"^\*\*|\*\*$", "", choice).strip()
                choice = re.sub(r"^\*+|\*+$", "", choice).strip()
                choice = re.sub(r"\*\*", "", choice).strip()
                choice = re.sub(r"^\s*[-–—]\s*", "", choice).strip()
                if 4 <= len(choice) <= 220 and not is_placeholder_choice(choice):
                    choices.append(choice)
                break
    deduped: list[str] = []
    for choice in choices:
        normalized = choice.lower()
        if normalized not in {item.lower() for item in deduped}:
            deduped.append(choice)
    return deduped[:4]


def is_placeholder_choice(choice: str) -> bool:
    normalized = choice.lower()
    blocked = [
        "a concise action",
        "another concise action",
        "specific playable action",
        "the player can take",
        "option ",
    ]
    return any(text in normalized for text in blocked)


def is_placeholder_text(value: str) -> bool:
    blocked = [
        "short current location",
        "current immediate objective",
        "one sentence scene summary",
        "scene summary",
        "current location",
    ]
    normalized = value.lower()
    return any(item in normalized for item in blocked)


def clean_extracted_text(value: Any, fallback: str, limit: int, default: str) -> str:
    text = str(value or "").strip()
    if not text or is_placeholder_text(text):
        text = fallback
    if not text or is_placeholder_text(text):
        text = default
    return text[:limit]


async def update_world_from_dm_response(
    session: Session,
    campaign_id: int,
    dm_text: str,
) -> WorldState:
    state = get_world_state(session, campaign_id)
    choices = extract_choices(dm_text)
    fallback_choices = choices or choices_from_state(state) or [
        "Ask a follow-up question.",
        "Inspect the area.",
        "Move carefully onward.",
    ]
    fallback = {
        "location": state.current_location,
        "objective": state.active_objective,
        "summary": state.scene_summary,
        "choices": fallback_choices,
    }
    extracted = await ollama_service.chat_json(
        (
            "You are the utility model for a D&D web app. The main DM model writes "
            "narration; your job is to convert it into compact UI state and dynamic "
            "player actions. Return only valid JSON and do not continue the story."
        ),
        f"""
DM narration:
{dm_text}

Return JSON:
location: concrete current place, 2-6 words
objective: immediate active goal, 4-14 words
summary: one sentence summary of the current scene
choices: array of 2-4 strings, not objects. Each string is one concise,
scene-specific player action under 90 characters

Choice rules:
- Use concrete nouns from the scene when possible.
- Include a mix of investigation, social, travel, or risk-taking options when relevant.
- Do not ask for dice rolls in the choice text; the rules referee decides rolls separately.
- Do not invent outcomes, rewards, or success.
- Do not return objects such as {{"action": "..."}}, only plain strings.

Do not use labels or placeholders such as "short current location",
"current immediate objective", "one sentence scene summary", or "specific playable action".
""".strip(),
        fallback,
    )
    utility_choices = clean_choice_list(extracted.get("choices", []))
    choices = utility_choices or choices
    choices = [choice[:140] for choice in choices][:4]
    state.current_location = clean_extracted_text(
        extracted.get("location"),
        state.current_location,
        120,
        "Unknown location",
    )
    state.active_objective = clean_extracted_text(
        extracted.get("objective"),
        state.active_objective,
        180,
        "Choose the next move.",
    )
    fallback_summary = " ".join(format_summary_source(dm_text).split()[:42])
    summary = extracted.get("summary") or fallback_summary or state.scene_summary
    state.scene_summary = clean_extracted_text(
        summary,
        fallback_summary or state.scene_summary,
        260,
        "The scene is unfolding.",
    )

    if choices:
        state.choices_json = json.dumps(choices)
    state.updated_at = now_utc()
    session.add(state)
    add_event(
        session,
        campaign_id,
        "choices_updated",
        {
            "choices": choices_from_state(state),
            "location": state.current_location,
            "objective": state.active_objective,
            "summary": state.scene_summary,
        },
        commit=False,
    )
    session.commit()
    session.refresh(state)
    return state


def format_summary_source(text: str) -> str:
    return re.sub(r"(\*\*|#{1,6}\s|---+)", "", text).strip()


def _fallback_roll_decision(action: str) -> dict[str, Any]:
    lower = action.lower()
    checks = [
        (("sneak", "hide", "stealth"), "Dexterity", "Stealth", 13),
        (("persuade", "convince", "lie", "deceive", "rumor"), "Charisma", "Persuasion", 12),
        (("search", "inspect", "investigate", "study"), "Intelligence", "Investigation", 12),
        (("listen", "notice", "watch", "spot"), "Wisdom", "Perception", 12),
        (("climb", "force", "break", "lift"), "Strength", "Athletics", 13),
        (("attack", "strike", "shoot", "stab"), "Strength or Dexterity", "Attack", 12),
    ]
    for words, ability, skill, dc in checks:
        if any(word in lower for word in words):
            return {
                "requires_roll": True,
                "narration": f"{action[:120]}",
                "formula": "1d20+2",
                "ability": ability,
                "skill": skill,
                "dc": dc,
                "reason": concise_roll_reason(action, skill),
            }
    return {
        "requires_roll": False,
        "narration": "",
        "formula": "1d20",
        "ability": "Ability",
        "skill": None,
        "dc": 10,
        "reason": "",
    }


async def decide_roll(session: Session, campaign_id: int, action: str) -> dict[str, Any]:
    fallback = _fallback_roll_decision(action)
    system = (
        "You are the utility rules referee for a D&D web app. The main DM model "
        "narrates; your job is to decide if the player's action needs a dice check. "
        "Return only JSON."
    )
    user = f"""
{_context_block(session, campaign_id)}

Player action:
{action}

Return JSON with:
requires_roll: boolean
narration: short optional setup text before a roll
formula: dice formula, usually 1d20 plus a small modifier like 1d20+2
ability: ability name
skill: skill or null
dc: integer 5-25
reason: why the roll is required

Require a roll only when failure would create an interesting consequence.
Do not require a roll for simple navigation, ordinary conversation, or safe actions.
Use the current character context when choosing the most likely ability and skill.
The reason and narration must be about this one action only. Do not summarize player choices,
campaign options, or previous menu text.
""".strip()
    decision = await ollama_service.chat_json(system, user, fallback)
    merged = {**fallback, **decision}
    try:
        merged["formula"] = normalize_formula(str(merged.get("formula") or "1d20"))
    except ValueError:
        merged["formula"] = fallback["formula"]
    try:
        merged["dc"] = max(5, min(25, int(merged.get("dc") or 10)))
    except (TypeError, ValueError):
        merged["dc"] = fallback["dc"]
    merged["requires_roll"] = bool(merged.get("requires_roll"))
    merged["reason"] = clean_roll_text(
        merged.get("reason"),
        concise_roll_reason(action, str(merged.get("skill") or merged.get("ability") or "check")),
    )
    merged["narration"] = clean_roll_text(merged.get("narration"), action[:140])
    return merged


def concise_roll_reason(action: str, skill: str | None) -> str:
    label = skill or "check"
    clean_action = clean_choice_value(action).rstrip(".")
    if len(clean_action) > 90:
        clean_action = clean_action[:90].rsplit(" ", 1)[0]
    return f"{label}: {clean_action}"


def clean_roll_text(value: Any, fallback: str) -> str:
    text = clean_choice_value(value).strip()
    bad_fragments = [
        "you have three options",
        "you have 3 options",
        "option 1",
        "option 2",
        "option 3",
        "this message establishes",
    ]
    if not text or any(fragment in text.lower() for fragment in bad_fragments):
        text = fallback
    return text[:180]


def create_pending_roll(
    session: Session,
    campaign_id: int,
    action: str,
    decision: dict[str, Any],
) -> PendingRoll:
    pending = PendingRoll(
        campaign_id=campaign_id,
        action_text=action,
        formula=str(decision["formula"]),
        ability=str(decision.get("ability") or "Ability"),
        skill=decision.get("skill"),
        dc=int(decision["dc"]),
        reason=str(decision.get("reason") or "Resolve the uncertain outcome."),
        narration=str(decision.get("narration") or ""),
    )
    session.add(pending)
    session.commit()
    session.refresh(pending)
    add_event(
        session,
        campaign_id,
        "roll_required",
        {
            "pending_roll_id": pending.id,
            "formula": pending.formula,
            "ability": pending.ability,
            "skill": pending.skill,
            "dc": pending.dc,
            "reason": pending.reason,
            "narration": pending.narration,
        },
    )
    return pending


def perform_pending_roll(session: Session, campaign_id: int, pending_roll_id: int) -> DiceRoll:
    pending = session.get(PendingRoll, pending_roll_id)
    if not pending or pending.campaign_id != campaign_id:
        raise ValueError("Pending roll not found.")
    if pending.status != "pending":
        raise ValueError("Roll has already been resolved.")
    result = roll_formula(pending.formula)
    outcome = outcome_for(result.total, pending.dc)
    roll = DiceRoll(
        campaign_id=campaign_id,
        pending_roll_id=pending.id,
        formula=result.formula,
        rolls_json=json.dumps(result.rolls),
        modifier=result.modifier,
        total=result.total,
        dc=pending.dc,
        outcome=outcome,
        reason=pending.reason,
    )
    pending.status = "resolved"
    pending.resolved_at = now_utc()
    session.add(roll)
    session.add(pending)
    add_event(
        session,
        campaign_id,
        "dice_rolled",
        {
            "pending_roll_id": pending.id,
            "formula": result.formula,
            "rolls": result.rolls,
            "modifier": result.modifier,
            "total": result.total,
            "dc": pending.dc,
            "outcome": outcome,
            "reason": pending.reason,
        },
        commit=False,
    )
    session.commit()
    session.refresh(roll)
    return roll


async def build_roll_resolution_prompt(
    session: Session,
    campaign_id: int,
    pending: PendingRoll,
    roll: DiceRoll,
) -> str:
    lore = await rag_store.search(
        pending.action_text,
        limit=4,
        document_ids=get_campaign_lore_ids(session, campaign_id),
    )
    lore_text = "\n".join(
        f"[{item['filename']}#{item['chunk_index']}] {item['text']}" for item in lore
    )
    return f"""
{_context_block(session, campaign_id)}

Player action:
{pending.action_text}

Required check:
{pending.ability}{f" ({pending.skill})" if pending.skill else ""}, DC {pending.dc}
Reason: {pending.reason}

Dice result:
Formula: {roll.formula}
Rolls: {roll.rolls_json}
Total: {roll.total}
Outcome: {roll.outcome}

Relevant lore:
{lore_text or "No retrieved lore."}

Resolve the action as the Dungeon Master. Reflect the dice result directly.
On success, reward progress. On failure, add a complication without blocking play.
Write 120-180 words and stay under 1000 characters. Avoid markdown headings, bold markers, and separators.
Output only the DM narration and Choices section. Do not explain what your response does.
End with a Choices section containing 2-4 numbered options that are specific to the
current scene, named NPCs, locations, threats, or clues. Each choice must be a
playable action written in one sentence. Do not copy instructions or use generic
placeholder wording.
""".strip()
