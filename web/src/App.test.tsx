import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { App } from "./App";

test("renders the user switcher with all four seeded users", () => {
  globalThis.fetch = (() => new Promise(() => {})) as typeof fetch; // keep the queue loading
  render(
    <QueryClientProvider client={new QueryClient()}>
      <App />
    </QueryClientProvider>,
  );
  expect(screen.getByLabelText("Acting as")).toBeInTheDocument();
  expect(screen.getAllByRole("option")).toHaveLength(4);
});
