import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ApiError } from "../api/client";
import { ErrorMessage } from "./ErrorMessage";

describe("ErrorMessage", () => {
  it("shows the envelope message and trace id, not a static label", () => {
    render(<ErrorMessage error={new ApiError("file_too_large", "El archivo supera 25 MB", "tr-9", 413)} />);
    expect(screen.getByRole("alert")).toHaveTextContent("El archivo supera 25 MB");
    expect(screen.getByText("trace_id: tr-9")).toBeInTheDocument();
  });

  it("explains network failures", () => {
    render(<ErrorMessage error={new TypeError("fetch failed")} />);
    expect(screen.getByRole("alert")).toHaveTextContent("No se pudo conectar");
  });
});
