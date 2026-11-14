import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import Classroom from "./components/Classroom";
import ExportPanel from "./components/ExportPanel";
import ScenarioTransfer from "./components/ScenarioTransfer";
import App from "./App";
import example from "./fixtures/malapert";
vi.mock("./components/MoonScene", () => ({ default: () => null }));
const reply = (body: unknown) =>
  ({ ok: true, json: async () => body }) as Response;
beforeEach(() => {
  localStorage.clear();
  window.history.replaceState({}, "", "/");
  Element.prototype.scrollIntoView = vi.fn();
  vi.stubGlobal("fetch", vi.fn());
});
afterEach(() => {
  cleanup();
  localStorage.clear();
  window.history.replaceState({}, "", "/");
  vi.unstubAllGlobals();
});
test("export link preserves analysis, comparison and cursor without starting jobs", () => {
  render(
    <ExportPanel
      jobId="a-job"
      result={example}
      frameIndex={1}
      bId="b-job"
      b={example}
      cursor={4}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: /Export \/ Share/ }));
  const url = new URL(
    (screen.getByLabelText("Saved result link") as HTMLInputElement).value,
  );
  expect(url.searchParams.get("analysis")).toBe("a-job");
  expect(url.searchParams.get("compare")).toBe("b-job");
  expect(url.searchParams.get("cursor")).toBe("4");
  expect(url.searchParams.get("frame")).toBe("1");
  expect(fetch).not.toHaveBeenCalled();
});
test("shared analysis takes precedence over device history and restores selected sample", async () => {
  localStorage.setItem("lunar-analysis-id", "old-job");
  window.history.replaceState({}, "", "/?analysis=shared-job&frame=1");
  vi.mocked(fetch).mockImplementation(async (path) =>
    reply(
      String(path).endsWith("/presets")
        ? { sites: [] }
        : String(path).endsWith("/result")
          ? example
          : { id: "shared-job", status: "ready" },
    ),
  );
  render(<App />);
  await screen.findByText("20.5%");
  await waitFor(() =>
    expect(
      (
        screen.getByRole("slider", {
          name: "Observation time",
        }) as HTMLInputElement
      ).value,
    ).toBe("1"),
  );
  expect(
    vi
      .mocked(fetch)
      .mock.calls.some(([path]) => String(path).includes("old-job")),
  ).toBe(false);
});
test("classroom uses a lesson with hidden technical details and controllable sample playback", async () => {
  const lesson = {
    id: "changing-light",
    title: "Changing light",
    source_url: "https://example.org",
    limitation: "One observer only",
    cards: [{ title: "Terrain horizon", text: "A ridge can hide the Sun." }],
  };
  vi.mocked(fetch).mockImplementation(async (path, init) =>
    reply(
      path === "/api/classroom"
        ? { lessons: [lesson] }
        : init?.method === "POST"
          ? { id: "lesson-job" }
          : String(path).endsWith("/result")
            ? example
            : { id: "lesson-job", status: "ready" },
    ),
  );
  render(<Classroom onOpen={vi.fn()} />);
  fireEvent.click(
    await screen.findByRole("button", { name: /Start lesson: changing light/ }),
  );
  const slider = await screen.findByRole("slider", { name: "Lesson time" });
  expect(
    screen.getByText("Technical details and sources").closest("details")?.open,
  ).toBe(false);
  fireEvent.change(slider, { target: { value: "1" } });
  expect(screen.getByRole("heading", { name: "Sun: dark" })).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Lesson playback speed"), {
    target: { value: "250" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Play lesson" }));
  expect(screen.getByRole("button", { name: "Pause lesson" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Pause lesson" }));
});
test("import validation does not queue work; explicit calculation checks fingerprints", async () => {
  const manifest = {
    schema_version: 1,
    kind: "lunar-scenario",
    scenarios: [
      {
        submission: {
          site_id: "demo",
          start_utc: "2026-11-04T00:00:00Z",
          end_utc: "2026-11-05T00:00:00Z",
        },
        expected: {
          model_version: "v1",
          horizon_model_version: "h1",
          result_sha256: "a".repeat(64),
        },
      },
    ],
  };
  vi.mocked(fetch).mockImplementation(async (path) =>
    reply(
      path === "/api/scenarios/validate"
        ? manifest
        : path === "/api/analyses"
          ? { id: "import-job" }
          : path === "/api/scenarios/check"
            ? { matches: false }
            : { id: "import-job", status: "ready" },
    ),
  );
  render(<ScenarioTransfer onOpen={vi.fn()} />);
  fireEvent.click(
    screen.getByText("Import a shared scenario file", { exact: true }),
  );
  const file = new File([JSON.stringify(manifest)], "scenario.json", {
    type: "application/json",
  });
  Object.defineProperty(file, "text", {
    value: async () => JSON.stringify(manifest),
  });
  fireEvent.change(screen.getByLabelText("Import scenario JSON"), {
    target: { files: [file] },
  });
  await screen.findByText("Inputs validated. Nothing has been queued.");
  expect(
    vi.mocked(fetch).mock.calls.some(([path]) => path === "/api/analyses"),
  ).toBe(false);
  fireEvent.click(
    screen.getByRole("button", { name: /Calculate imported scenario/ }),
  );
  expect(
    await screen.findByText("Results differ from the shared snapshot"),
  ).toBeTruthy();
  expect(
    screen.getByRole("button", { name: /Open imported analysis/ }),
  ).toBeTruthy();
});
