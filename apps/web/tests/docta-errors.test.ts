import { describe, expect, it } from "vitest";

import { DoctaError, humanError } from "../lib/docta";

describe("humanError retrieval failures", () => {
  it.each(["RETRIEVAL_UNAVAILABLE", "RETRIEVAL_INDEX_MISSING"])(
    "maps %s to a Spanish message that keeps the question",
    (code) => {
      const message = humanError(new DoctaError(code));

      expect(message).toContain("Tu pregunta quedó guardada");
      expect(message).not.toContain(code);
    },
  );
});
