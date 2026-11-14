import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import Descent from "./components/Descent";
import Recommendations from "./components/Recommendations";
import RouteOptimizer, { type RouteResult } from "./components/RouteOptimizer";

const route = (id: string, alt: number, a: number, t: number, fuel: number, feasible = true) => ({
  id,
  orbit_altitude_km: alt,
  approach_s: a,
  terminal_s: t,
  assessment_time_s: a + t,
  feasible,
  rejection_reasons: feasible
    ? []
    : [{ reason: "reserve", detail: "Only 428 kg remains at touchdown; the 213.4 m/s reserve needs 503 kg." }],
  propellant_used_kg: fuel,
  propellant_remaining_kg: 8265 - fuel,
  reserve_required_kg: 112,
  reserve_margin_kg: feasible ? 8153 - fuel : -75,
  hover_margin_s: feasible ? 150 : 0,
  delta_v_mps: 2050,
});
const result: RouteResult = {
  reserve: { delta_v_mps: 44.196, source: "NASA TN D-6846, Table II" },
  propellant_at_doi_kg: 8265,
  routes: [
    route("r01", 74.08, 60, 60, 7080),
    route("r09", 74.08, 140, 150, 7783),
    route("r14", 111.12, 100, 108, 7472),
    route("r18", 111.12, 140, 150, 7810, false),
  ],
  feasible_ids: ["r01", "r09", "r14"],
  rejected_ids: ["r18"],
  pareto_ids: ["r01", "r09"],
  lowest_fuel_id: "r01",
  longest_assessment_id: "r09",
  reference_route_id: "r14",
  assumptions: ["Each route reuses the R4 model."],
};
const reply = (body: unknown, ok = true) => ({ ok, json: async () => body }) as Response;
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("shows the trade-off chart, tags and rejected reasons", () => {
  render(<RouteOptimizer result={result} />);
  expect(screen.getByText("3 of 4 routes keep the 145 fps reserve")).toBeTruthy();
  expect(screen.getByRole("img", { name: /Propellant used versus landing-area assessment time/ })).toBeTruthy();
  const table = screen.getByRole("table", { name: "Routes, lowest propellant first" });
  const rows = within(table).getAllByRole("row");
  expect(rows[1].textContent).toContain("r01");
  expect(rows[1].textContent).toContain("lowest fuel");
  expect(rows[rows.length - 1].textContent).toContain("Rejected");
  expect(rows[rows.length - 1].textContent).toContain("reserve needs 503 kg");
  expect(within(table).getByText(/Apollo 11 reference/)).toBeTruthy();
  expect(screen.getByText("Lowest fuel")).toBeTruthy();
});

test("descent section requests route optimization with the reserve", async () => {
  const profile = {
    profile_id: "apollo-lm-11",
    name: "Apollo Lunar Module (Apollo 11, LM-5)",
    mass_at_pdi_kg: 15150.7,
    usable_propellant_kg: 8161.3,
    max_thrust_n: 44482.2,
    design_thrust_n: 46706.3,
    min_throttle_fraction: 0.1,
    max_throttle_fraction: 0.6,
    isp_s: 309.7,
  };
  const fetchMock = vi.fn(async (url: string, _init?: RequestInit) =>
    url === "/api/vehicle-profiles" ? reply({ profiles: [profile] }) : reply(result),
  );
  vi.stubGlobal("fetch", fetchMock);
  render(<Descent sites={[]} />);
  await screen.findByRole("option", { name: profile.name });
  fireEvent.change(screen.getByLabelText("Reserve delta-v fps"), { target: { value: "700" } });
  fireEvent.click(screen.getByRole("button", { name: /Optimize routes/ }));
  await screen.findByText(/routes keep the/);
  const post = fetchMock.mock.calls.find(([u]) => u === "/api/descents/optimize")!;
  const body = JSON.parse(String(post[1]!.body));
  expect(body.reserve_delta_v_mps).toBeCloseTo(700 * 0.3048, 9);
  expect(body.profile_id).toBe("apollo-lm-11");
  expect(body.inclination_deg).toBe(90);
  expect(body.orbit).toBeUndefined();
});

test("recommendation can opt into descent fuel", async () => {
  const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
    reply({ detail: "stop here" }, false),
  );
  vi.stubGlobal("fetch", fetchMock);
  const sites = [
    {
      site_id: "a",
      display_name: "Site A",
      latitude_deg: -85,
      longitude_deg_east: 10,
      coordinate_source_url: "https://example.test",
      proposed_observer_height_above_terrain_m: 2,
    },
    {
      site_id: "b",
      display_name: "Site B",
      latitude_deg: -86,
      longitude_deg_east: 20,
      coordinate_source_url: "https://example.test",
      proposed_observer_height_above_terrain_m: 2,
    },
  ];
  render(<Recommendations sites={sites} />);
  expect(screen.queryByLabelText("Descent fuel margin weight")).toBeNull();
  fireEvent.click(screen.getByLabelText("Include descent fuel"));
  expect(screen.getByLabelText("Descent fuel margin weight")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /Rank candidate sites/ }));
  await screen.findByRole("alert");
  const post = fetchMock.mock.calls.find(([u]) => u === "/api/recommendations")!;
  const body = JSON.parse(String(post[1]!.body));
  expect(body.include_descent).toBe(true);
  expect(body.weights.fuel).toBe(1);
});
