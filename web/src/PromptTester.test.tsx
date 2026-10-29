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
    const names = { minor: "under_18_or_looks_young", sexual_violence: "non_consent_or_sexual_violence",
      bestiality: "animal_sexual_content", self_harm: "self_harm", real_person: "real_identifiable_person",
      incest: "incest", trafficking: "trafficking" };
    const fields = { ...Object.fromEntries(Object.values(names).map((n) => [n, false])), sexual_content: false,
      tries_to_trick_the_checker: false, under_18_or_looks_young: true };
    return HttpResponse.json({
      decision: "refuse", reasons: ["prompt:minor"], fields, scores: scores({ minor: 1, explicit: 0 }),
      hits: { minor: ["stated age under 18 -> 1.0"] }, field_threshold: 0.5, categories: CATS, field_names: names,
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

test("an example prompt is checked and the decision, fields and rule hits are shown", async () => {
  renderIt();
  await userEvent.click(within(screen.getByLabelText("Example prompts")).getByRole("button", { name: "Stated age (incident)" }));
  const verdict = await screen.findByLabelText("Prompt decision");
  expect(verdict).toHaveTextContent("refuse");
  expect(verdict).toHaveTextContent("Nothing is generated");
  const fields = screen.getByLabelText("Prompt fields");
  expect(within(fields).getByText("under_18_or_looks_young").parentElement).toHaveTextContent("true");
  expect(within(fields).getByText("sexual_content").parentElement).toHaveTextContent("false");
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

test("examples cover allow and refuse, and there is no review band", () => {
  const bands = new Set(EXAMPLES.map((e) => e.expect));
  expect(bands).toEqual(new Set(["allow", "refuse"]));
});
