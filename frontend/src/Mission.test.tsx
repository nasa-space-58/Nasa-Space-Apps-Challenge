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

test("submits UTC coordinates and AGL height and reports a failed job", async () => {
  const fetcher = vi.mocked(fetch);
  fetcher.mockImplementation(
    async (url, init) =>
      ({
        ok: true,
        json: async () => {
          if (url === "/api/presets") return { sites: [] };
          if (init?.method === "POST")
            return { id: "job-one", status: "pending" };
          return {
            id: "job-one",
            status: "failed",
            error: "Terrain has missing coverage",
          };
        },
      }) as Response,
  );
  render(<App />);
  fireEvent.change(screen.getByLabelText("Latitude"), {
    target: { value: "-86.1" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Run analysis ↗" }));
  await screen.findByText("Terrain has missing coverage");
  const submission = fetcher.mock.calls.find(
    (call) => call[1]?.method === "POST",
  )!;
  expect(JSON.parse(String(submission[1]?.body))).toMatchObject({
    site_id: "custom",
    latitude_deg: -86.1,
    observer_height_m: 2,
    start_utc: "2026-11-04T00:00:00.000Z",
    end_utc: "2026-11-05T00:00:00.000Z",
    refine: true,
  });
  expect(localStorage.getItem("lunar-analysis-id")).toBe("job-one");
  expect(
    screen.getByRole("button", { name: "Run analysis ↗" }).closest("fieldset")
      ?.disabled,
  ).toBe(false);
});

test("keeps API errors visible and supports reconnecting", async () => {
  vi.mocked(fetch).mockRejectedValueOnce(new Error("Network unavailable"));
  render(<App />);
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "Reconnect" }));
  await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
});

test("source dialog traps keyboard focus and returns to its opener", async () => {
  render(<App />);
  const opener = screen.getByRole("button", { name: "Sources ↗" });
  opener.focus();
  fireEvent.click(opener);
  const close = screen.getByRole("button", { name: "Close sources" });
  expect(document.activeElement).toBe(close);
  fireEvent.keyDown(close, { key: "Tab", shiftKey: true });
  expect(document.activeElement?.textContent).toBe("JPL Horizons");
  fireEvent.keyDown(document.activeElement!, { key: "Tab" });
  expect(document.activeElement).toBe(close);
  fireEvent.keyDown(close, { key: "Escape" });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(opener);
});
