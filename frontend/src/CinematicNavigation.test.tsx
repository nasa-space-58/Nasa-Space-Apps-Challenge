import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import App from "./App";

vi.mock("./components/MoonScene", () => ({ default: () => null }));
beforeEach(() => {
  localStorage.clear();
  window.history.replaceState({}, "", "/");
  Element.prototype.scrollIntoView = vi.fn();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: true, json: async () => ({ sites: [] }) })),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("ambient motion can be paused and resumed without hiding observation controls", () => {
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "Pause ambient motion" }));
  expect(
    screen
      .getByRole("button", { name: "Resume ambient motion" })
      .getAttribute("aria-pressed"),
  ).toBe("true");
  expect(screen.getByRole("button", { name: /Run analysis/ })).toBeTruthy();
  fireEvent.click(
    screen.getByRole("button", { name: "Resume ambient motion" }),
  );
  expect(
    screen
      .getByRole("button", { name: "Pause ambient motion" })
      .getAttribute("aria-pressed"),
  ).toBe("false");
});

test("skip control opens and focuses the workspace even from time search", async () => {
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "Time search" }));
  expect(document.getElementById("mission")?.hidden).toBe(true);
  fireEvent.click(
    screen.getByRole("button", { name: "Skip to mission control" }),
  );
  await waitFor(() => expect(document.activeElement?.id).toBe("mission"));
  expect(document.getElementById("mission")?.hidden).toBe(false);
});

test("system reduced motion is respected and cannot be accidentally overridden", () => {
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({
      matches: true,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
  render(<App />);
  const button = screen.getByRole("button", {
    name: "Reduced motion",
  }) as HTMLButtonElement;
  expect(button.disabled).toBe(true);
  expect(button.getAttribute("aria-pressed")).toBe("true");
  expect(
    screen.getByRole("heading", { name: "Lunar Landing Navigator" }),
  ).toBeTruthy();
});
