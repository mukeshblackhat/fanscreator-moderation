import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { API_URL } from "./api";
import { ReviewQueue, type ReviewItem } from "./ReviewQueue";

const ADMIN = { userId: "admin_u", workspaceId: "ws_unverified" };
const HELD: ReviewItem = {
  id: "7f1c2a9e-0000-4000-8000-000000000001",
  status: "held",
  reasons: ["review:real_person@frame=3"],
  created_at: "2026-10-29T05:00:00Z",
  held_reason_kind: "score",
};

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderQueue() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ReviewQueue who={ADMIN} />
    </QueryClientProvider>,
  );
}

// Incident symptom 5: moderators saw "Nothing to review" while 40 items waited.
test("a failed /review request shows an error with Retry, never 'Nothing to review'", async () => {
  let calls = 0;
  server.use(
    http.get(`${API_URL}/review`, () => {
      calls += 1;
      return calls === 1 ? new HttpResponse(null, { status: 500 }) : HttpResponse.json([HELD]);
    }),
  );
  renderQueue();

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("500");
  expect(screen.queryByText("Nothing to review")).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByText("Possible real person · frame 3")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("shows a loading state before the first response", async () => {
  server.use(http.get(`${API_URL}/review`, () => new Promise(() => {})));
  renderQueue();
  expect(screen.getByRole("status")).toHaveTextContent(/loading/i);
  expect(screen.queryByText("Nothing to review")).not.toBeInTheDocument();
});

test("shows 'Nothing to review' only after a successful empty response", async () => {
  server.use(http.get(`${API_URL}/review`, () => HttpResponse.json([])));
  renderQueue();
  expect(await screen.findByText("Nothing to review")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("approve is disabled for items that were never screened", async () => {
  server.use(
    http.get(`${API_URL}/review`, () =>
      HttpResponse.json([{ ...HELD, held_reason_kind: "screening_unavailable", reasons: [] }]),
    ),
  );
  renderQueue();
  await waitFor(() => expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled());
  expect(screen.getByRole("button", { name: "Reject" })).toBeEnabled();
});
