import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { API_URL } from "./api";
import { Counters } from "./Counters";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderCounters() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <Counters />
    </QueryClientProvider>,
  );
}

test("shows pending, held, oldest waiting and provider state", async () => {
  server.use(http.get(`${API_URL}/metrics/summary`, () => HttpResponse.json({
    media_by_status: { pending: 3, held: 40 }, held_by_reason: {}, oldest_waiting_job_s: 12.4,
    unchecked_published: 0, breaker: "open",
  })));
  renderCounters();
  const counters = await screen.findByLabelText("Live counters");
  expect(counters).toHaveTextContent("Pending 3");
  expect(counters).toHaveTextContent("Held 40");
  expect(counters).toHaveTextContent("Oldest waiting 12 s");
  expect(counters).toHaveTextContent("Provider open");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("a failed metrics request says so", async () => {
  server.use(http.get(`${API_URL}/metrics/summary`, () => new HttpResponse(null, { status: 503 })));
  renderCounters();
  expect(await screen.findByRole("alert")).toHaveTextContent("HTTP 503");
});
