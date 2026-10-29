import { render, screen } from "@testing-library/react";
import { App } from "./App";

test("renders the user switcher with all four seeded users", () => {
  render(<App />);
  const select = screen.getByLabelText("Acting as");
  expect(select).toBeInTheDocument();
  expect(screen.getAllByRole("option")).toHaveLength(4);
});
