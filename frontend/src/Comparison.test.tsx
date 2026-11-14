import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import Comparison from "./components/Comparison";
import fixture from "./fixtures/malapert";
import type { Result } from "./types";
const a = fixture as Result;
const base = {
  site_id: "malapert-massif-r23",
  latitude_deg: -85.9948,
  longitude_deg_east: 359.76422,
  observer_height_m: 2,
  start_utc: "2026-11-04T00:00:00Z",
  end_utc: "2026-11-05T00:00:00Z",
  step_s: 3600,
  refine: true,
  horizon: { search_distance_m: 100000 },
};
const sites = [
  {
    site_id: "mouton",
    display_name: "Mons Mouton",
    latitude_deg: -84.6,
    longitude_deg_east: 31,
    coordinate_source_url: "https://planetarynames.wr.usgs.gov/Feature/16070",
    proposed_observer_height_above_terrain_m: 2,
  },
];
const mount = () =>
  render(<Comparison aId="baseline" a={a} sites={sites} onClose={() => {}} />);
beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal("fetch", vi.fn());
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const response = (body: unknown, ok = true) =>
  ({ ok, json: async () => body }) as Response;

test("48-hour example preserves A and all shared settings when submitting B", async () => {
  const original = JSON.stringify(a);
  vi.mocked(fetch).mockImplementation(async (url, init) =>
    response(
      url === "/api/analyses/baseline"
        ? { submission: base }
        : init?.method === "POST"
          ? { id: "b", status: "pending" }
          : { status: "failed", error: "Source unavailable" },
    ),
  );
  mount();
  await screen.findByLabelText("Scenario B start UTC");
  fireEvent.click(
    screen.getByRole("button", { name: "Use +48-hour delay ↗" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Run scenario B ↗" }));
  await screen.findByText(/Source unavailable/);
  const sent = vi
    .mocked(fetch)
    .mock.calls.find((call) => call[0] === "/api/analyses")!;
  expect(JSON.parse(String(sent[1]?.body))).toEqual({
    ...base,
    start_utc: "2026-11-06T00:00:00.000Z",
    end_utc: "2026-11-07T00:00:00.000Z",
  });
  expect(JSON.stringify(a)).toBe(original);
});

test("site mode preserves baseline dates, height, refinement and duration", async () => {
  vi.mocked(fetch).mockImplementation(async (url, init) =>
    response(
      url === "/api/analyses/baseline"
        ? { submission: base }
        : init?.method === "POST"
          ? { id: "b", status: "pending" }
          : { status: "failed", error: "Test failure" },
    ),
  );
  mount();
  await screen.findByLabelText("Scenario B start UTC");
  fireEvent.click(
    screen.getByRole("button", { name: "Same date, different place" }),
  );
  fireEvent.change(screen.getByLabelText("Scenario B region"), {
    target: { value: "mouton" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Run scenario B ↗" }));
  await screen.findByText(/Test failure/);
  const sent = vi
    .mocked(fetch)
    .mock.calls.find((call) => call[0] === "/api/analyses")!;
  expect(JSON.parse(String(sent[1]?.body))).toEqual({
    ...base,
    site_id: "mouton",
    latitude_deg: -84.6,
    longitude_deg_east: 31,
  });
});

test("restores B, synchronizes both sample views and preserves data across tabs", async () => {
  localStorage.setItem(
    "lunar-comparison:baseline",
    JSON.stringify({ draft: base, mode: "dates", bId: "saved-b" }),
  );
  const deltas = {
    visible_duration_s: 0,
    visible_percent_of_interval: 0,
    longest_blocked_s: 0,
  };
  const match = {
    mode: "identical",
    status: "ready",
    alignment: "shared_utc",
    duration_s: 86400,
    start_offset_s: 0,
    deltas: { sun: deltas, earth: deltas },
    changes: { sun: [], earth: [] },
    explanations: ["Same results."],
    cursor: [
      {
        elapsed_s: 0,
        a_index: 0,
        b_index: 0,
        a_sample_age_s: 0,
        b_sample_age_s: 0,
      },
      {
        elapsed_s: 82800,
        a_index: 1,
        b_index: 1,
        a_sample_age_s: 0,
        b_sample_age_s: 0,
      },
    ],
  };
  vi.mocked(fetch).mockImplementation(async (url) =>
    response(
      url === "/api/analyses/baseline"
        ? { submission: base }
        : url === "/api/comparisons"
          ? match
          : String(url).endsWith("/result")
            ? a
            : { status: "ready" },
    ),
  );
  mount();
  await screen.findByText("Same results.");
  fireEvent.change(screen.getByRole("slider", { name: "Comparison time" }), {
    target: { value: "1" },
  });
  const panelA = screen.getByRole("article", { name: "Scenario A results" }),
    panelB = screen.getByRole("article", { name: "Scenario B results" });
  expect(
    within(panelA).getByText(/Displayed sample: 2026-11-04 23:00:00 UTC/),
  ).toBeTruthy();
  expect(
    within(panelB).getByText(/Displayed sample: 2026-11-04 23:00:00 UTC/),
  ).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", { name: "Scenario B" }));
  fireEvent.click(screen.getByRole("tab", { name: "Scenario A" }));
  expect(
    (
      screen.getByRole("slider", {
        name: "Comparison time",
      }) as HTMLInputElement
    ).value,
  ).toBe("1");
  expect(
    (screen.getByLabelText("Scenario B start UTC") as HTMLInputElement).value,
  ).toContain("2026-11-04");
  const clipIds = Array.from(document.querySelectorAll("clipPath")).map(
    (node) => node.id,
  );
  expect(new Set(clipIds).size).toBe(clipIds.length);
});

test("incompatible comparison never presents successful delta cards", async () => {
  localStorage.setItem(
    "lunar-comparison:baseline",
    JSON.stringify({ draft: base, mode: "dates", bId: "saved-b" }),
  );
  vi.mocked(fetch).mockImplementation(async (url) =>
    url === "/api/comparisons"
      ? response({ detail: "Incompatible results" }, false)
      : response(
          url === "/api/analyses/baseline"
            ? { submission: base }
            : String(url).endsWith("/result")
              ? a
              : { status: "ready" },
        ),
  );
  mount();
  await waitFor(() =>
    expect(screen.getByRole("alert").textContent).toContain(
      "Incompatible results",
    ),
  );
  expect(screen.queryByRole("slider", { name: "Comparison time" })).toBeNull();
});
