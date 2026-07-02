import { expect, test, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { LorePanel } from "./App";
import { TRANSLATIONS } from "./lib/i18n";

test("renders LorePanel with correct upload texts and file inputs", () => {
  const onRefreshLoreIndex = vi.fn();
  const onUploadLore = vi.fn();
  const mockLore = [
    { id: 1, filename: "aventura1.txt", status: "ready", chunks: 5, created_at: "" },
    { id: 2, filename: "aventura2.pdf", status: "indexing", chunks: 0, created_at: "" }
  ];

  render(
    <LorePanel
      lore={mockLore}
      onRefreshLoreIndex={onRefreshLoreIndex}
      onUploadLore={onUploadLore}
    />
  );

  // Check upload label is rendered
  expect(screen.getByText(TRANSLATIONS.uploadLore)).toBeTruthy();

  // Check files are listed
  expect(screen.getByText("aventura1.txt")).toBeTruthy();
  expect(screen.getByText("ready · 5 chunks")).toBeTruthy();
  expect(screen.getByText("aventura2.pdf")).toBeTruthy();
  expect(screen.getByText("indexing · 0 chunks")).toBeTruthy();
});

test("triggers onUploadLore when a file is selected", () => {
  const onRefreshLoreIndex = vi.fn();
  const onUploadLore = vi.fn();

  render(
    <LorePanel
      lore={[]}
      onRefreshLoreIndex={onRefreshLoreIndex}
      onUploadLore={onUploadLore}
    />
  );

  // Use selector to find input because of label wrapper
  const fileInput = screen.getByText(TRANSLATIONS.uploadLore).parentElement?.querySelector("input[type='file']");
  expect(fileInput).toBeTruthy();
  if (fileInput) {
    const file = new File(["dummy content"], "my_adventure.txt", { type: "text/plain" });
    fireEvent.change(fileInput, { target: { files: [file] } });
    expect(onUploadLore).toHaveBeenCalledWith(file);
  }
});

test("triggers onRefreshLoreIndex when refresh button is clicked", () => {
  const onRefreshLoreIndex = vi.fn();
  const onUploadLore = vi.fn();

  render(
    <LorePanel
      lore={[]}
      onRefreshLoreIndex={onRefreshLoreIndex}
      onUploadLore={onUploadLore}
    />
  );

  const refreshButton = screen.getByTitle("Atualizar índice");
  fireEvent.click(refreshButton);

  expect(onRefreshLoreIndex).toHaveBeenCalled();
});
