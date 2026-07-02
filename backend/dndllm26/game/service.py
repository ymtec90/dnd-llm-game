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
    GameSession,
    CharacterStatus,
    CampaignMessage,
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
        content=f"Campanha criada. Cenário: {setting} Tom: {tone}",
    )
    session.add(intro)
    state = WorldState(
        campaign_id=campaign.id,
        current_location="Abertura da campanha",
        active_objective="Estabelecer a primeira cena.",
        scene_summary=f"{setting} Tom: {tone}",
        choices_json=json.dumps(
            [
                "Procurar trabalho ou boatos.",
                "Encontrar um lugar seguro para descansar.",
                "Estudar os problemas locais.",
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


async def generate_ai_companions(session: Session, campaign_id: int, count: int, classes: list[str]) -> list[Character]:
    companions = []
    import random
    ancestries_pool = ["Anão", "Elfo", "Humano", "Halfling", "Meio-Elfo", "Meio-Orc"]
    names_pool = ["Tharivol", "Keth", "Valerie", "Grom", "Bryn", "Lia", "Kaelen", "Dorn", "Sariel", "Tordek"]
    backstory_pool = [
        "Um guerreiro experiente que busca glória e ouro nas masmorras.",
        "Um andarilho misterioso com um passado sombrio e segredos guardados.",
        "Um erudito devoto à busca de relíquias arcanas perdidas.",
        "Um sobrevivente cínico que faz qualquer coisa por algumas moedas.",
    ]
    
    for i in range(count):
        char_class = classes[i] if i < len(classes) else random.choice(["Guerreiro", "Mago", "Clérigo", "Ladino"])
        
        system = "Você é um gerador de personagens de D&D 5e em português. Retorne apenas JSON."
        user = f"Gere um companheiro de aventura de classe {char_class}. Retorne JSON com: name (nome curto de fantasia), ancestry (raça), backstory (antecedentes e motivação de 1 frase em português)."
        fallback = {
            "name": f"{random.choice(names_pool)}",
            "ancestry": random.choice(ancestries_pool),
            "backstory": random.choice(backstory_pool)
        }
        
        try:
            generated = await ollama_service.chat_json(system, user, fallback)
        except Exception:
            generated = fallback
            
        name = str(generated.get("name") or fallback["name"])
        ancestry = str(generated.get("ancestry") or fallback["ancestry"])
        backstory = str(generated.get("backstory") or fallback["backstory"])
        
        char = add_character(
            session=session,
            campaign_id=campaign_id,
            name=name,
            ancestry=ancestry,
            character_class=char_class,
            backstory=backstory,
            inventory=["Rações", "Tocha", "Cantil"],
            is_human=False,
        )
        companions.append(char)
    return companions


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
    
    # Separate player characters from NPC/AI characters
    human_characters = [c for c in characters if c.is_human]
    ai_characters = [c for c in characters if not c.is_human]
    
    party = []
    if human_characters:
        party.append("Jogador Humano:")
        for char in human_characters:
            party.append(f"- {char.name}, {char.ancestry} {char.character_class}: {char.backstory}")
    if ai_characters:
        party.append("Companheiros de Grupo controlados por você (IA):")
        for char in ai_characters:
            party.append(f"- {char.name}, {char.ancestry} {char.character_class}: {char.backstory}")
    party_text = "\n".join(party)

    lore = await rag_store.search(
        f"{campaign.title if campaign else campaign_id}\n{campaign.setting if campaign else ''}",
        limit=4,
        document_ids=get_campaign_lore_ids(session, campaign_id),
    )
    lore_text = "\n".join(
        f"[{item['filename']}#{item['chunk_index']}] {item['text']}" for item in lore
    )
    system = (
        "Você é o Mestre (DM) de um jogo de D&D. Crie a cena de abertura com base no briefing. "
        "Escreva sua narração e diálogos exclusivamente em português do Brasil. "
        "Você deve gerar apenas a mensagem do Mestre em primeira pessoa que o jogador verá. "
        "Nunca diga 'aqui está', 'possível', 'esta mensagem estabelece' ou explique sua escrita. "
        "Seja concreto, jogável e conciso. Não use cabeçalhos markdown. Não invente resultados de dados. "
        "Se houver companheiros de grupo da IA listados, você controla as ações e falas deles também. "
        "Separe claramente a narração do Mestre das ações/falas desses companheiros, identificando-os explicitamente (ex: [Nome do Companheiro]: 'Fala...'). "
        "Termine com exatamente uma seção Escolhas: contendo de 2 a 4 opções numeradas de ações para o jogador."
    )
    user = f"""
Título da campanha: {campaign.title if campaign else campaign_id}
Cenário: {campaign.setting if campaign else ""}
Tom: {campaign.tone if campaign else ""}

Grupo:
{party_text or "Nenhum herói selecionado."}

Histórico (Lore) relevante:
{lore_text or "Nenhum histórico disponível."}

Escreva a primeira mensagem do Mestre para a campanha. Ela deve estabelecer onde os heróis estão, qual tensão imediata está visível e o que eles podem fazer a seguir.
Não descreva a mensagem, não a analise nem a envolva em aspas. Comece diretamente com a cena.
Limites estritos: menos de {MAX_DM_CHARS} caracteres e menos de {MAX_DM_WORDS} palavras no total.
Escreva tudo exclusivamente em português do Brasil.
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


async def build_dm_prompt(session: Session, campaign_id: int, action: str, game_session_id: int | None = None) -> str:
    campaign = session.get(Campaign, campaign_id)
    if game_session_id:
        msg_turns = session.exec(
            select(CampaignMessage)
            .where(CampaignMessage.game_session_id == game_session_id)
            .order_by(CampaignMessage.created_at)
        ).all()[-10:]
        recent = "\n".join(f"{turn.speaker}: {turn.content}" for turn in msg_turns)
        characters = session.exec(
            select(CharacterStatus)
            .where(CharacterStatus.game_session_id == game_session_id)
        ).all()
    else:
        turns = get_turns(session, campaign_id)[-10:]
        recent = "\n".join(f"{turn.speaker}: {turn.content}" for turn in turns)
        characters = get_characters(session, campaign_id)
    
    # Separate player characters from NPC/AI characters
    human_characters = [c for c in characters if c.is_human]
    ai_characters = [c for c in characters if not c.is_human]
    
    party = []
    if human_characters:
        party.append("Jogador Humano:")
        for char in human_characters:
            party.append(f"- {char.name}, {char.ancestry} {char.character_class}: {char.backstory}")
    if ai_characters:
        party.append("Companheiros de Grupo controlados por você (IA):")
        for char in ai_characters:
            party.append(f"- {char.name}, {char.ancestry} {char.character_class}: {char.backstory}")
    party_text = "\n".join(party)

    lore_pack = None
    tone_override = None
    if game_session_id:
        game_session = session.get(GameSession, game_session_id)
        if game_session and game_session.lore_pack:
            from dndllm26.rag.lore_manager import LORE_PACKS
            lore_pack = game_session.lore_pack
            if lore_pack in LORE_PACKS:
                tone_override = LORE_PACKS[lore_pack]["tone_prompt"]

    if lore_pack:
        lore = await rag_store.search(
            action + "\n" + recent,
            limit=4,
            lore_pack=lore_pack,
        )
    else:
        lore = await rag_store.search(
            action + "\n" + recent,
            limit=4,
            document_ids=get_campaign_lore_ids(session, campaign_id),
        )
        
    lore_text = "\n".join(
        f"[{item['filename']}#{item['chunk_index']}] {item['text']}" for item in lore
    )
    
    tone_str = tone_override or (campaign.tone if campaign else "")
    setting_str = (LORE_PACKS[lore_pack]["name"] if (lore_pack and lore_pack in LORE_PACKS) else (campaign.setting if campaign else ""))

    return f"""
Campanha: {campaign.title if campaign else campaign_id}
Cenário: {setting_str}
Tom: {tone_str}

Grupo:
{party_text or "Nenhum membro do grupo foi adicionado ainda."}

Turnos recentes:
{recent}

Histórico local relevante:
{lore_text or "Nenhum histórico recuperado."}

Referências de campanha selecionadas:
{available_lore_documents(session, campaign_id) or "Nenhuma lore de campanha selecionada. Use regras do estilo D&D 5e."}

Ação do jogador:
{action}

Continue a cena em português do Brasil. Inclua qualquer teste de atributo ou consequência necessária.
Se houver companheiros de grupo da IA listados, você controla as ações e diálogos deles. Separe claramente a narrativa do Mestre das falas e ações desses companheiros (ex: [Nome do Companheiro]: 'Fala...').
Escreva entre 120 e 180 palavras e mantenha-se abaixo de 1000 caracteres. Evite cabeçalhos markdown, marcadores em negrito e separadores.
Retorne apenas a narração do Mestre (e falas/ações dos companheiros) e a seção Escolhas. Não explique o que sua resposta faz.
Escreva tudo exclusivamente em português do Brasil.
Termine com uma seção Escolhas contendo de 2 a 4 opções numeradas que sejam específicas para a cena atual, NPCs nomeados, ameaças ou pistas. Cada escolha deve ser uma ação jogável escrita em uma única frase. Não copie instruções ou use termos genéricos.
""".strip()


def _context_block(session: Session, campaign_id: int, game_session_id: int | None = None) -> str:
    campaign = session.get(Campaign, campaign_id)
    if game_session_id:
        state = session.get(GameSession, game_session_id)
        msg_turns = session.exec(
            select(CampaignMessage)
            .where(CampaignMessage.game_session_id == game_session_id)
            .order_by(CampaignMessage.created_at)
        ).all()[-12:]
        char_statuses = session.exec(
            select(CharacterStatus)
            .where(CharacterStatus.game_session_id == game_session_id)
        ).all()
        party = "\n".join(
            f"- {char.name}: {char.ancestry} {char.character_class}. {char.backstory}"
            for char in char_statuses
        )
        recent = "\n".join(f"{turn.speaker}: {turn.content}" for turn in msg_turns)
    else:
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
    game_session_id: int | None = None,
) -> WorldState | GameSession:
    if game_session_id:
        state = session.get(GameSession, game_session_id)
    else:
        state = get_world_state(session, campaign_id)
        
    choices = extract_choices(dm_text)
    fallback_choices = choices or choices_from_state(state) or [
        "Fazer uma pergunta de acompanhamento.",
        "Inspecionar a área.",
        "Seguir adiante com cuidado.",
    ]
    fallback = {
        "location": state.current_location,
        "objective": state.active_objective,
        "summary": state.scene_summary,
        "choices": fallback_choices,
    }
    extracted = await ollama_service.chat_json(
        (
            "Você é o modelo utilitário para um aplicativo web de D&D. O modelo principal do Mestre escreve "
            "a narração; seu trabalho é convertê-la em um estado de UI compacto e ações dinâmicas para o jogador. "
            "Retorne apenas JSON válido e não continue a história."
        ),
        f"""
Narração do Mestre (DM):
{dm_text}

Retorne um JSON com os seguintes campos em português do Brasil:
location: local atual concreto, 2 a 6 palavras
objective: objetivo ativo imediato, 4 a 14 palavras
summary: resumo de uma frase da cena atual
choices: array de 2 a 4 strings (não objetos). Cada string deve ser uma ação concisa do jogador específica para a cena, com menos de 90 caracteres

Regras de escolha:
- Use substantivos concretos da cena quando possível.
- Inclua uma mistura de opções de investigação, social, viagem ou risco quando relevante.
- Não peça rolagens de dados no texto da escolha.
- Não invente resultados, recompensas ou sucesso.
- Não retorne objetos como {{"action": "..."}}, apenas strings normais.
- Escreva tudo em português do Brasil.

Não use rótulos ou marcadores como "localização atual curta",
"objetivo imediato ativo", "resumo da cena de uma frase" ou "ação específica jogável".
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
        "Local desconhecido",
    )
    state.active_objective = clean_extracted_text(
        extracted.get("objective"),
        state.active_objective,
        180,
        "Escolha o próximo movimento.",
    )
    fallback_summary = " ".join(format_summary_source(dm_text).split()[:42])
    summary = extracted.get("summary") or fallback_summary or state.scene_summary
    state.scene_summary = clean_extracted_text(
        summary,
        fallback_summary or state.scene_summary,
        260,
        "A cena está se desenrolando.",
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
        (("sneak", "hide", "stealth", "furtivo", "esconder", "furtividade"), "Destreza", "Furtividade", 13),
        (("persuade", "convince", "lie", "deceive", "rumor", "persuadir", "convencer", "mentir", "enganar", "boato"), "Carisma", "Persuasão", 12),
        (("search", "inspect", "investigate", "study", "procurar", "inspecionar", "investigar", "estudar"), "Inteligência", "Investigação", 12),
        (("listen", "notice", "watch", "spot", "ouvir", "notar", "observar", "perceber"), "Sabedoria", "Percepção", 12),
        (("climb", "force", "break", "lift", "escalar", "forçar", "quebrar", "levantar"), "Força", "Atletismo", 13),
        (("attack", "strike", "shoot", "stab", "atacar", "golpear", "atirar", "esfaquear"), "Força ou Destreza", "Ataque", 12),
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
        "ability": "Atributo",
        "skill": None,
        "dc": 10,
        "reason": "",
    }


async def decide_roll(session: Session, campaign_id: int, action: str, game_session_id: int | None = None) -> dict[str, Any]:
    fallback = _fallback_roll_decision(action)
    system = (
        "Você é o árbitro de regras utilitário para um jogo de D&D. O Mestre principal "
        "narra; seu trabalho é decidir se a ação do jogador precisa de um teste de dados. "
        "Retorne apenas JSON."
    )
    user = f"""
{_context_block(session, campaign_id, game_session_id)}

Ação do jogador:
{action}

Retorne um JSON com os seguintes campos (textos em português do Brasil):
requires_roll: boolean
narration: texto curto opcional de preparação antes da rolagem
formula: fórmula do dado, geralmente 1d20 mais um modificador pequeno como 1d20+2
ability: nome do atributo (ex: Força, Destreza, Constituição, Inteligência, Sabedoria, Carisma)
skill: perícia correspondente (ou null)
dc: número inteiro de 5 a 25 (Classe de Dificuldade)
reason: justificativa curta de por que a rolagem é necessária

Exija uma rolagem apenas quando a falha criar uma consequência interessante.
Não exija rolagem para navegação simples, conversas comuns ou ações seguras.
Use o contexto do personagem atual ao escolher o atributo e a perícia mais prováveis.
A justificativa (reason) e a narração devem ser apenas sobre esta ação.
Escreva todos os campos de texto (narration, reason, ability, skill) em português do Brasil.
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
    game_session_id: int | None = None,
) -> str:
    lore_pack = None
    if game_session_id:
        game_session = session.get(GameSession, game_session_id)
        if game_session and game_session.lore_pack:
            lore_pack = game_session.lore_pack

    if lore_pack:
        lore = await rag_store.search(
            pending.action_text,
            limit=4,
            lore_pack=lore_pack,
        )
    else:
        lore = await rag_store.search(
            pending.action_text,
            limit=4,
            document_ids=get_campaign_lore_ids(session, campaign_id),
        )
    lore_text = "\n".join(
        f"[{item['filename']}#{item['chunk_index']}] {item['text']}" for item in lore
    )
    
    if game_session_id:
        characters = session.exec(
            select(CharacterStatus)
            .where(CharacterStatus.game_session_id == game_session_id)
        ).all()
    else:
        characters = get_characters(session, campaign_id)
        
    # Separate player characters from NPC/AI characters
    human_characters = [c for c in characters if c.is_human]
    ai_characters = [c for c in characters if not c.is_human]
    
    party = []
    if human_characters:
        party.append("Jogador Humano:")
        for char in human_characters:
            party.append(f"- {char.name}, {char.ancestry} {char.character_class}: {char.backstory}")
    if ai_characters:
        party.append("Companheiros de Grupo controlados por você (IA):")
        for char in ai_characters:
            party.append(f"- {char.name}, {char.ancestry} {char.character_class}: {char.backstory}")
    party_text = "\n".join(party)

    return f"""
{_context_block(session, campaign_id, game_session_id)}

Ação do jogador:
{pending.action_text}

Teste necessário:
{pending.ability}{f" ({pending.skill})" if pending.skill else ""}, CD {pending.dc}
Motivo: {pending.reason}

Resultado do dado:
Fórmula: {roll.formula}
Dados rolados: {roll.rolls_json}
Total: {roll.total}
Resultado: {roll.outcome}

Lore relevante:
{lore_text or "Nenhum histórico recuperado."}

Grupo:
{party_text or "Nenhum membro do grupo."}

Resolva a ação como o Mestre em português do Brasil. Reflita o resultado do dado diretamente na história.
Em caso de sucesso, recompense o progresso. Em caso de falha, adicione uma complicação sem bloquear o jogo.
Se houver companheiros de grupo da IA, você também controla as ações e diálogos deles. Separe claramente a narrativa do Mestre das falas e ações dos companheiros (ex: [Nome do Companheiro]: 'Fala...').
Escreva entre 120 e 180 palavras e mantenha-se abaixo de 1000 caracteres. Evite cabeçalhos markdown, marcadores em negrito e separadores.
Retorne apenas a narração do Mestre (e falas/ações dos companheiros) e a seção Escolhas. Não explique o que sua resposta faz.
Escreva tudo exclusivamente em português do Brasil.
Termine com uma seção Escolhas contendo de 2 a 4 opções numeradas que sejam específicas para a cena atual, NPCs nomeados, ameaças ou pistas. Cada escolha deve ser uma ação jogável escrita em uma única frase. Não copie instruções ou use termos genéricos.
""".strip()


def add_session_message(session: Session, game_session_id: int, speaker: str, content: str) -> CampaignMessage:
    msg = CampaignMessage(game_session_id=game_session_id, speaker=speaker, content=content)
    session.add(msg)
    
    # Also update GameSession's updated_at timestamp
    game_session = session.get(GameSession, game_session_id)
    if game_session:
        game_session.updated_at = now_utc()
        session.add(game_session)
        
    session.commit()
    session.refresh(msg)
    return msg


def create_game_session(session: Session, campaign_id: int, name: str, lore_pack: str | None = None) -> GameSession:
    campaign = session.get(Campaign, campaign_id)
    if not campaign:
        raise ValueError("Campaign not found")
        
    world_state = session.exec(
        select(WorldState).where(WorldState.campaign_id == campaign_id)
    ).first()
    
    current_location = world_state.current_location if world_state else "Abertura da campanha"
    active_objective = world_state.active_objective if world_state else "Estabelecer a primeira cena."
    scene_summary = world_state.scene_summary if world_state else ""
    choices_json = world_state.choices_json if world_state else json.dumps([
        "Procurar trabalho ou boatos.",
        "Encontrar um lugar seguro para descansar.",
        "Estudar os problemas locais.",
    ])
    
    game_session = GameSession(
        campaign_id=campaign_id,
        name=name,
        lore_pack=lore_pack,
        current_location=current_location,
        active_objective=active_objective,
        scene_summary=scene_summary,
        choices_json=choices_json
    )
    session.add(game_session)
    session.commit()
    session.refresh(game_session)
    
    # Snapshot characters
    characters = session.exec(
        select(Character).where(Character.campaign_id == campaign_id)
    ).all()
    for char in characters:
        status = CharacterStatus(
            game_session_id=game_session.id,
            character_id=char.id,
            name=char.name,
            ancestry=char.ancestry,
            character_class=char.character_class,
            backstory=char.backstory,
            inventory=char.inventory_json,
            is_human=char.is_human,
            hp=10,
            max_hp=10,
            level=1,
            xp=0,
            gold=0
        )
        session.add(status)
        
    # Snapshot turns
    turns = session.exec(
        select(Turn).where(Turn.campaign_id == campaign_id).order_by(Turn.created_at)
    ).all()
    for turn in turns:
        msg = CampaignMessage(
            game_session_id=game_session.id,
            speaker=turn.speaker,
            content=turn.content,
            created_at=turn.created_at
        )
        session.add(msg)
        
    session.commit()
    session.refresh(game_session)
    return game_session


def delete_game_session(session: Session, game_session_id: int) -> None:
    game_session = session.get(GameSession, game_session_id)
    if not game_session:
        raise ValueError("Session not found")
        
    characters = session.exec(
        select(CharacterStatus).where(CharacterStatus.game_session_id == game_session_id)
    ).all()
    for char in characters:
        session.delete(char)
        
    messages = session.exec(
        select(CampaignMessage).where(CampaignMessage.game_session_id == game_session_id)
    ).all()
    for msg in messages:
        session.delete(msg)
        
    session.delete(game_session)
    session.commit()


async def extract_status_updates(
    session: Session,
    campaign_id: int,
    dm_text: str,
    game_session_id: int,
) -> list[dict]:
    # We query the utility LLM to parse updates from the narrative
    characters = session.exec(
        select(CharacterStatus).where(CharacterStatus.game_session_id == game_session_id)
    ).all()
    
    char_names = ", ".join(c.name for c in characters)
    
    system = (
        "Você é o motor de regras de D&D. Analise a narração do Mestre (DM) "
        "e identifique se algum personagem recebeu/perdeu XP, ouro, HP ou itens. "
        "Personagens no grupo: " + char_names + ". Retorne APENAS um objeto JSON."
    )
    
    user = f"""
Narração do Mestre:
{dm_text}

Analise a narração e retorne as atualizações de estado do grupo seguindo exatamente este formato JSON:
{{
  "character_updates": [
    {{
      "name": "Nome do Personagem",
      "xp_gained": 0,
      "gold_gained": 0,
      "hp_change": 0,
      "items_added": [
        {{
          "name": "Nome do Item",
          "type": "consumable | weapon | armor | utility",
          "effect": "Descrição curta do efeito (ex: cura 5 HP)"
        }}
      ],
      "items_removed": ["Nome do Item a remover"]
    }}
  ]
}}
Retorne apenas JSON válido. Se não houver mudanças, retorne um array vazio de character_updates.
""".strip()
    
    fallback = {"character_updates": []}
    response = await ollama_service.chat_json(system, user, fallback)
    
    updates = response.get("character_updates", [])
    applied_updates = []
    
    import uuid
    for up in updates:
        name = up.get("name", "")
        # Find matching character status
        char_status = None
        for c in characters:
            if c.name.lower() in name.lower() or name.lower() in c.name.lower():
                char_status = c
                break
        
        if not char_status:
            continue
            
        previous_level = char_status.level
        
        # Apply HP change
        hp_change = int(up.get("hp_change", 0))
        if hp_change != 0:
            char_status.hp = max(0, min(char_status.max_hp, char_status.hp + hp_change))
            
        # Apply XP and handle Level Up!
        xp_gained = int(up.get("xp_gained", 0))
        if xp_gained > 0:
            char_status.xp += xp_gained
            next_level_xp = char_status.level * 100
            if char_status.xp >= next_level_xp:
                char_status.level += 1
                char_status.xp -= next_level_xp
                char_status.max_hp += 5
                char_status.hp = char_status.max_hp
                
        # Apply gold
        gold_gained = int(up.get("gold_gained", 0))
        if gold_gained != 0:
            char_status.gold = max(0, char_status.gold + gold_gained)
            
        # Apply inventory changes
        current_inv = []
        try:
            current_inv = json.loads(char_status.inventory)
            if not isinstance(current_inv, list):
                current_inv = []
        except:
            current_inv = []
            
        # Add items
        items_added = up.get("items_added", [])
        added_to_alert = []
        for item in items_added:
            new_item = {
                "id": str(uuid.uuid4())[:8],
                "name": item.get("name", "Item desconhecido"),
                "type": item.get("type", "utility"),
                "effect": item.get("effect", "Sem efeito mecânico")
            }
            current_inv.append(new_item)
            added_to_alert.append(new_item)
            
        # Remove items
        items_removed = up.get("items_removed", [])
        removed_to_alert = []
        for item_name in items_removed:
            for idx, item in enumerate(current_inv):
                if item_name.lower() in item["name"].lower() or item["name"].lower() in item_name.lower():
                    removed_to_alert.append(current_inv.pop(idx))
                    break
                    
        char_status.inventory = json.dumps(current_inv)
        session.add(char_status)
        
        applied_updates.append({
            "character_id": char_status.character_id,
            "name": char_status.name,
            "xp_gained": xp_gained,
            "gold_gained": gold_gained,
            "hp_change": hp_change,
            "level_up": char_status.level > previous_level,
            "level": char_status.level,
            "items_added": added_to_alert,
            "items_removed": removed_to_alert
        })
        
    session.commit()
    return applied_updates
