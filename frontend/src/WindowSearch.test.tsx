import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import WindowSearch from "./components/WindowSearch";
const candidate = (state: string, id: string) => ({
  job_id: id,
  state,
  start_utc: "2026-11-04T00:00:00Z",
  end_utc: "2026-11-05T00:00:00Z",
  conditions: [
    {
      name: "sunlight_percent",
      actual: 80,
      threshold: 70,
      operator: ">=",
      passed: state === "unknown" ? null : true,
    },
  ],
  unknown_seconds: { sun: state === "unknown" ? 3600 : 0, earth: 0 },
});
const result = {
  id: "saved-search",
  status: "partial",
  completed: 2,
  total: 2,
  matches: 1,
  limitation: "Grid only",
  candidates: [
    candidate("match", "match-job"),
    candidate("unknown", "unknown-job"),
  ],
};
const reply = (body: unknown, ok = true) =>
  ({ ok, json: async () => body }) as Response;
beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => reply(result)),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
test("submits explicit UTC mission and thresholds and saves the search", async () => {
  render(<WindowSearch sites={[]} onChoose={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: /Set sunlight example/ }));
  fireEvent.change(screen.getByLabelText("Search mission duration"), {
    target: { value: "48" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: /Search candidate starts/ }),
  );
  await screen.findByText("2 / 2 evaluated");
  const sent = vi
    .mocked(fetch)
    .mock.calls.find(([, init]) => init?.method === "POST")!;
  const body = JSON.parse(String(sent[1]?.body));
  expect(body.template.start_utc).toBe("2026-11-04T00:00:00.000Z");
  expect(body.template.end_utc).toBe("2026-11-06T00:00:00.000Z");
  expect(body).toMatchObject({
    min_sunlight_percent: 70,
    max_darkness_hours: 6,
    min_earth_hours: 0,
    start_step_hours: 24,
  });
  expect(JSON.parse(localStorage.getItem("lunar-window-search")!).id).toBe(
    "saved-search",
  );
});
test("unknown coverage is explained and excluded by confirmed-match filter", async () => {
  localStorage.setItem(
    "lunar-window-search",
    JSON.stringify({ id: "saved-search" }),
  );
  render(<WindowSearch sites={[]} onChoose={vi.fn()} />);
  await screen.findByText(/Unknown coverage: Sun 3600/);
  expect(screen.getAllByRole("article")).toHaveLength(2);
  fireEvent.click(screen.getByLabelText("Show confirmed matches only"));
  expect(screen.getAllByRole("article")).toHaveLength(1);
  expect(screen.queryByText(/not confirmed suitable/)).toBeNull();
});
test("opens the exact analysis or uses it as comparison A", async () => {
  localStorage.setItem(
    "lunar-window-search",
    JSON.stringify({ id: "saved-search" }),
  );
  const choose = vi.fn(async () => {});
  render(<WindowSearch sites={[]} onChoose={choose} />);
  await screen.findByText("2 / 2 evaluated");
  fireEvent.click(screen.getAllByRole("button", { name: /Open analysis/ })[0]);
  await waitFor(() => expect(choose).toHaveBeenCalledWith("match-job", false));
  await waitFor(() =>
    expect(
      (
        screen.getAllByRole("button", {
          name: /Use as comparison A/,
        })[0] as HTMLButtonElement
      ).disabled,
    ).toBe(false),
  );
  fireEvent.click(
    screen.getAllByRole("button", { name: /Use as comparison A/ })[0],
  );
  await waitFor(() => expect(choose).toHaveBeenCalledWith("match-job", true));
});
test("queue rejection is visible and leaves the form available", async () => {
  vi.mocked(fetch).mockResolvedValue(
    reply({ detail: "Search queue is full" }, false),
  );
  render(<WindowSearch sites={[]} onChoose={vi.fn()} />);
  fireEvent.click(
    screen.getByRole("button", { name: /Search candidate starts/ }),
  );
  expect((await screen.findByRole("alert")).textContent).toContain(
    "Search queue is full",
  );
  expect(localStorage.getItem("lunar-window-search")).toBeNull();
  expect(
    (screen.getByLabelText("Search mission duration") as HTMLInputElement)
      .disabled,
  ).toBe(false);
});
