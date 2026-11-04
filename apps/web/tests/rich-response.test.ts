import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { RichResponse } from "../components/rich-response";
import { buildCheckedProgram, checkProgram, toOpenUiLang, type CitationView } from "../lib/openui/adapter";
import { equivalentText, parseIntervention, type TutorIntervention } from "../lib/tutor-contract";

const fixtures = JSON.parse(
  readFileSync(resolve(__dirname, "../../../tests/fixtures/tutor_contract.json"), "utf8"),
) as { cases: { name: string; valid: boolean; value: unknown }[] };

const valid = fixtures.cases
  .filter((c) => c.valid)
  .map((c) => ({ name: c.name, intervention: parseIntervention(c.value) }));

function views(i: TutorIntervention): CitationView[] {
  return i.citations.map((c, n) => ({
    chunk_id: c.chunk_id,
    document_title: `Documento ${n + 1}`,
    page: 3,
    page_end: 4,
    quote: c.quote,
    fragment: `frag-${n + 1}`,
    document_version_id: "22222222-2222-4222-8222-222222222222",
  }));
}

const escapeHtml = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");

const noop = () => {};
const render = (props: Partial<Parameters<typeof RichResponse>[0]> & { intervention: TutorIntervention }) =>
  renderToString(
    createElement(RichResponse, { citations: views(props.intervention), onAction: noop, ...props }),
  );

describe("adapter over shared fixtures", () => {
  for (const { name, intervention } of valid) {
    it(`${name}: deterministic, passes the library check`, () => {
      const a = toOpenUiLang(intervention, views(intervention));
      expect(toOpenUiLang(intervention, views(intervention))).toBe(a);
      expect(checkProgram(a)).toEqual({ ok: true });
      expect(buildCheckedProgram(intervention, views(intervention)).ok).toBe(true);
    });

    it(`${name}: rich render carries the text, fallback carries text and citations`, () => {
      const rich = render({ intervention });
      expect(rich).not.toContain("rich-fallback");
      for (const b of intervention.blocks) {
        if (b.type === "question") expect(rich).toContain(escapeHtml(b.text));
        if (b.type === "diagram") {
          expect(rich).toContain(escapeHtml(b.title));
          for (const n of b.nodes) expect(rich).toContain(escapeHtml(n.label));
        }
        if (b.type === "activity") {
          expect(rich).toContain(escapeHtml(b.question));
          for (const option of b.options) expect(rich).toContain(escapeHtml(option.text));
          expect(rich).not.toContain(escapeHtml(b.hint));
          expect(rich).not.toContain(escapeHtml(b.explanation));
        }
      }
      const fallback = render({ intervention, richEnabled: false });
      expect(fallback).toContain("rich-fallback");
      if (intervention.blocks.some((b) => b.type === "activity")) {
        for (const block of intervention.blocks) {
          if (block.type === "activity") {
            expect(fallback).toContain(escapeHtml(block.question));
            for (const option of block.options) expect(fallback).toContain(escapeHtml(option.text));
            expect(fallback).not.toContain(escapeHtml(block.hint));
            expect(fallback).not.toContain(escapeHtml(block.explanation));
          } else {
            expect(fallback).toContain(escapeHtml(equivalentText({ ...intervention, blocks: [block] })));
          }
        }
      } else {
        expect(fallback).toContain(escapeHtml(equivalentText(intervention)));
      }
      for (const c of intervention.citations) expect(fallback).toContain(escapeHtml(c.quote));
    });
  }
});

describe("injection and rejection", () => {
  const evil = 'x") , Evil("y"\n root = Evil("z") ) ]) <script>alert(1)</script> \\ \t \u0001';
  const sequence = valid.find((v) => v.intervention.blocks.some((b) => b.type === "diagram"))!;

  it("malicious strings cannot add components or statements", () => {
    const base = sequence.intervention;
    const hostile: TutorIntervention = {
      ...base,
      blocks: base.blocks.map((b) =>
        b.type === "explanation"
          ? { ...b, title: evil, text: evil }
          : b.type === "diagram"
            ? { ...b, title: evil, nodes: b.nodes.map((n) => ({ ...n, label: evil })) }
            : b,
      ),
    };
    const program = toOpenUiLang(hostile, views(hostile));
    expect(program.split("\n")).toHaveLength(1);
    expect(checkProgram(program)).toEqual({ ok: true });
    const html = render({ intervention: hostile });
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toContain("rich-fallback");
  });

  it("rejects unknown components, parse errors and stray statements", () => {
    expect(checkProgram('root = TutorResponse([Evil("x")])').ok).toBe(false);
    expect(checkProgram('root = TutorResponse([GuidedQuestion("x")').ok).toBe(false);
    expect(checkProgram('root = TutorResponse([GuidedQuestion(1)])').ok).toBe(false);
    expect(checkProgram('root = GuidedQuestion("x")').ok).toBe(false);
    expect(checkProgram('root = TutorResponse([GuidedQuestion("x")])\nEvil("y")').ok).toBe(false);
    expect(checkProgram("").ok).toBe(false);
  });

  it("falls back when the adapter cannot represent the content", () => {
    const base = valid.find((v) => v.intervention.blocks.some((b) => b.type === "sources"))!.intervention;
    expect(buildCheckedProgram(base, []).ok).toBe(false);
    const html = render({ intervention: base, citations: [] });
    expect(html).toContain("rich-fallback");
    expect(html).toContain("No se pudo mostrar la vista enriquecida");
    expect(html).toContain(escapeHtml(equivalentText(base)));
    for (const c of base.citations) expect(html).toContain(escapeHtml(c.quote));
  });
});

describe("diagram presentation", () => {
  it("presents explanation lists and emphasis without interpreting HTML", () => {
    const base = valid.find((v) => v.intervention.blocks.some((b) => b.type === "explanation"))!.intervention;
    const value: TutorIntervention = { ...base, blocks: [{
      type: "explanation", title: "Ramas", text: "Dos ejemplos:\n\n- **Óptica**: luz\n- Mecánica: movimiento <script>"
    }] };
    const html = render({ intervention: value });
    expect(html).toContain("<ul>");
    expect(html).toContain("<strong>Óptica</strong>");
    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toContain("<script>");
  });

  it("orders a linear sequence from edges and keeps branch relations explicit", () => {
    const base = valid.find((v) => v.intervention.blocks.some((b) => b.type === "diagram"))!.intervention;
    const diagram = base.blocks.find((b) => b.type === "diagram")!;
    if (diagram.type !== "diagram") throw new Error("missing diagram");
    const nodes = [{ id: "a", label: "Inicio" }, { id: "b", label: "Medio" }, { id: "c", label: "Final" }];
    const linear: TutorIntervention = { ...base, blocks: [{ ...diagram, nodes, edges: [
      { source: "b", target: "c", label: "después" },
      { source: "a", target: "b", label: "primero" },
    ] }] };
    const html = render({ intervention: linear });
    expect(html).toContain("rich-sequence-card");
    expect(html.indexOf("Inicio")).toBeLessThan(html.indexOf("Medio"));
    expect(html.indexOf("Medio")).toBeLessThan(html.indexOf("Final"));
    expect(html).toContain("primero");
    expect(html).toContain("después");
    const branched: TutorIntervention = { ...base, blocks: [{ ...diagram, nodes, edges: [
      { source: "a", target: "b", label: "uno" },
      { source: "a", target: "c", label: "dos" },
    ] }] };
    const branchHtml = render({ intervention: branched });
    expect(branchHtml).not.toContain("rich-sequence-card");
    expect(branchHtml).toContain("uno");
    expect(branchHtml).toContain("dos");
  });
});

describe("actions", () => {
  it("shows validated citations even when the model omits a sources block", () => {
    const entry = valid.find((v) => v.name === "entry_question")!.intervention;
    const html = render({ intervention: entry });
    expect(html).not.toContain("rich-fallback");
    expect(html).toContain('aria-label="Fuentes"');
    expect(html).toContain(escapeHtml(entry.citations[0].quote));
  });

  it("renders only allowed actions and honours disabled", () => {
    const entry = valid.find((v) => v.name === "entry_question")!.intervention;
    const html = render({ intervention: entry, disabled: true });
    expect(html).toContain("Otra pista");
    expect(html).toContain("Explicar");
    expect(html).toContain("Ver fuente");
    expect(html.match(/disabled=""/g)?.length).toBe(3);
    const abstain = valid.find((v) => v.intervention.situation === "insufficient_evidence");
    if (abstain) expect(render({ intervention: abstain.intervention })).not.toContain("rich-action");
  });
});
