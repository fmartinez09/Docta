import { describe, expect, it, vi } from "vitest";

import { getApiStatus } from "../lib/api-status";

describe("getApiStatus", () => {
  it("reports a validated healthy API response", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      Response.json({ status: "ok", service: "docta-api" }),
    );

    await expect(getApiStatus("http://api:8000", fetcher)).resolves.toEqual({ available: true });
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it("fails closed on malformed or unreachable responses", async () => {
    const malformed = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ status: "maybe" }));
    const unreachable = vi.fn<typeof fetch>().mockRejectedValue(new Error("connection refused"));

    await expect(getApiStatus("http://api:8000", malformed)).resolves.toEqual({
      available: false,
    });
    await expect(getApiStatus("http://api:8000", unreachable)).resolves.toEqual({
      available: false,
    });
  });
});

