import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  Activity,
  BookOpen,
  BrainCircuit,
  CheckCircle2,
  Dice5,
  Edit3,
  HeartPulse,
  LoaderCircle,
  MapPin,
  Plus,
  RefreshCw,
  Save,
  ScrollText,
  Send,
  Shield,
  Sparkles,
  Trash2,
  Upload,
  UserPlus,
  Users,
  X
} from "lucide-react";
import {
  API_BASE,
  ChoiceUpdate,
  Campaign,
  Character,
  Health,
  Hero,
  LoreDocument,
  PendingRoll,
  Turn,
  WorldState,
  getJson,
  postJson,
  resolveRoll,
  streamTurn
} from "./lib/api";
import "./styles/app.css";

type Detail = {
  campaign: Campaign;
  characters: Character[];
  turns: Turn[];
  world_state: WorldState;
  choices: string[];
  pending_roll?: PendingRoll | null;
};

type PlayPhase = "ready" | "checking" | "generating" | "roll_required" | "rolling" | "error";

function App() {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [action, setAction] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [lore, setLore] = useState<LoreDocument[]>([]);
  const [heroes, setHeroes] = useState<Hero[]>([]);
  const [selectedHeroIds, setSelectedHeroIds] = useState<number[]>([]);
  const [selectedLoreIds, setSelectedLoreIds] = useState<number[]>([]);
  const [showHeroPicker, setShowHeroPicker] = useState(false);
  const [creatingCampaign, setCreatingCampaign] = useState(false);
  const [rollResult, setRollResult] = useState<string>("Ready");
  const [pendingRoll, setPendingRoll] = useState<PendingRoll | null>(null);
  const [choices, setChoices] = useState<string[]>([]);
  const [phase, setPhase] = useState<PlayPhase>("ready");
  const [statusMessage, setStatusMessage] = useState("Ready for your next move.");
  const [newTitle, setNewTitle] = useState("The Shattered Gate");
  const [newSetting, setNewSetting] = useState(
    "A frontier city built above sealed ruins where old oaths are failing."
  );
  const [newTone, setNewTone] = useState("tense heroic fantasy");

  const turnCount = detail?.turns.filter((turn) => turn.speaker !== "System").length ?? 0;
  const hasConfiguredChatModel = useMemo(() => {
    if (!health) return false;
    return health.models.includes(health.chat_model);
  }, [health]);

  async function refreshCampaigns() {
    const rows = await getJson<Campaign[]>("/campaigns");
    setCampaigns(rows);
    if (!activeId && rows.length) setActiveId(rows[0].id);
  }

  async function refreshDetail(id: number) {
    const next = await getJson<Detail>(`/campaigns/${id}`);
    setDetail(next);
    setChoices(next.choices ?? choicesFromState(next.world_state));
    setPendingRoll(next.pending_roll ?? null);
  }

  async function refreshStatus() {
    const [status, loreRows, heroRows] = await Promise.all([
      getJson<Health>("/health"),
      getJson<LoreDocument[]>("/lore"),
      getJson<Hero[]>("/heroes")
    ]);
    setHealth(status);
    setLore(loreRows);
    setHeroes(heroRows);
    setSelectedHeroIds((current) =>
      current.length ? current.filter((id) => heroRows.some((hero) => hero.id === id)) : []
    );
    setSelectedLoreIds((current) =>
      current.length ? current.filter((id) => loreRows.some((doc) => doc.id === id)) : []
    );
  }

  useEffect(() => {
    refreshCampaigns().catch((err) => setError(String(err)));
    refreshStatus().catch((err) => setError(String(err)));
  }, []);

  useEffect(() => {
    if (activeId) refreshDetail(activeId).catch((err) => setError(String(err)));
  }, [activeId]);

  async function createCampaign() {
    setCreatingCampaign(true);
    try {
      const campaign = await postJson<Campaign>("/campaigns", {
        title: newTitle,
        setting: newSetting,
        tone: newTone,
        hero_ids: selectedHeroIds,
        lore_document_ids: selectedLoreIds
      });
      const campaignId = Number(campaign.id);
      if (!Number.isFinite(campaignId)) {
        throw new Error("Campaign creation did not return an id.");
      }
      setShowHeroPicker(false);
      setActiveId(campaignId);
      await refreshCampaigns();
      await refreshDetail(campaignId);
    } catch (err) {
      setError(String(err));
    } finally {
      setCreatingCampaign(false);
    }
  }

  async function createHero(payload: HeroPayload) {
    await postJson<Hero>("/heroes", payload);
    await refreshStatus();
  }

  async function updateHero(id: number, payload: HeroPayload) {
    await fetch(`${API_BASE}/heroes/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).then(async (response) => {
      if (!response.ok) throw new Error(await response.text());
    });
    await refreshStatus();
  }

  async function deleteHero(id: number) {
    await fetch(`${API_BASE}/heroes/${id}`, { method: "DELETE" }).then(async (response) => {
      if (!response.ok) throw new Error(await response.text());
    });
    setSelectedHeroIds((current) => current.filter((heroId) => heroId !== id));
    await refreshStatus();
  }

  async function submitAction(event: FormEvent) {
    event.preventDefault();
    if (!activeId || !action.trim()) return;
    const playerText = action.trim();
    setAction("");
    setStreaming(true);
    setDraft("");
    setPhase("checking");
    setStatusMessage("DM is judging the action...");
    let gotRollPrompt = false;
    let gotError = false;
    setDetail((current) =>
      current
        ? {
            ...current,
            turns: [
              ...current.turns,
              {
                id: Date.now(),
                campaign_id: activeId,
                speaker: "Player",
                content: playerText,
                created_at: new Date().toISOString()
              }
            ]
          }
        : current
    );
    try {
      await streamTurn(activeId, playerText, (event, payload) => {
        if (event === "narration_delta" && isContentPayload(payload)) {
          setPhase("generating");
          setStatusMessage("DM is generating the scene...");
          setDraft((text) => text + payload.content);
        }
        if (event === "narration" && isContentPayload(payload)) {
          setDraft((text) => text + payload.content);
        }
        if (event === "phase" && isPhasePayload(payload)) {
          if (payload.status === "utility_analyzing") {
            setPhase("checking");
            setStatusMessage("Utility model is preparing actions...");
          }
        }
        if (event === "roll_required" && isPendingRoll(payload)) {
          gotRollPrompt = true;
          setPhase("roll_required");
          setStatusMessage("Dice check required. Roll to continue.");
          setPendingRoll(payload);
        }
        if (event === "choices_updated" && isChoiceUpdate(payload)) {
          setChoices(payload.choices);
          setDetail((current) =>
            current
              ? {
                  ...current,
                  choices: payload.choices,
                  world_state: {
                    ...current.world_state,
                    active_objective: payload.objective,
                    current_location: payload.location,
                    scene_summary: payload.summary,
                    choices_json: JSON.stringify(payload.choices)
                  }
                }
              : current
          );
        }
        if (event === "error" && isErrorPayload(payload)) {
          gotError = true;
          setPhase("error");
          setError(payload.message);
        }
      });
      await refreshDetail(activeId);
      await refreshStatus();
    } catch (err) {
      setError(String(err));
    } finally {
      setStreaming(false);
      setDraft("");
      if (gotRollPrompt) {
        setPhase("roll_required");
        setStatusMessage("Dice check required. Roll to continue.");
      } else if (!gotError) {
        setPhase("ready");
        setStatusMessage("Ready for your next move.");
      }
    }
  }

  async function uploadLore(file: File | null) {
    if (!file) return;
    const data = new FormData();
    data.append("file", file);
    const response = await fetch(`${API_BASE}/lore/upload`, { method: "POST", body: data });
    if (!response.ok) setError(await response.text());
    await refreshStatus();
  }

  async function refreshLoreIndex() {
    const response = await fetch(`${API_BASE}/lore/refresh-index`, { method: "POST" });
    if (!response.ok) {
      setError(await response.text());
      return;
    }
    await refreshStatus();
  }

  function submitQuickAction(text: string) {
    setAction(cleanChoiceText(text));
  }

  async function resolvePendingRoll() {
    if (!activeId || !pendingRoll) return;
    setStreaming(true);
    setDraft("");
    setRollResult("Rolling...");
    setPhase("rolling");
    setStatusMessage("Rolling and resolving the check...");
    let gotError = false;
    try {
      await resolveRoll(activeId, pendingRoll.id, (event, payload) => {
        if (event === "roll_result" && isRollResult(payload)) {
          const mod =
            payload.modifier > 0
              ? ` + ${payload.modifier}`
              : payload.modifier < 0
                ? ` - ${Math.abs(payload.modifier)}`
                : "";
          setRollResult(
            `${payload.rolls.join(" + ")}${mod} = ${payload.total} vs DC ${payload.dc}: ${
              payload.outcome
            }`
          );
          setPendingRoll(null);
        }
        if (event === "phase" && isPhasePayload(payload)) {
          if (payload.status === "utility_analyzing") {
            setPhase("checking");
            setStatusMessage("Utility model is preparing actions...");
          }
        }
        if (event === "narration_delta" && isContentPayload(payload)) {
          setPhase("generating");
          setStatusMessage("DM is resolving the result...");
          setDraft((text) => text + payload.content);
        }
        if (event === "choices_updated" && isChoiceUpdate(payload)) {
          setChoices(payload.choices);
          setDetail((current) =>
            current
              ? {
                  ...current,
                  choices: payload.choices,
                  world_state: {
                    ...current.world_state,
                    active_objective: payload.objective,
                    current_location: payload.location,
                    scene_summary: payload.summary,
                    choices_json: JSON.stringify(payload.choices)
                  }
                }
              : current
          );
        }
        if (event === "error" && isErrorPayload(payload)) {
          gotError = true;
          setPhase("error");
          setError(payload.message);
        }
      });
      await refreshDetail(activeId);
      await refreshStatus();
    } catch (err) {
      setError(String(err));
    } finally {
      setStreaming(false);
      setDraft("");
      if (!gotError) {
        setPhase("ready");
        setStatusMessage("Ready for your next move.");
      }
    }
  }

  return (
    <main className="app-shell">
      <Sidebar
        activeId={activeId}
        campaigns={campaigns}
        health={health}
        hasConfiguredChatModel={hasConfiguredChatModel}
        lore={lore}
        heroes={heroes}
        newSetting={newSetting}
        newTitle={newTitle}
        newTone={newTone}
        onCreateCampaign={() => setShowHeroPicker(true)}
        onCreateHero={createHero}
        onDeleteHero={deleteHero}
        onUpdateHero={updateHero}
        onRefreshStatus={refreshStatus}
        onSelectCampaign={setActiveId}
        onSetNewSetting={setNewSetting}
        onSetNewTitle={setNewTitle}
        onSetNewTone={setNewTone}
        onUploadLore={uploadLore}
        onRefreshLoreIndex={refreshLoreIndex}
      />
      <PlayScreen
        action={action}
        detail={detail}
        draft={draft}
        error={error}
        pendingRoll={pendingRoll}
        phase={phase}
        choices={choices}
        rollResult={rollResult}
        statusMessage={statusMessage}
        streaming={streaming}
        turnCount={turnCount}
        onQuickAction={submitQuickAction}
        onResolvePendingRoll={resolvePendingRoll}
        onSetAction={setAction}
        onSubmitAction={submitAction}
      />
      {showHeroPicker && (
        <HeroPickerModal
          heroes={heroes}
          lore={lore}
          creating={creatingCampaign}
          selectedLoreIds={selectedLoreIds}
          selectedHeroIds={selectedHeroIds}
          onCancel={() => setShowHeroPicker(false)}
          onConfirm={createCampaign}
          onToggleHero={(id) =>
            setSelectedHeroIds((current) =>
              current.includes(id) ? current.filter((heroId) => heroId !== id) : [...current, id]
            )
          }
          onToggleLore={(id) =>
            setSelectedLoreIds((current) =>
              current.includes(id) ? current.filter((loreId) => loreId !== id) : [...current, id]
            )
          }
        />
      )}
    </main>
  );
}

type HeroPayload = {
  name: string;
  ancestry: string;
  character_class: string;
  backstory: string;
  inventory: string[];
  is_human: boolean;
};

type SidebarProps = {
  activeId: number | null;
  campaigns: Campaign[];
  health: Health | null;
  hasConfiguredChatModel: boolean;
  heroes: Hero[];
  lore: LoreDocument[];
  newSetting: string;
  newTitle: string;
  newTone: string;
  onCreateCampaign: () => void;
  onCreateHero: (payload: HeroPayload) => void;
  onDeleteHero: (id: number) => void;
  onRefreshStatus: () => void;
  onSelectCampaign: (id: number) => void;
  onSetNewSetting: (value: string) => void;
  onSetNewTitle: (value: string) => void;
  onSetNewTone: (value: string) => void;
  onUpdateHero: (id: number, payload: HeroPayload) => void;
  onUploadLore: (file: File | null) => void;
  onRefreshLoreIndex: () => void;
};

function Sidebar(props: SidebarProps) {
  return (
    <aside className="sidebar">
      <div className="brand">
        <Dice5 size={24} />
        <span>DNDLLM26</span>
      </div>
      <SystemStatus
        hasConfiguredChatModel={props.hasConfiguredChatModel}
        health={props.health}
        onRefreshStatus={props.onRefreshStatus}
      />
      <CampaignCreator
        newSetting={props.newSetting}
        newTitle={props.newTitle}
        newTone={props.newTone}
        onCreateCampaign={props.onCreateCampaign}
        onSetNewSetting={props.onSetNewSetting}
        onSetNewTitle={props.onSetNewTitle}
        onSetNewTone={props.onSetNewTone}
      />
      <HeroManager
        heroes={props.heroes}
        onCreateHero={props.onCreateHero}
        onDeleteHero={props.onDeleteHero}
        onUpdateHero={props.onUpdateHero}
      />
      <div className="campaign-list">
        {props.campaigns.map((campaign) => (
          <button
            className={campaign.id === props.activeId ? "campaign active" : "campaign"}
            key={campaign.id}
            onClick={() => props.onSelectCampaign(campaign.id)}
          >
            <strong>{campaign.title}</strong>
            <span>{campaign.tone}</span>
          </button>
        ))}
      </div>
      <LorePanel
        lore={props.lore}
        onRefreshLoreIndex={props.onRefreshLoreIndex}
        onUploadLore={props.onUploadLore}
      />
    </aside>
  );
}

function SystemStatus({
  hasConfiguredChatModel,
  health,
  onRefreshStatus
}: {
  hasConfiguredChatModel: boolean;
  health: Health | null;
  onRefreshStatus: () => void;
}) {
  return (
    <section className="panel">
      <div className="panel-title">
        <HeartPulse size={16} />
        <span>Local System</span>
        <button className="icon-button" onClick={onRefreshStatus} title="Refresh status">
          <RefreshCw size={15} />
        </button>
      </div>
      <dl className="status-grid">
        <dt>Ollama</dt>
        <dd className={health?.ollama === "ok" ? "ok" : "bad"}>
          {health?.ollama ?? "checking"}
        </dd>
        <dt>Chat</dt>
        <dd>{health?.chat_model ?? "unknown"}</dd>
        <dt>Utility</dt>
        <dd>{health?.utility_model ?? "unknown"}</dd>
        <dt>Embed</dt>
        <dd>{health?.embed_model ?? "unknown"}</dd>
      </dl>
      {health && !hasConfiguredChatModel && (
        <p className="hint">Configured chat model is not in Ollama's local model list.</p>
      )}
    </section>
  );
}

function CampaignCreator({
  newSetting,
  newTitle,
  newTone,
  onCreateCampaign,
  onSetNewSetting,
  onSetNewTitle,
  onSetNewTone
}: {
  newSetting: string;
  newTitle: string;
  newTone: string;
  onCreateCampaign: () => void;
  onSetNewSetting: (value: string) => void;
  onSetNewTitle: (value: string) => void;
  onSetNewTone: (value: string) => void;
}) {
  return (
    <section className="panel">
      <div className="panel-title">
        <Plus size={16} />
        <span>New Campaign</span>
      </div>
      <input value={newTitle} onChange={(event) => onSetNewTitle(event.target.value)} />
      <textarea value={newSetting} onChange={(event) => onSetNewSetting(event.target.value)} />
      <input value={newTone} onChange={(event) => onSetNewTone(event.target.value)} />
      <button className="primary" onClick={onCreateCampaign}>
        <Plus size={18} /> Create
      </button>
    </section>
  );
}

function HeroManager({
  heroes,
  onCreateHero,
  onDeleteHero,
  onUpdateHero
}: {
  heroes: Hero[];
  onCreateHero: (payload: HeroPayload) => void;
  onDeleteHero: (id: number) => void;
  onUpdateHero: (id: number, payload: HeroPayload) => void;
}) {
  const emptyDraft: HeroPayload = {
    name: "",
    ancestry: "Human",
    character_class: "Fighter",
    backstory: "",
    inventory: ["torch", "rations", "dagger"],
    is_human: true
  };
  const [draft, setDraft] = useState<HeroPayload>(emptyDraft);
  const [editingId, setEditingId] = useState<number | null>(null);

  function edit(hero: Hero) {
    setEditingId(hero.id);
    setDraft({
      name: hero.name,
      ancestry: hero.ancestry,
      character_class: hero.character_class,
      backstory: hero.backstory,
      inventory: parseInventory(hero.inventory_json),
      is_human: hero.is_human
    });
  }

  async function save() {
    if (!draft.name.trim()) return;
    const payload = {
      ...draft,
      name: draft.name.trim(),
      backstory: draft.backstory.trim() || "An adventurer looking for a reason to risk everything.",
      inventory: draft.inventory.filter(Boolean)
    };
    if (editingId) {
      await onUpdateHero(editingId, payload);
    } else {
      await onCreateHero(payload);
    }
    setEditingId(null);
    setDraft(emptyDraft);
  }

  return (
    <section className="panel hero-manager">
      <div className="panel-title">
        <UserPlus size={16} />
        <span>Hero Party</span>
      </div>
      <div className="hero-list">
        {heroes.map((hero) => (
          <article className="hero-row" key={hero.id}>
            <div>
              <strong>{hero.name}</strong>
              <small>
                {hero.ancestry} {hero.character_class}
              </small>
            </div>
            <button className="icon-button" onClick={() => edit(hero)} title="Edit hero">
              <Edit3 size={14} />
            </button>
            <button className="icon-button" onClick={() => onDeleteHero(hero.id)} title="Delete hero">
              <Trash2 size={14} />
            </button>
          </article>
        ))}
      </div>
      <input
        placeholder="Hero name"
        value={draft.name}
        onChange={(event) => setDraft({ ...draft, name: event.target.value })}
      />
      <div className="hero-form-grid">
        <input
          placeholder="Ancestry"
          value={draft.ancestry}
          onChange={(event) => setDraft({ ...draft, ancestry: event.target.value })}
        />
        <input
          placeholder="Class"
          value={draft.character_class}
          onChange={(event) => setDraft({ ...draft, character_class: event.target.value })}
        />
      </div>
      <textarea
        placeholder="Backstory"
        value={draft.backstory}
        onChange={(event) => setDraft({ ...draft, backstory: event.target.value })}
      />
      <input
        placeholder="Inventory, comma separated"
        value={draft.inventory.join(", ")}
        onChange={(event) =>
          setDraft({
            ...draft,
            inventory: event.target.value.split(",").map((item) => item.trim())
          })
        }
      />
      <label className="check-row">
        <input
          type="checkbox"
          checked={draft.is_human}
          onChange={(event) => setDraft({ ...draft, is_human: event.target.checked })}
        />
        Human
      </label>
      <div className="hero-actions">
        {editingId && (
          <button
            className="secondary"
            onClick={() => {
              setEditingId(null);
              setDraft(emptyDraft);
            }}
          >
            <X size={16} /> Cancel
          </button>
        )}
        <button className="primary" onClick={save} disabled={!draft.name.trim()}>
          <Save size={16} /> {editingId ? "Save Hero" : "Add Hero"}
        </button>
      </div>
    </section>
  );
}

function LorePanel({
  lore,
  onRefreshLoreIndex,
  onUploadLore
}: {
  lore: LoreDocument[];
  onRefreshLoreIndex: () => void;
  onUploadLore: (file: File | null) => void;
}) {
  return (
    <>
      <label className="upload">
        <Upload size={18} />
        <span>Upload Lore PDF</span>
        <input
          type="file"
          accept="application/pdf"
          onChange={(event) => onUploadLore(event.target.files?.[0] ?? null)}
        />
      </label>
      <section className="panel compact">
        <div className="panel-title">
          <BookOpen size={16} />
          <span>Lore</span>
          <button className="icon-button" onClick={onRefreshLoreIndex} title="Refresh indexing">
            <RefreshCw size={15} />
          </button>
        </div>
        {lore.length === 0 && <p className="hint">No PDFs indexed yet.</p>}
        {lore.slice(0, 5).map((doc) => (
          <div className="lore-row" key={doc.id}>
            <span>{doc.filename}</span>
            <small>
              {doc.status} · {doc.chunks} chunks
            </small>
          </div>
        ))}
      </section>
    </>
  );
}

type PlayScreenProps = {
  action: string;
  detail: Detail | null;
  draft: string;
  error: string | null;
  pendingRoll: PendingRoll | null;
  phase: PlayPhase;
  choices: string[];
  rollResult: string;
  statusMessage: string;
  streaming: boolean;
  turnCount: number;
  onQuickAction: (text: string) => void;
  onResolvePendingRoll: () => void;
  onSetAction: (value: string) => void;
  onSubmitAction: (event: FormEvent) => void;
};

function PlayScreen(props: PlayScreenProps) {
  return (
    <section className="play">
      {props.error && <div className="error">{props.error}</div>}
      {!props.detail && <EmptyState />}
      {props.detail && (
        <>
          <CampaignHeader detail={props.detail} />
          <GameHud
            detail={props.detail}
            phase={props.phase}
            rollResult={props.rollResult}
            statusMessage={props.statusMessage}
            turnCount={props.turnCount}
          />
          <div className="game-layout">
            <div className="scene-column">
              {props.pendingRoll && (
                <RollPrompt
                  pendingRoll={props.pendingRoll}
                  streaming={props.streaming}
                  onResolvePendingRoll={props.onResolvePendingRoll}
                />
              )}
              <SceneViewport
                draft={props.draft}
                phase={props.phase}
                streaming={props.streaming}
                turns={props.detail.turns}
              />
              <QuickActions choices={props.choices} onQuickAction={props.onQuickAction} />
              <ActionComposer
                action={props.action}
                pendingRoll={props.pendingRoll}
                streaming={props.streaming}
                onSetAction={props.onSetAction}
                onSubmitAction={props.onSubmitAction}
              />
            </div>
            <aside className="right-rail">
              <WorldPanel detail={props.detail} />
              <PartyPanel characters={props.detail.characters} />
              <AdventureLog
                draft={props.draft}
                streaming={props.streaming}
                turns={props.detail.turns}
              />
            </aside>
          </div>
        </>
      )}
    </section>
  );
}

function EmptyState() {
  return (
    <div className="empty">
      <BookOpen size={38} />
      <h1>Create a campaign to begin</h1>
    </div>
  );
}

function HeroPickerModal({
  creating,
  heroes,
  lore,
  selectedLoreIds,
  selectedHeroIds,
  onCancel,
  onConfirm,
  onToggleHero,
  onToggleLore
}: {
  creating: boolean;
  heroes: Hero[];
  lore: LoreDocument[];
  selectedLoreIds: number[];
  selectedHeroIds: number[];
  onCancel: () => void;
  onConfirm: () => void;
  onToggleHero: (id: number) => void;
  onToggleLore: (id: number) => void;
}) {
  return (
    <div className="modal-backdrop">
      <section className="hero-picker">
        <div className="modal-head">
          <div>
            <span className="eyebrow">New Campaign</span>
            <h2>Select Heroes</h2>
          </div>
          <button className="icon-button" onClick={onCancel} disabled={creating} title="Close">
            <X size={16} />
          </button>
        </div>
        <div className="hero-picker-list">
          <div className="picker-section-title">Heroes</div>
          {heroes.map((hero) => (
            <button
              className={
                selectedHeroIds.includes(hero.id) ? "hero-select selected" : "hero-select"
              }
              key={hero.id}
              onClick={() => onToggleHero(hero.id)}
            >
              <strong>{hero.name}</strong>
              <span>
                {hero.ancestry} {hero.character_class}
              </span>
              <small>{hero.backstory}</small>
            </button>
          ))}
          {heroes.length === 0 && <p className="hint">Create a hero in the sidebar first.</p>}
          <div className="picker-section-title">Campaign Lore</div>
          {lore.map((doc) => (
            <button
              className={selectedLoreIds.includes(doc.id) ? "hero-select selected" : "hero-select"}
              disabled={doc.status !== "ready" || doc.chunks === 0}
              key={doc.id}
              onClick={() => onToggleLore(doc.id)}
            >
              <strong>{doc.filename}</strong>
              <span>
                {doc.status} · {doc.chunks} chunks
              </span>
              {doc.status !== "ready" && <small>Refresh indexing before selecting this file.</small>}
            </button>
          ))}
          {lore.length === 0 && <p className="hint">Upload or add PDFs in the Lore panel.</p>}
        </div>
        <div className="modal-actions">
          <button className="secondary" onClick={onCancel} disabled={creating}>
            <X size={16} /> Cancel
          </button>
          <button
            className="primary"
            onClick={onConfirm}
            disabled={creating || selectedHeroIds.length === 0}
          >
            {creating ? <LoaderCircle className="spin" size={16} /> : <Plus size={16} />}
            {creating ? "Building Intro..." : "Start Campaign"}
          </button>
        </div>
      </section>
    </div>
  );
}

function CampaignHeader({ detail }: { detail: Detail }) {
  return (
    <header className="campaign-header">
      <div>
        <h1>{detail.campaign.title}</h1>
        <p>{detail.campaign.setting}</p>
      </div>
      <span>{detail.campaign.tone}</span>
    </header>
  );
}

function GameHud({
  detail,
  phase,
  rollResult,
  statusMessage,
  turnCount
}: {
  detail: Detail;
  phase: PlayPhase;
  rollResult: string;
  statusMessage: string;
  turnCount: number;
}) {
  return (
    <div className="game-hud">
      <div className={`status-pill ${phase}`}>
        {phase === "ready" ? <Activity size={16} /> : <LoaderCircle className="spin" size={16} />}
        <strong>{statusMessage}</strong>
      </div>
      <div>
        <ScrollText size={16} />
        <strong>{turnCount}</strong>
        <span>turns</span>
      </div>
      <div>
        <Users size={16} />
        <strong>{detail.characters.length}</strong>
        <span>party</span>
      </div>
      <div>
        <MapPin size={16} />
        <strong>{detail.world_state.current_location}</strong>
        <span>location</span>
      </div>
      <div className="roll-status">
        <Dice5 size={16} />
        <strong>{rollResult}</strong>
        <span>last roll</span>
      </div>
    </div>
  );
}

function SceneViewport({
  draft,
  phase,
  streaming,
  turns
}: {
  draft: string;
  phase: PlayPhase;
  streaming: boolean;
  turns: Turn[];
}) {
  const featured = streaming && draft ? { speaker: "DM", content: draft } : latestSceneTurn(turns);
  return (
    <section className="scene-viewport">
      <div className="scene-header">
        <div>
          <span className="eyebrow">Current Scene</span>
          <h2>{featured?.speaker ?? "DM"}</h2>
        </div>
        <div className={`model-indicator ${phase}`}>
          {phase === "generating" || phase === "checking" || phase === "rolling" ? (
            <LoaderCircle className="spin" size={16} />
          ) : (
            <BrainCircuit size={16} />
          )}
          <span>{phaseLabel(phase)}</span>
        </div>
      </div>
      <div className="scene-content">
        {featured ? (
          formatSceneText(featured.content).map((paragraph, index) => (
            <p key={`${paragraph.slice(0, 20)}-${index}`}>{paragraph}</p>
          ))
        ) : (
          <p>Start by describing what your character does.</p>
        )}
      </div>
    </section>
  );
}

function RollPrompt({
  pendingRoll,
  streaming,
  onResolvePendingRoll
}: {
  pendingRoll: PendingRoll;
  streaming: boolean;
  onResolvePendingRoll: () => void;
}) {
  return (
    <section className="roll-overlay" aria-live="polite">
      <div className="roll-prompt">
        <div className="roll-emblem">
          <Dice5 size={30} />
        </div>
        <div className="roll-copy">
          <span className="eyebrow">Dice Check</span>
          <strong>
            {pendingRoll.ability}
            {pendingRoll.skill ? ` (${pendingRoll.skill})` : ""} check
          </strong>
          <p>{pendingRoll.reason}</p>
          {pendingRoll.narration && <small>{pendingRoll.narration}</small>}
        </div>
        <div className="roll-target">
          <span>{pendingRoll.formula}</span>
          <span>DC {pendingRoll.dc}</span>
        </div>
        <button onClick={onResolvePendingRoll} disabled={streaming}>
          <Dice5 size={20} />
          Roll Dice
        </button>
      </div>
    </section>
  );
}

function PartyPanel({ characters }: { characters: Character[] }) {
  return (
    <section className="rail-panel">
      <div className="rail-title">
        <Shield size={16} />
        <span>Party</span>
      </div>
      <div className="party">
        {characters.map((character) => (
          <article key={character.id}>
            <Shield size={18} />
            <h2>{character.name}</h2>
            <p>
              {character.ancestry} {character.character_class}
            </p>
            <small>{character.backstory}</small>
          </article>
        ))}
      </div>
    </section>
  );
}

function WorldPanel({ detail }: { detail: Detail }) {
  return (
    <section className="rail-panel">
      <div className="rail-title">
        <MapPin size={16} />
        <span>World</span>
      </div>
      <dl className="world-grid">
        <dt>Location</dt>
        <dd>{detail.world_state.current_location}</dd>
        <dt>Objective</dt>
        <dd>{detail.world_state.active_objective}</dd>
        <dt>Scene</dt>
        <dd>{detail.world_state.scene_summary || detail.campaign.setting}</dd>
      </dl>
    </section>
  );
}

function QuickActions({
  choices,
  onQuickAction
}: {
  choices: string[];
  onQuickAction: (text: string) => void;
}) {
  const fallback = [
    "Ask around for rumors about the sealed ruins.",
    "Look for a safe tavern and listen for trouble.",
    "Inspect the nearest old oath-marker for magical signs.",
    "Find a guard, guild contact, or local guide."
  ];
  const cleanChoices = choices.map(cleanChoiceText).filter(Boolean);
  const actions = cleanChoices.length ? cleanChoices : fallback;
  return (
    <section className="quick-actions">
      <div className="quick-title">
        <Sparkles size={16} />
        <span>{cleanChoices.length ? "Player Choices" : "Suggested Actions"}</span>
      </div>
      {actions.map((text) => (
        <button key={text} onClick={() => onQuickAction(text)}>
          <Sparkles size={15} />
          {text}
        </button>
      ))}
    </section>
  );
}

function AdventureLog({
  draft,
  streaming,
  turns
}: {
  draft: string;
  streaming: boolean;
  turns: Turn[];
}) {
  const [expandedId, setExpandedId] = useState<number | "draft" | null>(null);
  return (
    <section className="rail-panel timeline-panel">
      <div className="rail-title">
        <ScrollText size={16} />
        <span>Timeline</span>
      </div>
      <div className="log">
        {turns.slice(-8).map((turn) => (
          <button
            className={
              expandedId === turn.id
                ? `timeline-entry ${turn.speaker.toLowerCase()} expanded`
                : `timeline-entry ${turn.speaker.toLowerCase()}`
            }
            key={turn.id}
            onClick={() => setExpandedId(expandedId === turn.id ? null : turn.id)}
          >
            <span>
              {turn.speaker === "DM" && <CheckCircle2 size={15} />}
              {turn.speaker}
            </span>
            <p>{expandedId === turn.id ? turn.content : summarizeTurn(turn.content)}</p>
          </button>
        ))}
        {streaming && draft && (
          <button
            className={expandedId === "draft" ? "timeline-entry dm expanded" : "timeline-entry dm"}
            onClick={() => setExpandedId(expandedId === "draft" ? null : "draft")}
          >
            <span>DM</span>
            <p>{draft}</p>
          </button>
        )}
      </div>
    </section>
  );
}

function ActionComposer({
  action,
  pendingRoll,
  streaming,
  onSetAction,
  onSubmitAction
}: {
  action: string;
  pendingRoll: PendingRoll | null;
  streaming: boolean;
  onSetAction: (value: string) => void;
  onSubmitAction: (event: FormEvent) => void;
}) {
  return (
    <form className="action-bar" onSubmit={onSubmitAction}>
      <input
        value={action}
        onChange={(event) => onSetAction(event.target.value)}
        placeholder={
          pendingRoll
            ? "Resolve the dice check to continue..."
            : "Describe what your character does..."
        }
        disabled={streaming || Boolean(pendingRoll)}
      />
      <button type="submit" disabled={streaming || Boolean(pendingRoll) || !action.trim()}>
        <Send size={18} />
      </button>
    </form>
  );
}

function latestSceneTurn(turns: Turn[]): Pick<Turn, "speaker" | "content"> | null {
  return (
    [...turns].reverse().find((turn) => turn.speaker === "DM") ??
    [...turns].reverse().find((turn) => turn.speaker !== "System") ??
    null
  );
}

function formatSceneText(content: string): string[] {
  const withoutChoices = content.split(/\n\s*(?:choices|what do you do)[\?:]?\s*\n/i)[0] ?? content;
  const withoutMeta = withoutChoices
    .replace(/^\s*(?:here(?:'s| is)|this is|a possible|possible)\b[^:\n]*:\s*/i, "")
    .split(/\n?\s*(?:this message establishes|the message establishes|it establishes)\b/i)[0];
  return withoutMeta
    .replace(/\*\*/g, "")
    .replace(/#{1,6}\s/g, "")
    .replace(/---+/g, "")
    .replace(/^\s*\d+[\).:]\s+.*$/gm, "")
    .split(/\n{2,}|\r?\n(?=\d+\. )/)
    .map((part) => part.trim())
    .filter(Boolean);
}

function phaseLabel(phase: PlayPhase): string {
  if (phase === "checking") return "Judging action";
  if (phase === "generating") return "DM generating";
  if (phase === "roll_required") return "Roll required";
  if (phase === "rolling") return "Resolving roll";
  if (phase === "error") return "Needs attention";
  return "Ready";
}

function isContentPayload(payload: unknown): payload is { content: string } {
  return typeof payload === "object" && payload !== null && "content" in payload;
}

function isErrorPayload(payload: unknown): payload is { message: string } {
  return typeof payload === "object" && payload !== null && "message" in payload;
}

function isPendingRoll(payload: unknown): payload is PendingRoll {
  return (
    typeof payload === "object" &&
    payload !== null &&
    "id" in payload &&
    "formula" in payload &&
    "dc" in payload
  );
}

function isRollResult(payload: unknown): payload is {
  rolls: number[];
  modifier: number;
  total: number;
  dc: number;
  outcome: string;
} {
  return (
    typeof payload === "object" &&
    payload !== null &&
    "rolls" in payload &&
    "total" in payload &&
    "outcome" in payload
  );
}

function isPhasePayload(payload: unknown): payload is { status: string } {
  return typeof payload === "object" && payload !== null && "status" in payload;
}

function isChoiceUpdate(payload: unknown): payload is ChoiceUpdate {
  return (
    typeof payload === "object" &&
    payload !== null &&
    "choices" in payload &&
    Array.isArray((payload as ChoiceUpdate).choices)
  );
}

function choicesFromState(state: WorldState): string[] {
  try {
    const value = JSON.parse(state.choices_json);
    return Array.isArray(value) ? value.map(cleanChoiceText).filter(Boolean) : [];
  } catch {
    return [];
  }
}

function cleanChoiceText(value: unknown): string {
  if (typeof value === "object" && value !== null) {
    const record = value as Record<string, unknown>;
    return cleanChoiceText(record.action ?? record.choice ?? record.text ?? record.label ?? "");
  }
  let text = String(value ?? "").trim();
  const objectMatch = text.match(/["']?(?:action|choice|text|label)["']?\s*:\s*["'](.+?)["']\s*[},]?$/i);
  if (objectMatch) text = objectMatch[1];
  return text
    .replace(/^\s*(?:\d+[\).:]|-|\*)\s+/, "")
    .replace(/^\{+|\}+$/g, "")
    .replace(/^['"]+|['"]+$/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function parseInventory(value: string): string[] {
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed.map((item) => String(item)) : [];
  } catch {
    return [];
  }
}

function summarizeTurn(content: string): string {
  const clean = formatSceneText(content).join(" ");
  return clean.length > 110 ? `${clean.slice(0, 110).trim()}...` : clean;
}

export default App;
