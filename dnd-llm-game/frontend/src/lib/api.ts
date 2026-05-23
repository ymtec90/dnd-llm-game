export const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8765/api";

export type Campaign = {
  id: number;
  title: string;
  setting: string;
  tone: string;
};

export type Character = {
  id: number;
  campaign_id: number;
  name: string;
  ancestry: string;
  character_class: string;
  backstory: string;
  inventory_json: string;
  is_human: boolean;
};

export type Hero = {
  id: number;
  name: string;
  ancestry: string;
  character_class: string;
  backstory: string;
  inventory_json: string;
  is_human: boolean;
};

export type Turn = {
  id: number;
  campaign_id: number;
  speaker: string;
  content: string;
  created_at: string;
};

export type WorldState = {
  id: number;
  campaign_id: number;
  current_location: string;
  active_objective: string;
  scene_summary: string;
  choices_json: string;
};

export type ChoiceUpdate = {
  choices: string[];
  location: string;
  objective: string;
  summary: string;
};

export type PendingRoll = {
  id: number;
  campaign_id: number;
  action_text: string;
  formula: string;
  ability: string;
  skill?: string | null;
  dc: number;
  reason: string;
  narration: string;
  status: string;
};

export type Health = {
  status: string;
  ollama: string;
  ollama_host: string;
  chat_model: string;
  utility_model: string;
  embed_model: string;
  models: string[];
};

export type LoreDocument = {
  id: number;
  filename: string;
  status: string;
  chunks: number;
  error?: string | null;
};

export async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

export async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

export async function streamTurn(
  campaignId: number,
  content: string,
  onEvent: (event: string, payload: unknown) => void
): Promise<void> {
  const response = await fetch(`${API_BASE}/campaigns/${campaignId}/actions/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content })
  });
  if (!response.ok || !response.body) throw new Error(await response.text());

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";
    for (const rawEvent of events) {
      const lines: string[] = [];
      let eventName = "message";
      for (const line of rawEvent.split("\n")) {
        if (line.startsWith("event: ")) eventName = line.slice(7);
        if (line.startsWith("data: ")) lines.push(line.slice(6));
      }
      if (lines.length) {
        const data = lines.join("\n");
        try {
          onEvent(eventName, JSON.parse(data));
        } catch {
          onEvent(eventName, data);
        }
      }
    }
  }
}

export async function resolveRoll(
  campaignId: number,
  pendingRollId: number,
  onEvent: (event: string, payload: unknown) => void
): Promise<void> {
  const response = await fetch(
    `${API_BASE}/campaigns/${campaignId}/rolls/${pendingRollId}/resolve/stream`,
    { method: "POST" }
  );
  if (!response.ok || !response.body) throw new Error(await response.text());

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";
    for (const rawEvent of events) {
      const lines: string[] = [];
      let eventName = "message";
      for (const line of rawEvent.split("\n")) {
        if (line.startsWith("event: ")) eventName = line.slice(7);
        if (line.startsWith("data: ")) lines.push(line.slice(6));
      }
      if (lines.length) {
        const data = lines.join("\n");
        try {
          onEvent(eventName, JSON.parse(data));
        } catch {
          onEvent(eventName, data);
        }
      }
    }
  }
}
