import { FormEvent, useEffect, useMemo, useState, useRef } from "react";
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
  X,
  Coins,
  Heart,
  Package
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
  streamTurn,
  GameSession,
  CharacterStatus,
  streamSessionTurn,
  resolveSessionRoll,
  useSessionItem
} from "./lib/api";
import { TRANSLATIONS } from "./lib/i18n";
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
  const [sessions, setSessions] = useState<GameSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<number | null>(null);
  const [toasts, setToasts] = useState<{ id: string; message: string; type: "success" | "info" | "warning" }[]>([]);
  
  const showToast = (message: string, type: "success" | "info" | "warning" = "info") => {
    const id = Math.random().toString(36).substring(2, 9);
    setToasts((prev) => [...prev, { id, message, type }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 4000);
  };

  const handleStatusUpdates = (event: string, payload: any) => {
    if (event === "status_updates" && payload && typeof payload === "object" && "updates" in payload) {
      const updates = payload.updates;
      if (Array.isArray(updates)) {
        for (const up of updates) {
          if (up.xp_gained > 0) showToast(`🌟 +${up.xp_gained} XP para ${up.name}!`, "success");
          if (up.gold_gained !== 0) showToast(`🪙 ${up.gold_gained > 0 ? "+" : ""}${up.gold_gained} Ouro para ${up.name}!`, "success");
          if (up.hp_change !== 0) showToast(`${up.hp_change > 0 ? "❤️ +" : "💔 "}${up.hp_change} HP para ${up.name}!`, up.hp_change > 0 ? "success" : "warning");
          if (up.level_up) showToast(`🎉 LEVEL UP! ${up.name} subiu para o nível ${up.level}!`, "success");
          if (Array.isArray(up.items_added)) {
            up.items_added.forEach((item: any) => showToast(`🎒 Item recebido: ${item.name}!`, "success"));
          }
          if (Array.isArray(up.items_removed)) {
            up.items_removed.forEach((item: any) => showToast(`🗑️ Item perdido: ${item.name}!`, "warning"));
          }
        }
      }
    }
  };
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
  const [rollResult, setRollResult] = useState<string>("Pronto");
  const [pendingRoll, setPendingRoll] = useState<PendingRoll | null>(null);
  const [choices, setChoices] = useState<string[]>([]);
  const [phase, setPhase] = useState<PlayPhase>("ready");
  const [statusMessage, setStatusMessage] = useState(TRANSLATIONS.readyMove);
  
  // New tone, setting and title default values in Portuguese
  const [newTitle, setNewTitle] = useState("O Portal Despedaçado");
  const [newSetting, setNewSetting] = useState(
    "Uma cidade fronteiriça construída sobre ruínas seladas onde juramentos antigos estão falhando."
  );
  const [newTone, setNewTone] = useState("fantasia heroica tensa");

  // State for AI Companions (Épico 2)
  const [aiCompanionsCount, setAiCompanionsCount] = useState<number>(0);
  const [aiCompanionsClasses, setAiCompanionsClasses] = useState<string[]>([]);

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

  async function refreshSessions() {
    const rows = await getJson<GameSession[]>("/sessions");
    setSessions(rows);
  }

  async function refreshDetail(id: number) {
    if (activeSessionId) {
      try {
        const next = await getJson<{
          session: GameSession;
          campaign: Campaign;
          characters: CharacterStatus[];
          turns: Turn[];
          choices: string[];
          pending_roll?: PendingRoll | null;
        }>(`/sessions/${activeSessionId}`);
        setDetail({
          campaign: next.campaign,
          characters: next.characters.map((c) => ({
            id: c.character_id,
            campaign_id: next.campaign.id,
            name: c.name,
            ancestry: c.ancestry,
            character_class: c.character_class,
            backstory: c.backstory,
            inventory_json: c.inventory,
            is_human: c.is_human,
            hp: c.hp,
            max_hp: c.max_hp,
            level: c.level,
            xp: c.xp,
            gold: c.gold,
            inventory: c.inventory
          })),
          turns: next.turns,
          world_state: {
            id: next.session.id!,
            campaign_id: next.session.campaign_id,
            current_location: next.session.current_location,
            active_objective: next.session.active_objective,
            scene_summary: next.session.scene_summary,
            choices_json: next.session.choices_json
          },
          choices: next.choices,
          pending_roll: next.pending_roll ?? null
        });
        setChoices(next.choices);
        setPendingRoll(next.pending_roll ?? null);
      } catch (err) {
        setError(String(err));
      }
    } else {
      const next = await getJson<Detail>(`/campaigns/${id}`);
      setDetail(next);
      setChoices(next.choices ?? choicesFromState(next.world_state));
      setPendingRoll(next.pending_roll ?? null);
    }
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
    refreshSessions().catch((err) => setError(String(err)));
    refreshStatus().catch((err) => setError(String(err)));
  }, []);

  useEffect(() => {
    if (activeSessionId) {
      const sess = sessions.find((s) => s.id === activeSessionId);
      if (sess) {
        setActiveId(sess.campaign_id);
        refreshDetail(sess.campaign_id).catch((err) => setError(String(err)));
      }
    } else if (activeId) {
      refreshDetail(activeId).catch((err) => setError(String(err)));
    }
  }, [activeId, activeSessionId, sessions]);

  async function handleCreateSession(campaignId: number, name: string, lorePack: string | null) {
    try {
      const sess = await postJson<GameSession>("/sessions", { campaign_id: campaignId, name, lore_pack: lorePack });
      await refreshSessions();
      setActiveSessionId(sess.id!);
    } catch (err) {
      setError(String(err));
    }
  }

  async function handleDeleteSession(id: number) {
    try {
      await fetch(`${API_BASE}/sessions/${id}`, { method: "DELETE" }).then(async (res) => {
        if (!res.ok) throw new Error(await res.text());
      });
      if (activeSessionId === id) {
        setActiveSessionId(null);
      }
      await refreshSessions();
    } catch (err) {
      setError(String(err));
    }
  }

  async function handleUseItem(characterId: number, itemId: string, itemName: string) {
    if (!activeSessionId) return;
    try {
      const res = await useSessionItem(activeSessionId, characterId, itemId);
      showToast(`🎒 Usou ${itemName}!${res.healing_applied > 0 ? ` (+${res.healing_applied} HP)` : ""}`, "success");
      if (activeId) {
        await refreshDetail(activeId);
      }
    } catch (err) {
      showToast(`⚠️ Erro ao usar item: ${err}`, "warning");
    }
  }

  async function createCampaign() {
    setCreatingCampaign(true);
    try {
      const campaign = await postJson<Campaign>("/campaigns", {
        title: newTitle,
        setting: newSetting,
        tone: newTone,
        hero_ids: selectedHeroIds,
        lore_document_ids: selectedLoreIds,
        ai_companions_count: aiCompanionsCount,
        ai_companions_classes: aiCompanionsClasses
      });
      const campaignId = Number(campaign.id);
      if (!Number.isFinite(campaignId)) {
        throw new Error("A criação da campanha não retornou um ID válido.");
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
    setStatusMessage(TRANSLATIONS.judgingAction);
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
      const handleEvent = (event: string, payload: unknown) => {
        handleStatusUpdates(event, payload);
        if (event === "narration_delta" && isContentPayload(payload)) {
          setPhase("generating");
          setStatusMessage(TRANSLATIONS.generatingScene);
          setDraft((text) => text + payload.content);
        }
        if (event === "narration" && isContentPayload(payload)) {
          setDraft((text) => text + payload.content);
        }
        if (event === "phase" && isPhasePayload(payload)) {
          if (payload.status === "utility_analyzing") {
            setPhase("checking");
            setStatusMessage(TRANSLATIONS.preparingActions);
          }
        }
        if (event === "roll_required" && isPendingRoll(payload)) {
          gotRollPrompt = true;
          setPhase("roll_required");
          setStatusMessage(TRANSLATIONS.rollRequired);
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
      };

      if (activeSessionId) {
        await streamSessionTurn(activeSessionId, playerText, handleEvent);
      } else {
        await streamTurn(activeId, playerText, handleEvent);
      }
      
      await refreshDetail(activeId);
      await refreshStatus();
    } catch (err) {
      setError(String(err));
    } finally {
      setStreaming(false);
      setDraft("");
      if (gotRollPrompt) {
        setPhase("roll_required");
        setStatusMessage(TRANSLATIONS.rollRequired);
      } else if (!gotError) {
        setPhase("ready");
        setStatusMessage(TRANSLATIONS.readyMove);
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
    setRollResult("Rolando...");
    setPhase("rolling");
    setStatusMessage(TRANSLATIONS.rollingCheck);
    let gotError = false;
    try {
      const handleEvent = (event: string, payload: unknown) => {
        handleStatusUpdates(event, payload);
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
            setStatusMessage(TRANSLATIONS.preparingActions);
          }
        }
        if (event === "narration_delta" && isContentPayload(payload)) {
          setPhase("generating");
          setStatusMessage("Mestre está resolvendo o resultado...");
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
      };

      if (activeSessionId) {
        await resolveSessionRoll(activeSessionId, pendingRoll.id, handleEvent);
      } else {
        await resolveRoll(activeId, pendingRoll.id, handleEvent);
      }

      await refreshDetail(activeId);
      await refreshStatus();
    } catch (err) {
      setError(String(err));
    } finally {
      setStreaming(false);
      setDraft("");
      if (!gotError) {
        setPhase("ready");
        setStatusMessage(TRANSLATIONS.readyMove);
      }
    }
  }

  return (
    <main className="app-shell">
      <div className="toasts-container" style={{
        position: "fixed",
        top: "20px",
        right: "20px",
        zIndex: 9999,
        display: "flex",
        flexDirection: "column",
        gap: "10px",
        pointerEvents: "none"
      }}>
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={`toast ${toast.type}`}
            style={{
              padding: "12px 20px",
              borderRadius: "8px",
              background: toast.type === "success" 
                ? "rgba(16, 185, 129, 0.95)" 
                : toast.type === "warning" 
                  ? "rgba(239, 68, 68, 0.95)" 
                  : "rgba(31, 41, 55, 0.95)",
              color: "#fff",
              backdropFilter: "blur(8px)",
              boxShadow: "0 4px 12px rgba(0, 0, 0, 0.15)",
              border: "1px solid rgba(255, 255, 255, 0.1)",
              fontSize: "0.9rem",
              fontWeight: 500,
              pointerEvents: "auto",
              minWidth: "250px",
              maxWidth: "350px",
              transition: "all 0.3s ease",
            }}
          >
            {toast.message}
          </div>
        ))}
      </div>
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
        onSelectCampaign={(id) => {
          setActiveSessionId(null);
          setActiveId(id);
        }}
        onSetNewSetting={setNewSetting}
        onSetNewTitle={setNewTitle}
        onSetNewTone={setNewTone}
        onUploadLore={uploadLore}
        onRefreshLoreIndex={refreshLoreIndex}
        sessions={sessions}
        activeSessionId={activeSessionId}
        onSelectSession={setActiveSessionId}
        onCreateSession={handleCreateSession}
        onDeleteSession={handleDeleteSession}
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
        activeSessionId={activeSessionId}
        onQuickAction={submitQuickAction}
        onResolvePendingRoll={resolvePendingRoll}
        onSetAction={setAction}
        onSubmitAction={submitAction}
        onUseItem={handleUseItem}
      />
      {showHeroPicker && (
        <HeroPickerModal
          heroes={heroes}
          lore={lore}
          creating={creatingCampaign}
          selectedLoreIds={selectedLoreIds}
          selectedHeroIds={selectedHeroIds}
          aiCompanionsCount={aiCompanionsCount}
          aiCompanionsClasses={aiCompanionsClasses}
          setAiCompanionsCount={setAiCompanionsCount}
          setAiCompanionsClasses={setAiCompanionsClasses}
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
  sessions: GameSession[];
  activeSessionId: number | null;
  onSelectSession: (id: number) => void;
  onCreateSession: (campaignId: number, name: string, lorePack: string | null) => void;
  onDeleteSession: (id: number) => void;
};

export function SessionsPanel({
  sessions,
  activeSessionId,
  campaigns,
  onSelectSession,
  onCreateSession,
  onDeleteSession
}: {
  sessions: GameSession[];
  activeSessionId: number | null;
  campaigns: Campaign[];
  onSelectSession: (id: number) => void;
  onCreateSession: (campaignId: number, name: string, lorePack: string | null) => void;
  onDeleteSession: (id: number) => void;
}) {
  const [newSessionName, setNewSessionName] = useState("");
  const [selectedCampaignId, setSelectedCampaignId] = useState<number | "">("");
  const [lorePacks, setLorePacks] = useState<{ id: string; name: string; description: string }[]>([]);
  const [selectedLorePackId, setSelectedLorePackId] = useState<string>("");

  useEffect(() => {
    getJson<{ id: string; name: string; description: string }[]>("/lore/packs")
      .then(setLorePacks)
      .catch(console.error);
  }, []);

  useEffect(() => {
    if (campaigns.length && selectedCampaignId === "") {
      setSelectedCampaignId(campaigns[0].id);
    }
  }, [campaigns, selectedCampaignId]);

  const handleCreate = () => {
    if (!newSessionName.trim() || selectedCampaignId === "") return;
    onCreateSession(Number(selectedCampaignId), newSessionName.trim(), selectedLorePackId || null);
    setNewSessionName("");
  };

  const selectedPack = lorePacks.find((p) => p.id === selectedLorePackId);

  return (
    <section className="panel" style={{ borderTop: "1px solid rgba(255, 255, 255, 0.1)", paddingTop: "1rem" }}>
      <div className="panel-title">
        <ScrollText size={16} />
        <span>Jogos Salvos (Saves)</span>
      </div>
      <div className="session-creator-form" style={{ display: "flex", flexDirection: "column", gap: "0.5rem", marginBottom: "1rem" }}>
        <input
          style={{ padding: "0.4rem 0.6rem", fontSize: "0.85rem", borderRadius: "4px", backgroundColor: "#1c1c1e", color: "#fff", border: "1px solid rgba(255,255,255,0.15)" }}
          placeholder="Nome da Sessão / Save"
          value={newSessionName}
          onChange={(e) => setNewSessionName(e.target.value)}
        />
        <select
          style={{ padding: "0.4rem 0.6rem", fontSize: "0.85rem", borderRadius: "4px", backgroundColor: "#1c1c1e", color: "#fff", border: "1px solid rgba(255,255,255,0.15)" }}
          value={selectedCampaignId}
          onChange={(e) => setSelectedCampaignId(Number(e.target.value))}
        >
          {campaigns.map((c) => (
            <option key={c.id} value={c.id}>
              {c.title}
            </option>
          ))}
        </select>
        <select
          aria-label="Cenário da Campanha"
          style={{ padding: "0.4rem 0.6rem", fontSize: "0.85rem", borderRadius: "4px", backgroundColor: "#1c1c1e", color: "#fff", border: "1px solid rgba(255,255,255,0.15)" }}
          value={selectedLorePackId}
          onChange={(e) => setSelectedLorePackId(e.target.value)}
        >
          <option value="">Cenário Padrão (Sem Lore Pack)</option>
          {lorePacks.map((pack) => (
            <option key={pack.id} value={pack.id}>
              {pack.name}
            </option>
          ))}
        </select>
        {selectedPack && (
          <div className="lore-pack-description-card" style={{
            background: "rgba(255, 255, 255, 0.05)",
            border: "1px solid rgba(255, 255, 255, 0.1)",
            borderRadius: "6px",
            padding: "8px 12px",
            fontSize: "0.75rem",
            color: "rgba(255, 255, 255, 0.8)",
            marginTop: "2px",
            marginBottom: "4px"
          }}>
            <strong style={{ display: "block", color: "#fff", marginBottom: "3px" }}>{selectedPack.name}</strong>
            {selectedPack.description}
          </div>
        )}
        <button
          className="primary"
          onClick={handleCreate}
          disabled={!newSessionName.trim() || selectedCampaignId === ""}
          style={{ padding: "0.4rem", display: "flex", alignItems: "center", justifyContent: "center", gap: "0.25rem" }}
        >
          <Plus size={14} /> Salvar & Iniciar
        </button>
      </div>

      <div className="campaign-list" style={{ maxHeight: "150px", overflowY: "auto", display: "flex", flexDirection: "column", gap: "0.25rem" }}>
        {sessions.map((sess) => {
          const campaign = campaigns.find((c) => c.id === sess.campaign_id);
          return (
            <div
              key={sess.id}
              className={sess.id === activeSessionId ? "campaign active" : "campaign"}
              style={{ display: "flex", justifyContent: "space-between", alignItems: "center", width: "100%", padding: "0.4rem 0.6rem", borderRadius: "6px", cursor: "pointer" }}
            >
              <button
                onClick={() => onSelectSession(sess.id!)}
                style={{ background: "none", border: "none", color: "inherit", textAlign: "left", flexGrow: 1, padding: 0, cursor: "pointer" }}
              >
                <strong style={{ display: "block", fontSize: "0.9rem" }}>{sess.name}</strong>
                <span style={{ fontSize: "0.75rem", opacity: 0.7 }}>
                  {campaign ? campaign.title : `Campanha #${sess.campaign_id}`}
                </span>
              </button>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onDeleteSession(sess.id!);
                }}
                title="Excluir Jogo"
                style={{ background: "none", border: "none", color: "#ff4d4d", cursor: "pointer", padding: "0.25rem", display: "flex", alignItems: "center" }}
              >
                <Trash2 size={14} />
              </button>
            </div>
          );
        })}
        {sessions.length === 0 && <p style={{ fontSize: "0.8rem", opacity: 0.5, fontStyle: "italic", margin: "0.5rem 0" }}>Nenhum jogo salvo. Crie um acima!</p>}
      </div>
    </section>
  );
}

function Sidebar(props: SidebarProps) {
  return (
    <aside className="sidebar">
      <div className="brand">
        <Dice5 size={24} />
        <span>{TRANSLATIONS.appName}</span>
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
      
      <SessionsPanel
        sessions={props.sessions}
        activeSessionId={props.activeSessionId}
        campaigns={props.campaigns}
        onSelectSession={props.onSelectSession}
        onCreateSession={props.onCreateSession}
        onDeleteSession={props.onDeleteSession}
      />

      <div className="campaign-list" style={{ borderTop: "1px solid rgba(255, 255, 255, 0.1)", paddingTop: "1rem" }}>
        <div className="panel-title" style={{ paddingLeft: "0.5rem", marginBottom: "0.5rem" }}>
          <span>Lista de Campanhas</span>
        </div>
        {props.campaigns.map((campaign) => (
          <button
            className={campaign.id === props.activeId && !props.activeSessionId ? "campaign active" : "campaign"}
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
        <span>{TRANSLATIONS.localSystem}</span>
        <button className="icon-button" onClick={onRefreshStatus} title="Atualizar status">
          <RefreshCw size={15} />
        </button>
      </div>
      <dl className="status-grid">
        <dt>Ollama</dt>
        <dd className={health?.ollama === "ok" ? "ok" : "bad"}>
          {health?.ollama ?? "verificando"}
        </dd>
        <dt>{TRANSLATIONS.chat}</dt>
        <dd>{health?.chat_model ?? "desconhecido"}</dd>
        <dt>{TRANSLATIONS.utility}</dt>
        <dd>{health?.utility_model ?? "desconhecido"}</dd>
        <dt>{TRANSLATIONS.embed}</dt>
        <dd>{health?.embed_model ?? "desconhecido"}</dd>
      </dl>
      {health && !hasConfiguredChatModel && (
        <p className="hint">{TRANSLATIONS.warningChatModel}</p>
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
        <span>{TRANSLATIONS.newCampaign}</span>
      </div>
      <input value={newTitle} onChange={(event) => onSetNewTitle(event.target.value)} />
      <textarea value={newSetting} onChange={(event) => onSetNewSetting(event.target.value)} />
      <input value={newTone} onChange={(event) => onSetNewTone(event.target.value)} />
      <button className="primary" onClick={onCreateCampaign}>
        <Plus size={18} /> {TRANSLATIONS.create}
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
    ancestry: "Humano",
    character_class: "Guerreiro",
    backstory: "",
    inventory: ["tocha", "rações", "adaga"],
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
      backstory: draft.backstory.trim() || "Um aventureiro procurando uma razão para arriscar tudo.",
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
        <span>{TRANSLATIONS.heroParty}</span>
      </div>
      <div className="hero-list">
        {heroes.map((hero) => (
          <article className="hero-row" key={hero.id}>
            <div>
              <strong>{hero.name}</strong>
              <small>
                {hero.ancestry} {hero.character_class} {hero.is_human ? "" : "(IA)"}
              </small>
            </div>
            <button className="icon-button" onClick={() => edit(hero)} title="Editar herói">
              <Edit3 size={14} />
            </button>
            <button className="icon-button" onClick={() => onDeleteHero(hero.id)} title="Excluir herói">
              <Trash2 size={14} />
            </button>
          </article>
        ))}
      </div>
      <input
        placeholder={TRANSLATIONS.heroName}
        value={draft.name}
        onChange={(event) => setDraft({ ...draft, name: event.target.value })}
      />
      <div className="hero-form-grid">
        <input
          placeholder={TRANSLATIONS.ancestry}
          value={draft.ancestry}
          onChange={(event) => setDraft({ ...draft, ancestry: event.target.value })}
        />
        <input
          placeholder={TRANSLATIONS.characterClass}
          value={draft.character_class}
          onChange={(event) => setDraft({ ...draft, character_class: event.target.value })}
        />
      </div>
      <textarea
        placeholder={TRANSLATIONS.backstory}
        value={draft.backstory}
        onChange={(event) => setDraft({ ...draft, backstory: event.target.value })}
      />
      <input
        placeholder={TRANSLATIONS.inventoryPlaceholder}
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
        {TRANSLATIONS.human}
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
            <X size={16} /> {TRANSLATIONS.cancel}
          </button>
        )}
        <button className="primary" onClick={save} disabled={!draft.name.trim()}>
          <Save size={16} /> {editingId ? TRANSLATIONS.saveHero : TRANSLATIONS.addHero}
        </button>
      </div>
    </section>
  );
}

export function LorePanel({
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
        <span>{TRANSLATIONS.uploadLore}</span>
        <input
          type="file"
          accept="application/pdf,text/plain"
          onChange={(event) => onUploadLore(event.target.files?.[0] ?? null)}
        />
      </label>
      <section className="panel compact">
        <div className="panel-title">
          <BookOpen size={16} />
          <span>{TRANSLATIONS.lore}</span>
          <button className="icon-button" onClick={onRefreshLoreIndex} title="Atualizar índice">
            <RefreshCw size={15} />
          </button>
        </div>
        {lore.length === 0 && <p className="hint">{TRANSLATIONS.noLores}</p>}
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
  activeSessionId: number | null;
  onQuickAction: (text: string) => void;
  onResolvePendingRoll: () => void;
  onSetAction: (value: string) => void;
  onSubmitAction: (event: FormEvent) => void;
  onUseItem: (characterId: number, itemId: string, itemName: string) => void;
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
              <PartyStatusPanel
                characters={props.detail.characters}
                activeSessionId={props.activeSessionId}
                onUseItem={props.onUseItem}
              />
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
      <h1>{TRANSLATIONS.emptyState}</h1>
    </div>
  );
}

function HeroPickerModal({
  creating,
  heroes,
  lore,
  selectedLoreIds,
  selectedHeroIds,
  aiCompanionsCount,
  aiCompanionsClasses,
  setAiCompanionsCount,
  setAiCompanionsClasses,
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
  aiCompanionsCount: number;
  aiCompanionsClasses: string[];
  setAiCompanionsCount: (count: number) => void;
  setAiCompanionsClasses: (classes: string[] | ((prev: string[]) => string[])) => void;
  onCancel: () => void;
  onConfirm: () => void;
  onToggleHero: (id: number) => void;
  onToggleLore: (id: number) => void;
}) {
  const handleCompanionClassChange = (index: number, value: string) => {
    setAiCompanionsClasses((prev) => {
      const next = [...prev];
      next[index] = value;
      return next;
    });
  };

  return (
    <div className="modal-backdrop">
      <section className="hero-picker">
        <div className="modal-head">
          <div>
            <span className="eyebrow">{TRANSLATIONS.newCampaign}</span>
            <h2>{TRANSLATIONS.selectHeroes}</h2>
          </div>
          <button className="icon-button" onClick={onCancel} disabled={creating} title={TRANSLATIONS.cancel}>
            <X size={16} />
          </button>
        </div>
        <div className="hero-picker-list">
          <div className="picker-section-title">{TRANSLATIONS.heroParty}</div>
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
          {heroes.length === 0 && <p className="hint">{TRANSLATIONS.noHeroesHint}</p>}

          <div className="picker-section-title">{TRANSLATIONS.aiCompanionsTitle}</div>
          <div style={{ padding: "0.75rem 1rem", display: "flex", flexDirection: "column", gap: "0.75rem", border: "1px solid rgba(255,255,255,0.1)", borderRadius: "8px", marginBottom: "1rem", backgroundColor: "rgba(0,0,0,0.2)" }}>
            <label style={{ display: "flex", justifyContent: "space-between", alignItems: "center", width: "100%" }}>
              <span>{TRANSLATIONS.aiCompanionsCount}</span>
              <select
                style={{ padding: "0.25rem 0.5rem", borderRadius: "4px", backgroundColor: "#1e1e1e", color: "#fff", border: "1px solid rgba(255,255,255,0.2)" }}
                value={aiCompanionsCount}
                onChange={(e) => {
                  const count = Number(e.target.value);
                  setAiCompanionsCount(count);
                  setAiCompanionsClasses((prev) => {
                    const next = [...prev];
                    while (next.length < count) next.push("Guerreiro");
                    return next.slice(0, count);
                  });
                }}
              >
                <option value={0}>0</option>
                <option value={1}>1</option>
                <option value={2}>2</option>
                <option value={3}>3</option>
              </select>
            </label>
            
            {Array.from({ length: aiCompanionsCount }).map((_, idx) => (
              <label key={idx} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", width: "100%" }}>
                <span>{TRANSLATIONS.companionClass} {idx + 1}:</span>
                <select
                  style={{ padding: "0.25rem 0.5rem", borderRadius: "4px", backgroundColor: "#1e1e1e", color: "#fff", border: "1px solid rgba(255,255,255,0.2)" }}
                  value={aiCompanionsClasses[idx] || "Guerreiro"}
                  onChange={(e) => handleCompanionClassChange(idx, e.target.value)}
                >
                  <option value="Guerreiro">Guerreiro</option>
                  <option value="Mago">Mago</option>
                  <option value="Clérigo">Clérigo</option>
                  <option value="Ladino">Ladino</option>
                  <option value="Paladino">Paladino</option>
                  <option value="Bárbaro">Bárbaro</option>
                  <option value="Bardo">Bardo</option>
                  <option value="Patrulheiro">Patrulheiro</option>
                </select>
              </label>
            ))}
          </div>

          <div className="picker-section-title">{TRANSLATIONS.campaignLore}</div>
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
              {doc.status !== "ready" && <small>{TRANSLATIONS.refreshIndexingHint}</small>}
            </button>
          ))}
          {lore.length === 0 && <p className="hint">{TRANSLATIONS.noLoresHint}</p>}
        </div>
        <div className="modal-actions">
          <button className="secondary" onClick={onCancel} disabled={creating}>
            <X size={16} /> {TRANSLATIONS.cancel}
          </button>
          <button
            className="primary"
            onClick={onConfirm}
            disabled={creating || selectedHeroIds.length === 0}
          >
            {creating ? <LoaderCircle className="spin" size={16} /> : <Plus size={16} />}
            {creating ? TRANSLATIONS.buildingIntro : TRANSLATIONS.startCampaign}
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
        <span> {TRANSLATIONS.turns}</span>
      </div>
      <div>
        <Users size={16} />
        <strong>{detail.characters.length}</strong>
        <span> {TRANSLATIONS.party}</span>
      </div>
      <div>
        <MapPin size={16} />
        <strong>{detail.world_state.current_location}</strong>
        <span> {TRANSLATIONS.location.toLowerCase()}</span>
      </div>
      <div className="roll-status">
        <Dice5 size={16} />
        <strong>{rollResult}</strong>
        <span> {TRANSLATIONS.lastRoll}</span>
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
  const isWaitingFirstChunk = streaming && !draft;
  const contentRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (contentRef.current) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight;
    }
  }, [draft]);

  return (
    <section className="scene-viewport">
      <div className="scene-header">
        <div>
          <span className="eyebrow">{TRANSLATIONS.currentScene}</span>
          <h2>{isWaitingFirstChunk ? "Mestre" : (featured?.speaker ?? "DM")}</h2>
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
      <div className="scene-content" ref={contentRef} style={{ maxHeight: "350px", overflowY: "auto" }}>
        {isWaitingFirstChunk ? (
          <div className="thinking-container" style={{ display: "flex", alignItems: "center", gap: "0.5rem", color: "rgba(255, 255, 255, 0.7)", fontStyle: "italic", padding: "1rem 0" }}>
            <LoaderCircle className="spin" size={20} />
            <span>O Mestre está a pensar...</span>
          </div>
        ) : featured ? (
          formatSceneText(featured.content).map((paragraph, index) => (
            <p key={`${paragraph.slice(0, 20)}-${index}`}>{paragraph}</p>
          ))
        ) : (
          <p>{TRANSLATIONS.startPrompt}</p>
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
          <span className="eyebrow">{TRANSLATIONS.diceCheck}</span>
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
          {TRANSLATIONS.rollDice}
        </button>
      </div>
    </section>
  );
}

interface PartyStatusPanelProps {
  characters: Character[];
  activeSessionId: number | null;
  onUseItem?: (characterId: number, itemId: string, itemName: string) => void;
}

export function PartyStatusPanel({ characters, activeSessionId, onUseItem }: PartyStatusPanelProps) {
  return (
    <section className="rail-panel party-status-panel">
      <div className="rail-title">
        <Shield size={16} />
        <span>{TRANSLATIONS.party.toUpperCase()}</span>
      </div>
      <div className="party" style={{ display: "flex", flexDirection: "column", gap: "1.2rem" }}>
        {characters.map((character) => {
          let items: { id: string; name: string; type: string; effect: string }[] = [];
          if (character.inventory) {
            try {
              const parsed = JSON.parse(character.inventory);
              if (Array.isArray(parsed)) {
                items = parsed;
              }
            } catch (e) {
              // Ignore invalid JSON
            }
          }

          const hasSessionStats = activeSessionId !== null && character.hp !== undefined;
          const currentHp = character.hp ?? 10;
          const maxHp = character.max_hp ?? 10;
          const level = character.level ?? 1;
          const xp = character.xp ?? 0;
          const gold = character.gold ?? 0;
          
          const xpPercent = Math.min(100, Math.max(0, (xp / (level * 100)) * 100));

          return (
            <article key={character.id} className="party-member-card" style={{
              background: "rgba(255, 255, 255, 0.03)",
              border: "1px solid rgba(255, 255, 255, 0.05)",
              borderRadius: "8px",
              padding: "1rem",
              display: "flex",
              flexDirection: "column",
              gap: "0.5rem"
            }}>
              <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                <Shield size={16} style={{ color: "var(--color-primary, #6366f1)" }} />
                <h3 style={{ margin: 0, fontSize: "1rem", fontWeight: 600 }}>
                  {character.name} {character.is_human ? "" : "(IA)"}
                </h3>
              </div>
              <p style={{ margin: 0, fontSize: "0.8rem", color: "rgba(255,255,255,0.6)" }}>
                {character.ancestry} {character.character_class}
              </p>

              {hasSessionStats && (
                <div className="session-stats" style={{ display: "flex", flexDirection: "column", gap: "0.6rem", marginTop: "0.5rem" }}>
                  <div className="stat-row">
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.75rem", marginBottom: "2px" }}>
                      <span style={{ display: "flex", alignItems: "center", gap: "3px" }}>
                        <Heart size={12} style={{ color: "#ef4444" }} /> HP
                      </span>
                      <span>{currentHp}/{maxHp}</span>
                    </div>
                    <div className="bar-bg" style={{ background: "rgba(255,255,255,0.1)", borderRadius: "4px", height: "6px", overflow: "hidden" }}>
                      <div className="bar-fill" style={{
                        background: "#ef4444",
                        width: `${(currentHp / maxHp) * 100}%`,
                        height: "100%",
                        transition: "width 0.3s ease"
                      }} />
                    </div>
                  </div>

                  <div className="stat-row">
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.75rem", marginBottom: "2px" }}>
                      <span>Nível {level}</span>
                      <span>{xp}/{level * 100} XP</span>
                    </div>
                    <div className="bar-bg" style={{ background: "rgba(255,255,255,0.1)", borderRadius: "4px", height: "6px", overflow: "hidden" }}>
                      <div className="bar-fill" style={{
                        background: "#10b981",
                        width: `${xpPercent}%`,
                        height: "100%",
                        transition: "width 0.3s ease"
                      }} />
                    </div>
                  </div>

                  <div style={{ display: "flex", alignItems: "center", gap: "4px", fontSize: "0.8rem", color: "#fbbf24" }}>
                    <Coins size={14} />
                    <span>{gold} PO</span>
                  </div>

                  <div className="inventory-section" style={{ marginTop: "0.5rem", borderTop: "1px solid rgba(255,255,255,0.05)", paddingTop: "0.5rem" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "4px", fontSize: "0.8rem", fontWeight: 600, marginBottom: "0.4rem", color: "rgba(255,255,255,0.8)" }}>
                      <Package size={14} />
                      <span>Inventário</span>
                    </div>
                    {items.length === 0 ? (
                      <span style={{ fontSize: "0.75rem", color: "rgba(255,255,255,0.4)", fontStyle: "italic" }}>Sem itens no inventário</span>
                    ) : (
                      <div className="items-list" style={{ display: "flex", flexDirection: "column", gap: "0.4rem" }}>
                        {items.map((item) => {
                          const isConsumable = item.type === "consumable";
                          return (
                            <div key={item.id} className="inventory-item" style={{
                              display: "flex",
                              justifyContent: "space-between",
                              alignItems: "center",
                              background: "rgba(255,255,255,0.02)",
                              border: "1px solid rgba(255,255,255,0.03)",
                              borderRadius: "4px",
                              padding: "6px 8px",
                              fontSize: "0.75rem"
                            }}>
                              <div style={{ display: "flex", flexDirection: "column", gap: "2px", flex: 1, marginRight: "8px" }}>
                                <strong style={{ color: "rgba(255,255,255,0.9)" }}>{item.name}</strong>
                                <span style={{ color: "rgba(255,255,255,0.5)", fontSize: "0.7rem" }}>{item.effect}</span>
                              </div>
                              {isConsumable && onUseItem && (
                                <button
                                  className="use-item-btn"
                                  onClick={() => onUseItem(character.id, item.id, item.name)}
                                  style={{
                                    background: "rgba(99, 102, 241, 0.2)",
                                    border: "1px solid rgba(99, 102, 241, 0.4)",
                                    borderRadius: "4px",
                                    color: "#a5b4fc",
                                    padding: "3px 8px",
                                    cursor: "pointer",
                                    fontSize: "0.7rem",
                                    transition: "all 0.2s"
                                  }}
                                  onMouseOver={(e) => {
                                    e.currentTarget.style.background = "rgba(99, 102, 241, 0.4)";
                                    e.currentTarget.style.color = "#fff";
                                  }}
                                  onMouseOut={(e) => {
                                    e.currentTarget.style.background = "rgba(99, 102, 241, 0.2)";
                                    e.currentTarget.style.color = "#a5b4fc";
                                  }}
                                >
                                  Usar
                                </button>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}

export function PartyPanel({ characters }: { characters: Character[] }) {
  return <PartyStatusPanel characters={characters} activeSessionId={null} />;
}

function WorldPanel({ detail }: { detail: Detail }) {
  return (
    <section className="rail-panel">
      <div className="rail-title">
        <MapPin size={16} />
        <span>{TRANSLATIONS.world}</span>
      </div>
      <dl className="world-grid">
        <dt>{TRANSLATIONS.location}</dt>
        <dd>{detail.world_state.current_location}</dd>
        <dt>{TRANSLATIONS.objective}</dt>
        <dd>{detail.world_state.active_objective}</dd>
        <dt>{TRANSLATIONS.scene}</dt>
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
    "Procurar boatos sobre as ruínas seladas.",
    "Procurar uma taberna segura e ouvir as conversas.",
    "Inspecionar o marco de juramento mais próximo.",
    "Encontrar um guarda, contato de guilda ou guia local."
  ];
  const cleanChoices = choices.map(cleanChoiceText).filter(Boolean);
  const actions = cleanChoices.length ? cleanChoices : fallback;
  return (
    <section className="quick-actions">
      <div className="quick-title">
        <Sparkles size={16} />
        <span>{cleanChoices.length ? TRANSLATIONS.playerChoices : TRANSLATIONS.suggestedActions}</span>
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
  const logRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [turns.length, draft]);

  return (
    <section className="rail-panel timeline-panel">
      <div className="rail-title">
        <ScrollText size={16} />
        <span>{TRANSLATIONS.timeline}</span>
      </div>
      <div className="log" ref={logRef} style={{ maxHeight: "250px", overflowY: "auto" }}>
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
              {turn.speaker === "Player" ? "Jogador" : turn.speaker}
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
            ? TRANSLATIONS.resolveCheck
            : TRANSLATIONS.describeAction
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
  if (phase === "checking") return TRANSLATIONS.checking;
  if (phase === "generating") return TRANSLATIONS.generating;
  if (phase === "roll_required") return TRANSLATIONS.rollRequired;
  if (phase === "rolling") return TRANSLATIONS.rolling;
  if (phase === "error") return TRANSLATIONS.needsAttention;
  return TRANSLATIONS.ready;
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
