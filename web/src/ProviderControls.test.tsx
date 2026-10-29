import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { API_URL, PROVIDER_URL } from "./api";
import { ProviderControls } from "./ProviderControls";

let mode = "ok";
const sent: unknown[] = [];
const server = setupServer(
  http.get(`${PROVIDER_URL}/_stats`, () => HttpResponse.json({ mode, calls: { a: 2, b: 1 } })),
  http.post(`${PROVIDER_URL}/_control`, async ({ request }) => {
    const body = (await request.json()) as { mode: string };
    sent.push(body);
    mode = body.mode;
    return HttpResponse.json(body);
  }),
  http.post(`${PROVIDER_URL}/_reset`, () => { mode = "ok"; return HttpResponse.json({ status: "reset" }); }),
  http.get(`${API_URL}/real-mode`, () => HttpResponse.json({ available: false })),
);
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => { server.resetHandlers(); sent.length = 0; mode = "ok"; });
afterAll(() => server.close());

function renderIt() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><ProviderControls /></QueryClientProvider>);
}

test("shows the current mode and call count", async () => {
  renderIt();
  expect(await screen.findByText(/3 calls received/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Healthy" })).toHaveAttribute("aria-pressed", "true");
});

test("switching to outage posts the mode to the provider", async () => {
  renderIt();
  const bar = await screen.findByLabelText("Provider controls");
  await userEvent.click(within(bar).getByRole("button", { name: "Outage" }));
  expect(sent).toContainEqual({ mode: "timeout", delay_ms: 0, duplicate_callbacks: false });
});

test("slow mode sends the chosen delay", async () => {
  renderIt();
  const input = await screen.findByLabelText("Slow delay in ms");
  await userEvent.clear(input);
  await userEvent.type(input, "3000");
  await userEvent.click(screen.getByRole("button", { name: "Slow" }));
  expect(sent).toContainEqual({ mode: "slow", delay_ms: 3000, duplicate_callbacks: false });
});

test("duplicate callbacks can be switched on", async () => {
  renderIt();
  await userEvent.click(await screen.findByLabelText("Duplicate callbacks"));
  expect(sent).toContainEqual({ mode: "ok", delay_ms: 0, duplicate_callbacks: true });
});
