import { describe, expect, it, vi } from "vitest";

import { readWebConfig } from "../lib/config";

vi.mock("node:fs", async (importOriginal) => ({
  ...(await importOriginal<typeof import("node:fs")>()),
  existsSync: () => true,
}));

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

describe("root environment loading", () => {
  it("ignores a root environment in the managed container", async () => {
    vi.stubEnv("DOCTA_DEV_MANAGED", "1");
    const load = vi.spyOn(process, "loadEnvFile").mockImplementation(() => {});
    try {
      vi.resetModules();
      await import("../next.config");
      expect(load).not.toHaveBeenCalled();
    } finally {
      vi.restoreAllMocks();
      vi.unstubAllEnvs();
    }
  });

  it("preserves root environment loading for native setup", async () => {
    vi.stubEnv("DOCTA_DEV_MANAGED", undefined);
    const load = vi.spyOn(process, "loadEnvFile").mockImplementation(() => {});
    try {
      vi.resetModules();
      await import("../next.config");
      expect(load).toHaveBeenCalledOnce();
    } finally {
      vi.restoreAllMocks();
      vi.unstubAllEnvs();
    }
  });
});
