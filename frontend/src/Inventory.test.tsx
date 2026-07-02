import { expect, test, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { PartyStatusPanel } from "./App";

test("renders PartyStatusPanel with session stats and interactive inventory", () => {
  const onUseItem = vi.fn();
  const mockCharacters = [
    {
      id: 201,
      campaign_id: 20,
      name: "Roderick",
      ancestry: "Humano",
      character_class: "Guerreiro",
      backstory: "Um bravo cavaleiro",
      inventory_json: "[]",
      is_human: true,
      hp: 8,
      max_hp: 12,
      level: 2,
      xp: 50,
      gold: 150,
      inventory: JSON.stringify([
        { id: "pot1", name: "Poção de Cura", type: "consumable", effect: "restaura 5 HP" },
        { id: "swd1", name: "Espada de Aço", type: "weapon", effect: "dano de 1d8" }
      ])
    }
  ];

  render(
    <PartyStatusPanel
      characters={mockCharacters}
      activeSessionId={5}
      onUseItem={onUseItem}
    />
  );

  // Check name and basic details
  expect(screen.getByText("Roderick")).toBeTruthy();
  expect(screen.getByText("Humano Guerreiro")).toBeTruthy();

  // Check session stats
  expect(screen.getByText("8/12")).toBeTruthy(); // HP text
  expect(screen.getByText("Nível 2")).toBeTruthy(); // Level
  expect(screen.getByText("50/200 XP")).toBeTruthy(); // XP info
  expect(screen.getByText("150 PO")).toBeTruthy(); // Gold

  // Check inventory items are displayed
  expect(screen.getByText("Poção de Cura")).toBeTruthy();
  expect(screen.getByText("restaura 5 HP")).toBeTruthy();
  expect(screen.getByText("Espada de Aço")).toBeTruthy();
  expect(screen.getByText("dano de 1d8")).toBeTruthy();

  // Check that "Usar" button is rendered only for consumable items
  const useButtons = screen.getAllByRole("button", { name: "Usar" });
  expect(useButtons.length).toBe(1); // Only potion is consumable

  // Trigger click on use button
  fireEvent.click(useButtons[0]);
  expect(onUseItem).toHaveBeenCalledWith(201, "pot1", "Poção de Cura");
});
