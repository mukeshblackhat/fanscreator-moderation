import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { API_URL } from "./api";
import { EXAMPLES, PromptTester } from "./PromptTester";

const WHO = { userId: "member_v", workspaceId: "ws_verified" };
const CATS = ["minor", "sexual_violence", "bestiality", "self_harm", "real_person", "incest", "trafficking"];
const scores = (over: Record<string, number>) => ({ ...Object.fromEntries([...CATS, "explicit"].map((c) => [c, 0])), ...over });

let lastBody: unknown = null;
const server = setupServer(
  http.get(`${API_URL}/prompts/events`, () => HttpResponse.json({ counts: { allow: 2, refuse: 1 }, events: [] })),
  http.post(`${API_URL}/prompts/check`, async ({ request }) => {
    lastBody = await request.json();
    return HttpResponse.json({
      decision: "refuse", reasons: ["prompt:minor"], scores: scores({ minor: 1, explicit: 0 }),
      hits: { minor: ["stated age under 18 -> 1.0"] }, thresholds: { refuse: 0.8, review: 0.5 }, categories: CATS,
    });
  }),
);
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderIt() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><PromptTester who={WHO} /></QueryClientProvider>);
}

test("an example prompt is checked and the decision, scores and rule hits are shown", async () => {
  renderIt();
  await userEvent.click(within(screen.getByLabelText("Example prompts")).getByRole("button", { name: "Stated age" }));
  const verdict = await screen.findByLabelText("Prompt decision");
  expect(verdict).toHaveTextContent("refuse");
  expect(verdict).toHaveTextContent("Nothing is generated");
  expect(screen.getByText("1.00")).toBeInTheDocument();
  expect(screen.getByText(/stated age under 18/)).toBeInTheDocument();
  expect(lastBody).toEqual({ prompt: "a 16 year old at the beach" });
});

test("the negative prompt field is sent with the check", async () => {
  renderIt();
  await userEvent.click(within(screen.getByLabelText("Example prompts")).getByRole("button", { name: "Negative prompt trick" }));
  await screen.findByLabelText("Prompt decision");
  expect(lastBody).toEqual({ prompt: "nude portrait, explicit", negative_prompt: "adult, mature, wrinkles" });
  expect(screen.getByLabelText("Negative prompt")).toHaveValue("adult, mature, wrinkles");
});

test("there are example cases for every band", () => {
  const bands = new Set(EXAMPLES.map((e) => e.expect));
  expect(bands).toEqual(new Set(["allow", "review", "refuse"]));
});
