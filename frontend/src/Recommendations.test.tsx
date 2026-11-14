import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import Recommendations from "./components/Recommendations";

const sites = [
  {
    site_id: "malapert-massif-r23",
    display_name: "Malapert Massif — R23",
    latitude_deg: -85.9948,
    longitude_deg_east: 359.76422,
    coordinate_source_url: "https://example.test",
    proposed_observer_height_above_terrain_m: 2,
  },
  {
    site_id: "mons-mouton-feature-center",
    display_name: "Mons Mouton — feature center",
    latitude_deg: -84.6,
    longitude_deg_east: 31,
    coordinate_source_url: "https://example.test",
    proposed_observer_height_above_terrain_m: 3,
  },
];
const score = (s: number) => ({ score: s, value: s, unit: "u", rule: "r" });
const row = (id: string, rank: number, total: number) => ({
  candidate_id: id,
  rank,
  total_score: total,
  contributions: { energy: total / 2, darkness: total / 2, communication: 0, terrain_safety: 0 },
  criteria: { energy: score(0.2), darkness: score(0.2), communication: score(0), terrain_safety: score(0.3) },
  explanation: `Rank ${rank} explanation`,
});
const weights = { energy: 0.25, darkness: 0.25, communication: 0.25, terrain_safety: 0.25 };
const ranking = (ids: string[]) => ({
  weights,
  ranked: ids.map((id, i) => row(id, i + 1, 0.5 - i / 10)),
  rejected: [{ candidate_id: "c3", explanation: "Rejected: slope 11.1 deg exceeds the limit 10.0 deg." }],
  insufficient_data: [],
  failed: [],
  not_modeled: { fuel: "After R5." },
});
const ready = {
  id: "rec-1",
  status: "ready",
  previous_id: null,
  request: {
    start_utc: "2026-11-04T00:00:00Z",
    end_utc: "2026-11-05T00:00:00Z",
    weights: { energy: 1, darkness: 1, communication: 1, terrain_safety: 1 },
    constraints: { slope_limit_deg: 10, landing_radius_m: 100 },
  },
  candidates: ["c1", "c2", "c3"].map((id, i) => ({
    candidate_id: id,
    site: { site_id: ["malapert-massif-r23", "mons-mouton-feature-center", "custom-1"][i], latitude_deg: -85, longitude_deg_east: 0 },
    state: "evaluated",
    error: null,
    safety: { covered: true, site_slope_deg: 3.9, max_landing_slope_deg: 5.6, hazard_distance_m: 288 },
  })),
  ranking: ranking(["c1", "c2"]),
  schedule_change: null,
};
const reply = (body: unknown, ok = true) => ({ ok, json: async () => body }) as Response;

beforeEach(() => localStorage.clear());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("submits candidates, constraints and weights and explains the ranking", async () => {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/rescore")) {
      return reply({
        previous: ranking(["c1", "c2"]),
        current: ranking(["c2", "c1"]),
        changes: [
          { candidate_id: "c1", previous_rank: 1, new_rank: 2, reason: "energy" },
          { candidate_id: "c2", previous_rank: 2, new_rank: 1, reason: "communication" },
        ],
      });
    }
    if (url.endsWith("/reschedule")) return reply({ ...ready, id: "rec-2", status: "pending" });
    return reply(init?.method === "POST" ? { ...ready, status: "pending", ranking: null } : ready);
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<Recommendations sites={sites} />);
  fireEvent.change(screen.getByLabelText("Maximum landing slope"), { target: { value: "8" } });
  fireEvent.change(screen.getByLabelText("DSN communication weight"), { target: { value: "3" } });
  fireEvent.click(screen.getByRole("button", { name: /Rank candidate sites/ }));
  await screen.findByText("Recommendation ready");

  const post = fetchMock.mock.calls.find(([u, i]) => u === "/api/recommendations" && i?.method === "POST")!;
  const body = JSON.parse(String(post[1]!.body));
  expect(body.candidates.map((c: { site_id: string }) => c.site_id)).toEqual([
    "malapert-massif-r23",
    "mons-mouton-feature-center",
  ]);
  expect(body.candidates[0].observer_height_m).toBe(2);
  expect(body.constraints.slope_limit_deg).toBe(8);
  expect(body.constraints.min_sunlight_percent).toBeNull();
  expect(body.weights.communication).toBe(3);
  expect(body.start_utc).toBe("2026-11-04T00:00:00Z");
  expect(body.end_utc).toBe("2026-11-05T00:00:00Z");

  const table = screen.getByRole("table", { name: "Ranking" });
  expect(within(table).getByText("Malapert Massif — R23")).toBeTruthy();
  expect(within(table).getByText("Rank 1 explanation")).toBeTruthy();
  expect(screen.getByRole("list", { name: "Rejected or unranked candidates" }).textContent).toContain(
    "slope 11.1 deg exceeds",
  );
  expect(localStorage.getItem("lln.recommendation.id")).toBe("rec-1");

  fireEvent.click(screen.getByRole("button", { name: /Apply new priorities/ }));
  const changes = await screen.findByRole("list", { name: "Rank changes" });
  expect(changes.textContent).toContain("moved from #2 to #1");
  expect(changes.textContent).toContain("DSN communication");
  expect(screen.getByRole("table", { name: "New priorities" })).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: /Re-rank after this delay/ }));
  await waitFor(() =>
    expect(fetchMock.mock.calls.some(([u]) => u === "/api/recommendations/rec-1/reschedule")).toBe(true),
  );
  const slip = fetchMock.mock.calls.find(([u]) => u === "/api/recommendations/rec-1/reschedule")!;
  expect(JSON.parse(String(slip[1]!.body))).toEqual({ delay_hours: 48 });
});

test("shows API validation errors and the schedule suggestion", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => reply({ detail: [{ msg: "Candidates must have distinct positions/heights" }] }, false)),
  );
  render(<Recommendations sites={sites} />);
  fireEvent.click(screen.getByRole("button", { name: /Rank candidate sites/ }));
  await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("distinct positions"));
  cleanup();
  localStorage.setItem("lln.recommendation.id", "rec-2");
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      reply({
        ...ready,
        id: "rec-2",
        previous_id: "rec-1",
        schedule_change: {
          suggestion: "malapert-massif-r23 (c1) remains the best option after the change.",
          changes: [],
        },
      }),
    ),
  );
  render(<Recommendations sites={sites} />);
  expect(await screen.findByText(/remains the best option/)).toBeTruthy();
});
