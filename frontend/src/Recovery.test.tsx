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
  vi.stubGlobal("fetch", vi.fn());
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const reply = (body: unknown) =>
  ({ ok: true, json: async () => body }) as Response;
test("ready-to-run example works even when the editable start field is empty", async () => {
  vi.mocked(fetch).mockImplementation(async (url, init) =>
    reply(
      url === "/api/presets"
        ? { sites: [] }
        : init?.method === "POST"
          ? { id: "example", status: "pending" }
          : { id: "example", status: "failed", error: "Fixture job stopped" },
    ),
  );
  render(<App />);
  fireEvent.change(screen.getByLabelText("Start date & time · UTC"), {
    target: { value: "" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Run the Malapert example ↗" }),
  );
  await waitFor(() =>
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(
          ([url, init]) => url === "/api/analyses" && init?.method === "POST",
        ),
    ).toBe(true),
  );
  const sent = vi
    .mocked(fetch)
    .mock.calls.find(([url]) => url === "/api/analyses")!;
  expect(JSON.parse(String(sent[1]?.body)).start_utc).toBe(
    "2026-11-04T00:00:00Z",
  );
});
test("reload restores submitted scenario fields instead of silently displaying defaults", async () => {
  localStorage.setItem("lunar-analysis-id", "saved");
  const submission = {
    site_id: "custom",
    latitude_deg: -87,
    longitude_deg_east: 42,
    observer_height_m: 5,
    start_utc: "2027-02-01T12:30:00Z",
    end_utc: "2027-02-04T12:30:00Z",
    refine: false,
  };
  vi.mocked(fetch).mockImplementation(async (url) =>
    reply(
      url === "/api/presets"
        ? { sites: [] }
        : {
            id: "saved",
            status: "failed",
            submission,
            error: "Fixture job stopped",
          },
    ),
  );
  render(<App />);
  await waitFor(() =>
    expect((screen.getByLabelText("Latitude") as HTMLInputElement).value).toBe(
      "-87",
    ),
  );
  expect((screen.getByLabelText("Longitude") as HTMLInputElement).value).toBe(
    "42",
  );
  expect(
    (
      screen.getByLabelText(
        "Observer height above terrain · m",
      ) as HTMLInputElement
    ).value,
  ).toBe("5");
  expect(
    (screen.getByLabelText("Start date & time · UTC") as HTMLInputElement)
      .value,
  ).toBe("2027-02-01T12:30");
  expect(
    (screen.getByLabelText("Observation duration") as HTMLSelectElement).value,
  ).toBe("72");
  expect((screen.getByRole("checkbox") as HTMLInputElement).checked).toBe(
    false,
  );
});
