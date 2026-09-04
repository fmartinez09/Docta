import { describe, expect, it } from "vitest";

import { readWebConfig } from "../lib/config";

describe("readWebConfig", () => {
  it("normalizes a valid API base URL", () => {
    expect(readWebConfig({ DOCTA_API_BASE_URL: "http://api:8000/" })).toEqual({
      apiBaseUrl: "http://api:8000",
    });
  });

  it("rejects unsupported URL schemes", () => {
    expect(() => readWebConfig({ DOCTA_API_BASE_URL: "file:///tmp/api" })).toThrow(
      "must use http or https",
    );
  });
});

