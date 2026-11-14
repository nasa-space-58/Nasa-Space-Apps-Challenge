import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import Descent from "./components/Descent";

const sites = [
  {
    site_id: "malapert-massif-r23",
    display_name: "Malapert Massif — R23",
    latitude_deg: -85.9948,
    longitude_deg_east: 359.76422,
    coordinate_source_url: "https://example.test",
    proposed_observer_height_above_terrain_m: 2,
  },
];
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
  sources: { isp_s: "Apollo 9 average 309.7 s" },
  urls: ["https://ntrs.nasa.gov/x.pdf"],
};
const sample = (t: number, phase: string, dr: number, alt: number) => ({
  t_s: t,
  phase,
  downrange_m: dr,
  altitude_m: alt,
  speed_mps: 100,
  vertical_speed_mps: -10,
  horizontal_speed_mps: 99,
  throttle_fraction: 0.5,
  mass_kg: 10000,
});
const plan = (feasible: boolean) => ({
  feasible,
  limiting_factors: feasible
    ? []
    : [{ factor: "plane_change", detail: "Orbit inclination 80.00 deg never overflies latitude -85.9948 deg." }],
  site: { site_id: "malapert-massif-r23", terrain_height_m: 4785 },
  deorbit: { delta_v_mps: 20.7, periapsis_speed_mps: 1691, coast_s: 3435 },
  plane_change: { required_deg: feasible ? 0 : 5.99, delta_v_mps: feasible ? 0 : 170.3, applied: false },
  pdi_range_m: 543000,
  phases: [
    { name: "coast", start_s: 0, end_s: 3435, delta_v_mps: 0, propellant_kg: 0 },
    { name: "braking", start_s: 3435, end_s: 3941, delta_v_mps: 1537.8, propellant_kg: 6019 },
    { name: "approach", start_s: 3941, end_s: 4041, delta_v_mps: 306.2, propellant_kg: 876 },
    { name: "terminal", start_s: 4041, end_s: 4149, delta_v_mps: 179.5, propellant_kg: 474 },
  ],
  totals: {
    powered_delta_v_mps: 2023.5,
    total_delta_v_mps: 2044.2,
    duration_s: 4149,
    propellant_used_kg: 7369,
    propellant_margin_kg: 793,
  },
  touchdown: { position_error_m: 0.004, vertical_speed_mps: -0.912, horizontal_speed_mps: 0.006 },
  reference_comparison: { model_powered_delta_v_fps: 6639, difference_percent: -2.0 },
  samples: [
    sample(0, "braking", -543000, 15240),
    sample(506, "braking", -8334, 2290),
    sample(606, "approach", -545, 156),
    sample(714, "terminal", 0, 0),
    sample(714, "landed", 0, 0),
  ],
  assumptions: ["Spherical Moon with point-mass gravity."],
});
const reply = (body: unknown, ok = true) => ({ ok, json: async () => body }) as Response;
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("submits the site, profile and orbit and shows the phase budget", async () => {
  const fetchMock = vi.fn(async (url: string, _init?: RequestInit) =>
    url === "/api/vehicle-profiles" ? reply({ profiles: [profile] }) : reply(plan(true)),
  );
  vi.stubGlobal("fetch", fetchMock);
  render(<Descent sites={sites} />);
  await screen.findByRole("option", { name: profile.name });
  fireEvent.change(screen.getByLabelText("Orbit inclination"), { target: { value: "88" } });
  fireEvent.click(screen.getByRole("button", { name: /Compute descent/ }));
  await screen.findByText(/Reaches the site within tolerance/);
  const post = fetchMock.mock.calls.find(([u]) => u === "/api/descents")!;
  const body = JSON.parse(String(post[1]!.body));
  expect(body).toMatchObject({
    site_id: "malapert-massif-r23",
    latitude_deg: -85.9948,
    profile_id: "apollo-lm-11",
    orbit: { altitude_km: 111.12, inclination_deg: 88 },
    allow_plane_change: false,
  });
  expect(screen.getByText("2024 m/s")).toBeTruthy();
  const table = screen.getByRole("table", { name: "Phase timing and budget" });
  expect(within(table).getByText("Braking")).toBeTruthy();
  expect(within(table).getByText(/1537.8 m\/s/)).toBeTruthy();
  expect(screen.getByRole("img", { name: /Altitude above the site versus downrange/ })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Final 10 km" }));
  expect(screen.getByRole("img", { name: /final 10 km/ })).toBeTruthy();
});

test("reports limiting factors and custom profiles", async () => {
  const fetchMock = vi.fn(async (url: string, _init?: RequestInit) =>
    url === "/api/vehicle-profiles" ? reply({ profiles: [profile] }) : reply(plan(false)),
  );
  vi.stubGlobal("fetch", fetchMock);
  render(<Descent sites={sites} />);
  await screen.findByRole("option", { name: profile.name });
  fireEvent.change(screen.getByLabelText("Vehicle profile"), { target: { value: "custom" } });
  fireEvent.click(screen.getByRole("button", { name: /Compute descent/ }));
  await screen.findByText(/Not feasible as specified/);
  expect(screen.getByText(/never overflies latitude/)).toBeTruthy();
  const post = fetchMock.mock.calls.find(([u]) => u === "/api/descents")!;
  const body = JSON.parse(String(post[1]!.body));
  expect(body.custom_profile.isp_s).toBe(310);
  expect(body.profile_id).toBeUndefined();
});

test("shows API errors", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      url === "/api/vehicle-profiles"
        ? reply({ profiles: [profile] })
        : reply({ detail: "Terrain not cached yet; run an analysis first" }, false),
    ),
  );
  render(<Descent sites={sites} />);
  await screen.findByRole("option", { name: profile.name });
  fireEvent.click(screen.getByRole("button", { name: /Compute descent/ }));
  await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Terrain not cached"));
});
