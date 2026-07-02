import { expect, test, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SessionsPanel } from "./App";

vi.mock("./lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/api")>();
  return {
    ...original,
    getJson: vi.fn().mockImplementation((url: string) => {
      if (url === "/lore/packs") {
        return Promise.resolve([
          { id: "grimdark_scifi", name: "Ficção Científica Sombria (Aegis-9)", description: "Nave Aegis-9" },
          { id: "historico_brasil", name: "Brasil Colonial", description: "Ouro Preto 1789" }
        ]);
      }
      return Promise.resolve([]);
    })
  };
});

test("renders SessionsPanel and sends lore_pack in onCreateSession", async () => {
  const onCreateSession = vi.fn();
  const onSelectSession = vi.fn();
  const onDeleteSession = vi.fn();

  render(
    <SessionsPanel
      sessions={[]}
      activeSessionId={null}
      campaigns={[{ id: 1, title: "Campanha Teste", setting: "Medieval", tone: "Neutro" }]}
      onSelectSession={onSelectSession}
      onCreateSession={onCreateSession}
      onDeleteSession={onDeleteSession}
    />
  );

  // Enter session name
  const nameInput = screen.getByPlaceholderText("Nome da Sessão / Save");
  fireEvent.change(nameInput, { target: { value: "Minha Nova Sessão" } });

  // Wait for the dropdown select options to load
  const selectElement = await screen.findByLabelText("Cenário da Campanha");
  expect(selectElement).toBeTruthy();

  // Initially, no description card is visible
  expect(screen.queryByText("Nave Aegis-9")).toBeNull();

  // Change lore pack to grimdark_scifi
  fireEvent.change(selectElement, { target: { value: "grimdark_scifi" } });

  // The description card should appear
  expect(await screen.findByText("Nave Aegis-9")).toBeTruthy();

  // Click create button
  const createButton = screen.getByRole("button", { name: "Salvar & Iniciar" });
  fireEvent.click(createButton);

  // onCreateSession should have been called with the lore_pack ID
  expect(onCreateSession).toHaveBeenCalledWith(1, "Minha Nova Sessão", "grimdark_scifi");
});
