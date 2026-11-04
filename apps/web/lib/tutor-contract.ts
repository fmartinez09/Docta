/**
 * Mirror of apps/api/src/docta_api/tutor_contract.py (tutor-intervention-v1/v2).
 * Parity is enforced by tests over tests/fixtures/tutor_contract.json.
 * Dependency-free on purpose: unknown fields and block types are rejected.
 */

export const CONTRACT_VERSION = "tutor-intervention-v2";
export const LEGACY_CONTRACT_VERSION = "tutor-intervention-v1";

export const SITUATIONS = [
  "ambiguous",
  "entry",
  "unsupported_claim",
  "partial",
  "contrastable_error",
  "missing_concept",
  "blocked",
  "grounded",
  "insufficient_evidence",
] as const;
export type Situation = (typeof SITUATIONS)[number];
export type Action = "another_hint" | "explain" | "view_source";

export type QuestionBlock = { type: "question"; text: string };
export type HintBlock = { type: "hint"; level: number; text: string };
export type ExplanationBlock = {
  type: "explanation";
  title: string;
  text: string;
};
export type DiagramNode = { id: string; label: string };
export type DiagramEdge = { source: string; target: string; label: string };
export type DiagramBlock = {
  type: "diagram";
  kind: "sequence" | "relations";
  title: string;
  nodes: DiagramNode[];
  edges: DiagramEdge[];
};
export type SourcesBlock = { type: "sources"; citation_indexes: number[] };
export type ActivityBlock = {
  type: "activity";
  question: string;
  options: { id: string; text: string }[];
  correct_option_id: string;
  hint: string;
  explanation: string;
};
export type Block =
  | QuestionBlock
  | HintBlock
  | ExplanationBlock
  | DiagramBlock
  | ActivityBlock
  | SourcesBlock;
export type InterventionCitation = { chunk_id: string; quote: string };
export type TutorIntervention = {
  version: typeof CONTRACT_VERSION | typeof LEGACY_CONTRACT_VERSION;
  situation: Situation;
  blocks: Block[];
  citations: InterventionCitation[];
  grounded: boolean;
};

export class ContractError extends Error {}

type Obj = Record<string, unknown>;

function fail(message: string): never {
  throw new ContractError(message);
}

function obj(value: unknown, keys: readonly string[], optional: readonly string[] = []): Obj {
  if (typeof value !== "object" || value === null || Array.isArray(value))
    fail("object expected");
  const record = value as Obj;
  for (const key of Object.keys(record))
    if (!keys.includes(key) && !optional.includes(key)) fail(`unknown field ${key}`);
  for (const key of keys) if (!(key in record)) fail(`missing field ${key}`);
  return record;
}

function str(value: unknown, min: number, max: number): string {
  if (typeof value !== "string" || value.length < min || value.length > max)
    fail("invalid string");
  return value;
}

function list(value: unknown, min: number, max: number): unknown[] {
  if (!Array.isArray(value) || value.length < min || value.length > max)
    fail("invalid list");
  return value;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const NODE_ID = /^[a-z][a-z0-9_]{0,23}$/;

function block(value: unknown): Block {
  const type = (value as Obj | null)?.type;
  switch (type) {
    case "question": {
      const v = obj(value, ["type", "text"]);
      return { type, text: str(v.text, 1, 600) };
    }
    case "hint": {
      const v = obj(value, ["type", "level", "text"]);
      if (!Number.isInteger(v.level) || (v.level as number) < 1 || (v.level as number) > 3)
        fail("invalid hint level");
      return { type, level: v.level as number, text: str(v.text, 1, 600) };
    }
    case "explanation": {
      const v = obj(value, ["type", "title", "text"]);
      return { type, title: str(v.title, 1, 120), text: str(v.text, 1, 1500) };
    }
    case "diagram": {
      const v = obj(value, ["type", "kind", "title", "nodes", "edges"]);
      if (v.kind !== "sequence" && v.kind !== "relations") fail("invalid diagram kind");
      const nodes = list(v.nodes, 2, 12).map((item) => {
        const n = obj(item, ["id", "label"]);
        const id = str(n.id, 1, 24);
        if (!NODE_ID.test(id)) fail("invalid node id");
        return { id, label: str(n.label, 1, 80) };
      });
      const ids = new Set(nodes.map((n) => n.id));
      if (ids.size !== nodes.length) fail("duplicate diagram node");
      const edges = list(v.edges, 1, 20).map((item) => {
        const e = obj(item, ["source", "target", "label"]);
        const source = str(e.source, 1, 24);
        const target = str(e.target, 1, 24);
        if (!ids.has(source) || !ids.has(target)) fail("unknown diagram node");
        return { source, target, label: str(e.label, 0, 80) };
      });
      return { type, kind: v.kind, title: str(v.title, 1, 120), nodes, edges };
    }
    case "sources": {
      const v = obj(value, ["type", "citation_indexes"]);
      const indexes = list(v.citation_indexes, 1, 5).map((i) => {
        if (!Number.isInteger(i) || (i as number) < 0) fail("invalid citation index");
        return i as number;
      });
      return { type, citation_indexes: indexes };
    }
    case "activity": {
      const v = obj(value, ["type", "question", "options", "correct_option_id", "hint", "explanation"]);
      const options = list(v.options, 2, 4).map((item) => {
        const option = obj(item, ["id", "text"]);
        const id = str(option.id, 1, 24);
        if (!NODE_ID.test(id)) fail("invalid option id");
        return { id, text: str(option.text, 1, 600) };
      });
      if (new Set(options.map((o) => o.id)).size !== options.length ||
          new Set(options.map((o) => o.text)).size !== options.length)
        fail("duplicate activity option");
      const correct = str(v.correct_option_id, 1, 24);
      if (!options.some((o) => o.id === correct)) fail("unknown correct option");
      return {
        type, question: str(v.question, 1, 600), options, correct_option_id: correct,
        hint: str(v.hint, 1, 600), explanation: str(v.explanation, 1, 1500),
      };
    }
    default:
      return fail("unknown block type");
  }
}

/** Parses untrusted JSON into a TutorIntervention or throws ContractError. */
export function parseIntervention(value: unknown): TutorIntervention {
  const v = obj(value, ["version", "situation", "blocks", "citations", "grounded"]);
  if (v.version !== CONTRACT_VERSION && v.version !== LEGACY_CONTRACT_VERSION) fail("unsupported version");
  if (!(SITUATIONS as readonly unknown[]).includes(v.situation)) fail("invalid situation");
  if (typeof v.grounded !== "boolean") fail("grounded must be boolean");
  const blocks = list(v.blocks, 1, 5).map(block);
  if (v.version === LEGACY_CONTRACT_VERSION && blocks.some((b) => b.type === "activity"))
    fail("activity requires tutor-intervention-v2");
  const citations = list(v.citations, 0, 5).map((item) => {
    const c = obj(item, ["chunk_id", "quote"]);
    const id = str(c.chunk_id, 36, 36);
    if (!UUID.test(id)) fail("invalid chunk id");
    return { chunk_id: id, quote: str(c.quote, 10, 1600) };
  });
  const situation = v.situation as Situation;
  if (blocks.filter((b) => b.type === "question" || b.type === "activity").length > 1)
    fail("one primary question per turn");
  for (const b of blocks)
    if (b.type === "sources" && b.citation_indexes.some((i) => i >= citations.length))
      fail("sources reference a missing citation");
  if (situation === "insufficient_evidence") {
    if (
      v.grounded ||
      citations.length ||
      blocks.some((b) => b.type !== "question" && b.type !== "explanation")
    )
      fail("abstention carries no evidence");
  } else if (situation === "ambiguous") {
    if (v.grounded || citations.length || blocks.length !== 1 || blocks[0].type !== "question")
      fail("clarification must be one uncited question");
  } else if (!v.grounded || !citations.length) {
    fail("factual guidance requires citations");
  }
  return {
    version: v.version as TutorIntervention["version"],
    situation,
    blocks,
    citations,
    grounded: v.grounded,
  };
}

/** Deterministic plain-text rendering of the same content, diagrams included. */
export function equivalentText(intervention: TutorIntervention): string {
  return intervention.blocks
    .map((b) => {
      switch (b.type) {
        case "question":
          return b.text;
        case "hint":
          return `Pista ${b.level}: ${b.text}`;
        case "explanation":
          return `${b.title}. ${b.text}`;
        case "diagram": {
          const labels = new Map(b.nodes.map((n) => [n.id, n.label]));
          const steps = b.edges.map(
            (e) =>
              `${labels.get(e.source)} → ${labels.get(e.target)}` +
              (e.label ? ` (${e.label})` : ""),
          );
          const kind = b.kind === "sequence" ? "Secuencia" : "Relaciones";
          return `${kind}: ${b.title}. ${steps.join("; ")}.`;
        }
        case "sources":
          return `Fuentes: ${b.citation_indexes.map((i) => `[${i + 1}]`).join(" ")}.`;
        case "activity":
          return `Ahora te toca razonar: ${b.question}\n${b.options.map((o, i) => `${String.fromCharCode(65 + i)}. ${o.text}`).join("\n")}`;
      }
    })
    .join("\n\n");
}

/** Controls a turn exposes; the application, never the model, decides them. */
export function allowedActions(intervention: TutorIntervention): Action[] {
  if (
    intervention.situation === "insufficient_evidence" ||
    intervention.situation === "ambiguous"
  )
    return [];
  const actions: Action[] = [];
  if (intervention.situation !== "grounded") actions.push("another_hint");
  actions.push("explain");
  if (intervention.citations.length) actions.push("view_source");
  return actions;
}
