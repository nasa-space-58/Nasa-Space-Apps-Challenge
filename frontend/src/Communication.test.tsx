import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import Communication from "./components/Communication";

const metrics = (available: number, unknown = 0, count = 1) => ({
  available_duration_s: available,
  unavailable_duration_s: 86400 - available - unknown,
  unknown_duration_s: unknown,
  available_percent_of_interval: (available / 86400) * 100,
  longest_available_s: available,
  window_count: count,
});
const span = (a: string, b: string, state: string, ids?: string[]) => ({
  start_utc: a,
  end_utc: b,
  state,
  duration_s: (Date.parse(b) - Date.parse(a)) / 1000,
  ...(ids ? { station_ids: ids } : {}),
});
const ready = {
  id: "comm-1",
  status: "ready",
  error: null,
  request: { min_elevation_deg: 10 },
  analysis_id: "analysis-1",
  result: {
    min_elevation_deg: 10,
    start_utc: "2026-11-04T00:00:00Z",
    end_utc: "2026-11-05T00:00:00Z",
    stations: [
      { station_id: "DSS-43", complex: "Canberra", antenna: "70-m" },
      { station_id: "DSS-63", complex: "Madrid", antenna: "70-m" },
    ],
    windows: {
      stations: {
        "DSS-43": {
          intervals: [
            span("2026-11-04T00:00:00Z", "2026-11-04T02:41:00Z", "available"),
            span("2026-11-04T02:41:00Z", "2026-11-05T00:00:00Z", "unavailable"),
          ],
          metrics: metrics(9660),
        },
        "DSS-63": {
          intervals: [
            span("2026-11-04T00:00:00Z", "2026-11-04T02:43:00Z", "unavailable"),
            span("2026-11-04T02:43:00Z", "2026-11-04T06:14:00Z", "available"),
            span("2026-11-04T06:14:00Z", "2026-11-05T00:00:00Z", "unavailable"),
          ],
          metrics: metrics(12660),
        },
      },
      combined: {
        intervals: [
          span("2026-11-04T00:00:00Z", "2026-11-04T02:41:00Z", "available", ["DSS-43"]),
          span("2026-11-04T02:41:00Z", "2026-11-04T02:43:00Z", "unavailable", []),
          span("2026-11-04T02:43:00Z", "2026-11-04T06:14:00Z", "available", ["DSS-63"]),
          span("2026-11-04T06:14:00Z", "2026-11-05T00:00:00Z", "unavailable", []),
        ],
        metrics: metrics(22320, 0, 2),
      },
    },
    source: {
      station_document: "DSN 810-005 module 301 Rev. P",
      station_url: "https://deepspace.jpl.nasa.gov/dsndocs/810-005/301/301P.pdf",
      sample_step_s: 300,
    },
    limitations: ["Geometric line of sight only; not a guaranteed radio link."],
  },
};
const reply = (body: unknown, ok = true) =>
  ({ ok, json: async () => body }) as Response;

beforeEach(() => {
  localStorage.clear();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("requests the chosen limit and shows station and combined windows", async () => {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) =>
    init?.method === "POST"
      ? reply({ ...ready, status: "pending", result: null })
      : reply(ready),
  );
  vi.stubGlobal("fetch", fetchMock);
  render(<Communication jobId="analysis-1" />);
  fireEvent.change(screen.getByLabelText("Station elevation limit"), {
    target: { value: "10" },
  });
  fireEvent.click(screen.getByRole("button", { name: /Check DSN communication/ }));
  await screen.findByText("Communication windows ready");
  const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(post[0]).toBe("/api/analyses/analysis-1/communication");
  expect(JSON.parse(String(post[1]!.body))).toEqual({ min_elevation_deg: 10 });
  expect(screen.getByText("25.8%")).toBeTruthy();
  expect(screen.getByRole("img", { name: /^DSS-43:/ })).toBeTruthy();
  expect(screen.getByRole("img", { name: /^ANY:/ })).toBeTruthy();
  const list = screen.getByRole("list", { name: "Communication windows" });
  expect(list.textContent).toContain("DSS-43");
  expect(list.textContent).toContain("DSS-63");
  expect(localStorage.getItem("lln.communication.analysis-1")).toBe("comm-1");
});

test("reopens a remembered result and reports failures", async () => {
  localStorage.setItem("lln.communication.analysis-1", "comm-1");
  vi.stubGlobal("fetch", vi.fn(async () => reply(ready)));
  render(<Communication jobId="analysis-1" />);
  await screen.findByText("Communication windows ready");
  expect(
    screen.getByRole("button", { name: /Recalculate windows/ }),
  ).toBeTruthy();
  cleanup();
  localStorage.clear();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => reply({ detail: "Analysis is not complete" }, false)),
  );
  render(<Communication jobId="analysis-2" />);
  fireEvent.click(screen.getByRole("button", { name: /Check DSN communication/ }));
  await waitFor(() =>
    expect(screen.getByRole("alert").textContent).toBe("Analysis is not complete"),
  );
});
