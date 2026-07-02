import json
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from dndllm26.core.settings import get_settings
from dndllm26.db.models import Campaign, Hero, LoreDocument, PendingRoll, now_utc, GameSession, CharacterStatus, CampaignMessage
from dndllm26.db.session import get_session
from dndllm26.game.service import (
    add_character,
    add_event,
    add_turn,
    build_roll_resolution_prompt,
    build_dm_prompt,
    clone_hero_to_campaign,
    generate_ai_companions,
    create_pending_roll,
    create_campaign,
    decide_roll,
    generate_campaign_intro,
    get_characters,
    get_pending_roll,
    get_turns,
    get_world_state,
    list_campaigns,
    perform_pending_roll,
    seed_default_heroes,
    set_campaign_lore,
    trim_dm_text,
    update_world_from_dm_response,
    choices_from_state,
    create_game_session,
    delete_game_session,
    add_session_message,
    extract_status_updates,
)
from dndllm26.llm.ollama_client import ollama_service
from dndllm26.rag.store import rag_store

router = APIRouter()


def sse_data(data: str, event: str | None = None) -> str:
    lines = []
    if event:
        lines.append(f"event: {event}")
    for line in data.splitlines() or [""]:
        lines.append(f"data: {line}")
    return "\n".join(lines) + "\n\n"


def sse_json(event: str, payload: dict[str, Any]) -> str:
    return sse_data(json.dumps(payload), event=event)


def register_uploaded_pdfs(session: Session) -> list[LoreDocument]:
    settings = get_settings()
    documents: list[LoreDocument] = []
    allowed_patterns = ["*.pdf", "*.txt"]
    paths = []
    for pattern in allowed_patterns:
        paths.extend(settings.upload_dir.glob(pattern))
    for path in sorted(paths, key=lambda p: p.name):
        existing = session.exec(
            select(LoreDocument).where(LoreDocument.filename == path.name)
        ).first()
        if existing:
            documents.append(existing)
            continue
        doc = LoreDocument(filename=path.name, status="queued")
        session.add(doc)
        session.commit()
        session.refresh(doc)
        documents.append(doc)
    return documents


async def index_lore_document(document_id: int) -> None:
    from dndllm26.db.session import engine

    settings = get_settings()
    with Session(engine) as job_session:
        record = job_session.get(LoreDocument, document_id)
        if not record:
            return
        path = settings.upload_dir / record.filename
        if not path.exists():
            record.status = "error"
            record.error = "Uploaded file is missing from disk."
            job_session.add(record)
            job_session.commit()
            return
        try:
            record.status = "indexing"
            record.error = None
            job_session.add(record)
            job_session.commit()
            chunks = await rag_store.index_document(path, document_id)
            record.status = "ready"
            record.chunks = chunks
        except Exception as exc:
            record.status = "error"
            record.error = str(exc)
        job_session.add(record)
        job_session.commit()


def queue_lore_indexing(session: Session, background_tasks: BackgroundTasks) -> None:
    for doc in register_uploaded_pdfs(session):
        if doc.status in {"queued", "error"} and doc.chunks == 0:
            background_tasks.add_task(index_lore_document, doc.id)


class CampaignCreate(BaseModel):
    title: str = "The Shattered Gate"
    setting: str = "A frontier city built over sealed ruins."
    tone: str = "dangerous heroic fantasy"
    hero_ids: list[int] = Field(default_factory=list)
    lore_document_ids: list[int] = Field(default_factory=list)
    ai_companions_count: int = 0
    ai_companions_classes: list[str] = Field(default_factory=list)


class CharacterCreate(BaseModel):
    name: str
    ancestry: str = "Human"
    character_class: str = "Fighter"
    backstory: str = "An adventurer looking for a reason to risk everything."
    inventory: list[str] = Field(default_factory=lambda: ["torch", "rations", "dagger"])
    is_human: bool = True


class HeroCreate(BaseModel):
    name: str
    ancestry: str = "Human"
    character_class: str = "Fighter"
    backstory: str = "An adventurer looking for a reason to risk everything."
    inventory: list[str] = Field(default_factory=lambda: ["torch", "rations", "dagger"])
    is_human: bool = True


class HeroUpdate(BaseModel):
    name: str | None = None
    ancestry: str | None = None
    character_class: str | None = None
    backstory: str | None = None
    inventory: list[str] | None = None
    is_human: bool | None = None


class PlayerAction(BaseModel):
    content: str


def pending_roll_payload(pending: PendingRoll) -> dict:
    return {
        "id": pending.id,
        "campaign_id": pending.campaign_id,
        "action_text": pending.action_text,
        "formula": pending.formula,
        "ability": pending.ability,
        "skill": pending.skill,
        "dc": pending.dc,
        "reason": pending.reason,
        "narration": pending.narration,
        "status": pending.status,
    }


def lore_payload(doc: LoreDocument) -> dict:
    return {
        "id": doc.id,
        "filename": doc.filename,
        "status": doc.status,
        "chunks": doc.chunks,
        "created_at": doc.created_at,
        "error": doc.error,
    }


@router.get("/health")
async def health() -> dict:
    try:
        models = await ollama_service.list_models()
        ollama = "ok"
    except Exception as exc:
        models = []
        ollama = f"error: {ollama_service.error_message(exc)}"
    settings = get_settings()
    return {
        "status": "ok",
        "ollama": ollama,
        "ollama_host": settings.ollama_host,
        "chat_model": settings.ollama_chat_model,
        "utility_model": settings.ollama_utility_model or settings.ollama_chat_model,
        "embed_model": settings.ollama_embed_model,
        "models": models,
    }


@router.get("/models")
async def models() -> dict:
    settings = get_settings()
    return {
        "models": await ollama_service.list_models(),
        "chat_model": settings.ollama_chat_model,
        "utility_model": settings.ollama_utility_model or settings.ollama_chat_model,
        "embed_model": settings.ollama_embed_model,
    }


@router.post("/models/{model}/pull")
async def pull_model(model: str, background_tasks: BackgroundTasks) -> dict:
    background_tasks.add_task(ollama_service.pull_model, model)
    return {"status": "queued", "model": model}


@router.get("/campaigns")
def campaigns(session: Session = Depends(get_session)) -> list[Campaign]:
    return list_campaigns(session)


@router.post("/campaigns")
async def create(payload: CampaignCreate, session: Session = Depends(get_session)) -> dict:
    campaign = create_campaign(session, payload.title, payload.setting, payload.tone)
    for hero_id in payload.hero_ids:
        hero = session.get(Hero, hero_id)
        if hero:
            clone_hero_to_campaign(session, campaign.id, hero)
    if payload.ai_companions_count > 0:
        await generate_ai_companions(session, campaign.id, payload.ai_companions_count, payload.ai_companions_classes)
    set_campaign_lore(session, campaign.id, payload.lore_document_ids)
    try:
        intro = await generate_campaign_intro(session, campaign.id)
        if intro:
            add_turn(session, campaign.id, "DM", intro)
            state = await update_world_from_dm_response(session, campaign.id, intro)
            add_event(
                session,
                campaign.id,
                "campaign_intro_generated",
                {
                    "location": state.current_location,
                    "objective": state.active_objective,
                    "choices": choices_from_state(state),
                },
            )
    except Exception as exc:
        add_event(
            session,
            campaign.id,
            "campaign_intro_failed",
            {"error": ollama_service.error_message(exc)},
        )
    return {
        "id": campaign.id,
        "title": campaign.title,
        "setting": campaign.setting,
        "tone": campaign.tone,
        "created_at": campaign.created_at,
        "updated_at": campaign.updated_at,
    }


@router.get("/heroes")
def heroes(session: Session = Depends(get_session)) -> list[Hero]:
    seed_default_heroes(session)
    return list(session.exec(select(Hero).order_by(Hero.updated_at.desc())).all())


@router.post("/heroes")
def create_hero(payload: HeroCreate, session: Session = Depends(get_session)) -> Hero:
    hero = Hero(
        name=payload.name,
        ancestry=payload.ancestry,
        character_class=payload.character_class,
        backstory=payload.backstory,
        inventory_json=json.dumps(payload.inventory),
        is_human=payload.is_human,
    )
    session.add(hero)
    session.commit()
    session.refresh(hero)
    return hero


@router.patch("/heroes/{hero_id}")
def update_hero(
    hero_id: int,
    payload: HeroUpdate,
    session: Session = Depends(get_session),
) -> Hero:
    hero = session.get(Hero, hero_id)
    if not hero:
        raise HTTPException(status_code=404, detail="Hero not found")
    updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items():
        if key == "inventory":
            hero.inventory_json = json.dumps(value)
        else:
            setattr(hero, key, value)
    hero.updated_at = now_utc()
    session.add(hero)
    session.commit()
    session.refresh(hero)
    return hero


@router.delete("/heroes/{hero_id}")
def delete_hero(hero_id: int, session: Session = Depends(get_session)) -> dict:
    hero = session.get(Hero, hero_id)
    if not hero:
        raise HTTPException(status_code=404, detail="Hero not found")
    session.delete(hero)
    session.commit()
    return {"status": "deleted", "id": hero_id}


@router.get("/campaigns/{campaign_id}")
def campaign_detail(campaign_id: int, session: Session = Depends(get_session)) -> dict:
    campaign = session.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    pending = get_pending_roll(session, campaign_id)
    world_state = get_world_state(session, campaign_id)
    return {
        "campaign": campaign,
        "characters": get_characters(session, campaign_id),
        "turns": get_turns(session, campaign_id),
        "world_state": world_state,
        "choices": choices_from_state(world_state),
        "pending_roll": pending_roll_payload(pending) if pending else None,
    }


@router.post("/campaigns/{campaign_id}/characters")
def campaign_character(
    campaign_id: int,
    payload: CharacterCreate,
    session: Session = Depends(get_session),
):
    if not session.get(Campaign, campaign_id):
        raise HTTPException(status_code=404, detail="Campaign not found")
    return add_character(
        session,
        campaign_id,
        payload.name,
        payload.ancestry,
        payload.character_class,
        payload.backstory,
        payload.inventory,
        payload.is_human,
    )


@router.post("/characters")
def character(payload: CharacterCreate, session: Session = Depends(get_session)):
    raise HTTPException(
        status_code=410,
        detail="Use POST /api/campaigns/{campaign_id}/characters instead.",
    )


@router.post("/campaigns/{campaign_id}/turns/stream")
async def stream_turn(
    campaign_id: int,
    payload: PlayerAction,
    session: Session = Depends(get_session),
):
    add_turn(session, campaign_id, "Player", payload.content)
    prompt = await build_dm_prompt(session, campaign_id, payload.content)

    async def event_stream():
        parts: list[str] = []
        try:
            async for token in ollama_service.stream_dm(prompt):
                if sum(len(part) for part in parts) >= 1000:
                    break
                remaining = 1000 - sum(len(part) for part in parts)
                token = token[:remaining]
                parts.append(token)
                yield sse_data(token)
        except Exception as exc:
            message = (
                "\n\n[Local LLM error] "
                f"{ollama_service.error_message(exc)}. "
                "Check that Ollama is running and the configured chat model is pulled."
            )
            parts.append(message)
            yield sse_data(message, event="error")
        dm_text = trim_dm_text("".join(parts))
        if dm_text:
            from dndllm26.db.session import engine

            with Session(engine) as write_session:
                add_turn(write_session, campaign_id, "DM", dm_text)
                await update_world_from_dm_response(write_session, campaign_id, dm_text)
        yield sse_data("done", event="done")

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/campaigns/{campaign_id}/actions/stream")
async def stream_action(
    campaign_id: int,
    payload: PlayerAction,
    session: Session = Depends(get_session),
):
    if not session.get(Campaign, campaign_id):
        raise HTTPException(status_code=404, detail="Campaign not found")
    if get_pending_roll(session, campaign_id):
        raise HTTPException(status_code=409, detail="Resolve the pending dice roll first.")

    action = payload.content.strip()
    if not action:
        raise HTTPException(status_code=400, detail="Action cannot be empty.")
    add_turn(session, campaign_id, "Player", action)
    add_event(session, campaign_id, "player_action", {"content": action})

    decision = await decide_roll(session, campaign_id, action)
    if decision["requires_roll"]:
        pending = create_pending_roll(session, campaign_id, action, decision)

        async def roll_required_stream():
            if pending.narration:
                yield sse_json("narration", {"content": pending.narration})
            yield sse_json("roll_required", pending_roll_payload(pending))
            yield sse_json("done", {"status": "roll_required"})

        return StreamingResponse(roll_required_stream(), media_type="text/event-stream")

    prompt = await build_dm_prompt(session, campaign_id, action)

    async def event_stream():
        parts: list[str] = []
        yield sse_json("phase", {"status": "dm_streaming"})
        try:
            async for token in ollama_service.stream_dm(prompt):
                if sum(len(part) for part in parts) >= 1000:
                    break
                remaining = 1000 - sum(len(part) for part in parts)
                token = token[:remaining]
                parts.append(token)
                yield sse_json("narration_delta", {"content": token})
        except Exception as exc:
            message = (
                "[Local LLM error] "
                f"{ollama_service.error_message(exc)}. "
                "Check that Ollama is running and the configured chat model is pulled."
            )
            parts.append(message)
            yield sse_json("error", {"message": message})
        dm_text = trim_dm_text("".join(parts))
        if dm_text:
            from dndllm26.db.session import engine

            with Session(engine) as write_session:
                add_turn(write_session, campaign_id, "DM", dm_text)
                yield sse_json("phase", {"status": "utility_analyzing"})
                add_event(write_session, campaign_id, "dm_response", {"content": dm_text})
                state = await update_world_from_dm_response(write_session, campaign_id, dm_text)
                yield sse_json(
                    "choices_updated",
                    {
                        "choices": choices_from_state(state),
                        "location": state.current_location,
                        "objective": state.active_objective,
                        "summary": state.scene_summary,
                    },
                )
        yield sse_json("done", {"status": "complete"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/campaigns/{campaign_id}/rolls/{pending_roll_id}/resolve/stream")
async def resolve_roll_stream(
    campaign_id: int,
    pending_roll_id: int,
    session: Session = Depends(get_session),
):
    pending = session.get(PendingRoll, pending_roll_id)
    if not pending or pending.campaign_id != campaign_id:
        raise HTTPException(status_code=404, detail="Pending roll not found")
    try:
        roll = perform_pending_roll(session, campaign_id, pending_roll_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    add_turn(
        session,
        campaign_id,
        "Roll",
        (
            f"{pending.ability}{f' ({pending.skill})' if pending.skill else ''}: "
            f"{roll.formula} -> {roll.rolls_json}"
            f"{f' + {roll.modifier}' if roll.modifier > 0 else ''}"
            f" = {roll.total} vs DC {pending.dc}: {roll.outcome}"
        ),
    )
    prompt = await build_roll_resolution_prompt(session, campaign_id, pending, roll)

    async def event_stream():
        parts: list[str] = []
        yield sse_json(
            "roll_result",
            {
                "pending_roll_id": pending.id,
                "formula": roll.formula,
                "rolls": json.loads(roll.rolls_json),
                "modifier": roll.modifier,
                "total": roll.total,
                "dc": roll.dc,
                "outcome": roll.outcome,
                "reason": roll.reason,
            },
        )
        try:
            async for token in ollama_service.stream_dm(prompt):
                if sum(len(part) for part in parts) >= 1000:
                    break
                remaining = 1000 - sum(len(part) for part in parts)
                token = token[:remaining]
                parts.append(token)
                yield sse_json("narration_delta", {"content": token})
        except Exception as exc:
            message = (
                "[Local LLM error] "
                f"{ollama_service.error_message(exc)}. "
                "The dice result was stored, but the DM response failed."
            )
            parts.append(message)
            yield sse_json("error", {"message": message})
        dm_text = trim_dm_text("".join(parts))
        if dm_text:
            from dndllm26.db.session import engine

            with Session(engine) as write_session:
                add_turn(write_session, campaign_id, "DM", dm_text)
                yield sse_json("phase", {"status": "utility_analyzing"})
                add_event(write_session, campaign_id, "dm_response", {"content": dm_text})
                state = await update_world_from_dm_response(write_session, campaign_id, dm_text)
                yield sse_json(
                    "choices_updated",
                    {
                        "choices": choices_from_state(state),
                        "location": state.current_location,
                        "objective": state.active_objective,
                        "summary": state.scene_summary,
                    },
                )
        yield sse_json("done", {"status": "complete"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/lore/upload")
async def upload_lore(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> LoreDocument:
    filename_lower = file.filename.lower()
    if not (filename_lower.endswith(".pdf") or filename_lower.endswith(".txt")):
        raise HTTPException(status_code=400, detail="Somente arquivos PDF e TXT são suportados")
    settings = get_settings()
    path = settings.upload_dir / file.filename
    path.write_bytes(await file.read())
    doc = LoreDocument(filename=file.filename, status="queued")
    session.add(doc)
    session.commit()
    session.refresh(doc)

    background_tasks.add_task(index_lore_document, doc.id)
    return doc


@router.get("/lore")
def lore(
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
) -> list[LoreDocument]:
    queue_lore_indexing(session, background_tasks)
    return list(session.exec(select(LoreDocument).order_by(LoreDocument.created_at.desc())).all())


@router.post("/lore/refresh-index")
def refresh_lore_index(
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
) -> dict:
    queue_lore_indexing(session, background_tasks)
    docs = session.exec(select(LoreDocument).order_by(LoreDocument.created_at.desc())).all()
    queued = [
        doc.id
        for doc in docs
        if doc.status in {"queued", "error"} and doc.chunks == 0 and doc.id is not None
    ]
    return {"status": "queued", "queued": queued, "documents": [lore_payload(doc) for doc in docs]}


from dndllm26.rag.lore_manager import get_lore_packs, ensure_lore_pack_indexed

@router.get("/lore/packs")
def list_lore_packs() -> list[dict]:
    return get_lore_packs()


class SessionCreate(BaseModel):
    campaign_id: int
    name: str
    lore_pack: str | None = None


@router.post("/sessions")
async def create_session(payload: SessionCreate, session: Session = Depends(get_session)) -> dict:
    try:
        if payload.lore_pack:
            await ensure_lore_pack_indexed(payload.lore_pack)
        sess = create_game_session(session, payload.campaign_id, payload.name, payload.lore_pack)
        return sess.model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/sessions")
def list_sessions(session: Session = Depends(get_session)) -> list[dict]:
    sessions = session.exec(select(GameSession).order_by(GameSession.updated_at.desc())).all()
    return [s.model_dump() for s in sessions]


@router.get("/sessions/{session_id}")
def session_detail(session_id: int, session: Session = Depends(get_session)) -> dict:
    game_session = session.get(GameSession, session_id)
    if not game_session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    campaign = session.get(Campaign, game_session.campaign_id)
    characters = session.exec(
        select(CharacterStatus).where(CharacterStatus.game_session_id == session_id)
    ).all()
    
    messages = session.exec(
        select(CampaignMessage).where(CampaignMessage.game_session_id == session_id).order_by(CampaignMessage.created_at)
    ).all()
    
    turns = [
        {
            "id": msg.id,
            "campaign_id": game_session.campaign_id,
            "speaker": msg.speaker,
            "content": msg.content,
            "created_at": msg.created_at
        }
        for msg in messages
    ]
    
    pending = get_pending_roll(session, game_session.campaign_id)
    
    return {
        "session": game_session.model_dump(),
        "campaign": campaign.model_dump() if campaign else None,
        "characters": [c.model_dump() for c in characters],
        "turns": turns,
        "choices": choices_from_state(game_session),
        "pending_roll": pending_roll_payload(pending) if pending else None
    }


@router.delete("/sessions/{session_id}")
def remove_session(session_id: int, session: Session = Depends(get_session)) -> dict:
    try:
        delete_game_session(session, session_id)
        return {"status": "deleted", "id": session_id}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/sessions/{session_id}/actions/stream")
async def stream_session_action(
    session_id: int,
    payload: PlayerAction,
    session: Session = Depends(get_session),
):
    game_session = session.get(GameSession, session_id)
    if not game_session:
        raise HTTPException(status_code=404, detail="Session not found")
        
    campaign_id = game_session.campaign_id
    if get_pending_roll(session, campaign_id):
        raise HTTPException(status_code=409, detail="Resolve the pending dice roll first.")

    action = payload.content.strip()
    if not action:
        raise HTTPException(status_code=400, detail="Action cannot be empty.")
        
    add_session_message(session, session_id, "Player", action)
    add_event(session, campaign_id, "player_action", {"content": action, "session_id": session_id})

    decision = await decide_roll(session, campaign_id, action, game_session_id=session_id)
    if decision["requires_roll"]:
        pending = create_pending_roll(session, campaign_id, action, decision)

        async def roll_required_stream():
            if pending.narration:
                yield sse_json("narration", {"content": pending.narration})
            yield sse_json("roll_required", pending_roll_payload(pending))
            yield sse_json("done", {"status": "roll_required"})

        return StreamingResponse(roll_required_stream(), media_type="text/event-stream")

    prompt = await build_dm_prompt(session, campaign_id, action, game_session_id=session_id)

    async def event_stream():
        parts: list[str] = []
        yield sse_json("phase", {"status": "dm_streaming"})
        try:
            async for token in ollama_service.stream_dm(prompt):
                if sum(len(part) for part in parts) >= 1000:
                    break
                remaining = 1000 - sum(len(part) for part in parts)
                token = token[:remaining]
                parts.append(token)
                yield sse_json("narration_delta", {"content": token})
        except Exception as exc:
            message = (
                "[Local LLM error] "
                f"{ollama_service.error_message(exc)}. "
                "Check that Ollama is running and the configured chat model is pulled."
            )
            parts.append(message)
            yield sse_json("error", {"message": message})
            
        dm_text = trim_dm_text("".join(parts))
        if dm_text:
            from dndllm26.db.session import engine
            with Session(engine) as write_session:
                add_session_message(write_session, session_id, "DM", dm_text)
                yield sse_json("phase", {"status": "utility_analyzing"})
                add_event(write_session, campaign_id, "dm_response", {"content": dm_text, "session_id": session_id})
                state = await update_world_from_dm_response(write_session, campaign_id, dm_text, game_session_id=session_id)
                yield sse_json(
                    "choices_updated",
                    {
                        "choices": choices_from_state(state),
                        "location": state.current_location,
                        "objective": state.active_objective,
                        "summary": state.scene_summary,
                    },
                )
                try:
                    updates = await extract_status_updates(write_session, campaign_id, dm_text, game_session_id=session_id)
                    if updates:
                        yield sse_json("status_updates", {"updates": updates})
                except Exception as exc:
                    print("Status updates extraction failed:", exc)
        yield sse_json("done", {"status": "complete"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/sessions/{session_id}/rolls/{pending_roll_id}/resolve/stream")
async def resolve_session_roll_stream(
    session_id: int,
    pending_roll_id: int,
    session: Session = Depends(get_session),
):
    game_session = session.get(GameSession, session_id)
    if not game_session:
        raise HTTPException(status_code=404, detail="Session not found")
        
    campaign_id = game_session.campaign_id
    pending = session.get(PendingRoll, pending_roll_id)
    if not pending or pending.campaign_id != campaign_id:
        raise HTTPException(status_code=404, detail="Pending roll not found")
        
    try:
        roll = perform_pending_roll(session, campaign_id, pending_roll_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    roll_text = (
        f"{pending.ability}{f' ({pending.skill})' if pending.skill else ''}: "
        f"{roll.formula} -> {roll.rolls_json}"
        f"{f' + {roll.modifier}' if roll.modifier > 0 else ''}"
        f" = {roll.total} vs DC {pending.dc}: {roll.outcome}"
    )
    add_session_message(session, session_id, "Roll", roll_text)

    prompt = await build_roll_resolution_prompt(session, campaign_id, pending, roll, game_session_id=session_id)

    async def event_stream():
        parts: list[str] = []
        yield sse_json(
            "roll_result",
            {
                "pending_roll_id": pending.id,
                "formula": roll.formula,
                "rolls": json.loads(roll.rolls_json),
                "modifier": roll.modifier,
                "total": roll.total,
                "dc": roll.dc,
                "outcome": roll.outcome,
                "reason": roll.reason,
            },
        )
        try:
            async for token in ollama_service.stream_dm(prompt):
                if sum(len(part) for part in parts) >= 1000:
                    break
                remaining = 1000 - sum(len(part) for part in parts)
                token = token[:remaining]
                parts.append(token)
                yield sse_json("narration_delta", {"content": token})
        except Exception as exc:
            message = (
                "[Local LLM error] "
                f"{ollama_service.error_message(exc)}. "
                "The dice result was stored, but the DM response failed."
            )
            parts.append(message)
            yield sse_json("error", {"message": message})
            
        dm_text = trim_dm_text("".join(parts))
        if dm_text:
            from dndllm26.db.session import engine
            with Session(engine) as write_session:
                add_session_message(write_session, session_id, "DM", dm_text)
                yield sse_json("phase", {"status": "utility_analyzing"})
                add_event(write_session, campaign_id, "dm_response", {"content": dm_text, "session_id": session_id})
                state = await update_world_from_dm_response(write_session, campaign_id, dm_text, game_session_id=session_id)
                yield sse_json(
                    "choices_updated",
                    {
                        "choices": choices_from_state(state),
                        "location": state.current_location,
                        "objective": state.active_objective,
                        "summary": state.scene_summary,
                    },
                )
                try:
                    updates = await extract_status_updates(write_session, campaign_id, dm_text, game_session_id=session_id)
                    if updates:
                        yield sse_json("status_updates", {"updates": updates})
                except Exception as exc:
                    print("Status updates extraction failed:", exc)
        yield sse_json("done", {"status": "complete"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


class ItemUsePayload(BaseModel):
    character_id: int
    item_id: str


@router.post("/sessions/{session_id}/items/use")
def use_session_item(
    session_id: int,
    payload: ItemUsePayload,
    session: Session = Depends(get_session)
) -> dict:
    game_session = session.get(GameSession, session_id)
    if not game_session:
        raise HTTPException(status_code=404, detail="Session not found")
        
    char_status = session.exec(
        select(CharacterStatus)
        .where(CharacterStatus.game_session_id == session_id)
        .where(CharacterStatus.character_id == payload.character_id)
    ).first()
    
    if not char_status:
        raise HTTPException(status_code=404, detail="Character not found in this session")
        
    try:
        inventory = json.loads(char_status.inventory)
        if not isinstance(inventory, list):
            inventory = []
    except:
        inventory = []
        
    item_idx = -1
    for idx, item in enumerate(inventory):
        if item.get("id") == payload.item_id:
            item_idx = idx
            break
            
    if item_idx == -1:
        raise HTTPException(status_code=404, detail="Item not found in inventory")
        
    used_item = inventory.pop(item_idx)
    char_status.inventory = json.dumps(inventory)
    
    effect = used_item.get("effect", "").lower()
    healing = 0
    import re
    if "hp" in effect or "cura" in effect:
        digits = re.findall(r"\d+", effect)
        if digits:
            healing = int(digits[0])
            char_status.hp = min(char_status.max_hp, char_status.hp + healing)
            
    session.add(char_status)
    
    message_text = f"O jogador usou {used_item.get('name')}"
    if healing > 0:
        message_text += f" (curando {healing} HP)"
        
    add_session_message(session, session_id, "System", message_text)
    session.commit()
    session.refresh(char_status)
    
    return {
        "status": "success",
        "used_item": used_item,
        "healing_applied": healing,
        "character": char_status.model_dump()
    }
