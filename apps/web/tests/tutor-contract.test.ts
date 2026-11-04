import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, it } from "vitest";
import {
  allowedActions,
  equivalentText,
  parseIntervention,
} from "../lib/tutor-contract";

const fixtures = JSON.parse(
  readFileSync(resolve(__dirname, "../../../tests/fixtures/tutor_contract.json"), "utf8"),
) as {
  cases: {
    name: string;
    valid: boolean;
    value: unknown;
    text?: string;
    actions?: string[];
  }[];
};

for (const item of fixtures.cases) {
  it(`shared fixture: ${item.name}`, () => {
    if (!item.valid) {
      expect(() => parseIntervention(item.value)).toThrow();
      return;
    }
    const parsed = parseIntervention(item.value);
    expect(equivalentText(parsed)).toBe(item.text);
    expect(allowedActions(parsed)).toEqual(item.actions);
  });
}
