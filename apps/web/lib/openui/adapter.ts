import { createParser } from "@openuidev/react-lang";
import type { TutorIntervention } from "../tutor-contract";
import { tutorLibrary } from "./library";

export type CitationView = {
  chunk_id: string;
  document_title: string;
  page: number;
  page_end: number;
  quote: string;
  fragment: string;
  document_version_id: string;
};

export class AdapterError extends Error {}

export type ProgramCheck = { ok: true } | { ok: false; reason: string };

/** Quotes a string as an OpenUI Lang literal (double quotes, backslash escapes). */
function lit(value: string): string {
  // Control characters other than \n and \t carry no meaning for students.
  const clean = value.replace(/[\u0000-\u0008\u000b-\u001f\u007f\u2028\u2029]/g, " ");
  return JSON.stringify(clean);
}

function num(value: number): string {
  if (!Number.isFinite(value)) throw new AdapterError("non-finite number");
  return String(value);
}

/**
 * Deterministically turns a validated intervention into an OpenUI Lang program:
 * one `root = TutorResponse([...])` statement. Pure; no model call, no network.
 * Throws AdapterError when the content cannot be represented faithfully.
 */
export function toOpenUiLang(
  intervention: TutorIntervention,
  citations: readonly CitationView[],
): string {
  const blocks = intervention.blocks.map((b) => {
    switch (b.type) {
      case "question":
        return `GuidedQuestion(${lit(b.text)})`;
      case "hint":
        return `Hint(${num(b.level)}, ${lit(b.text)})`;
      case "explanation":
        return `Explanation(${lit(b.title)}, ${lit(b.text)})`;
      case "activity": {
        const options = b.options.map((option) => `{id: ${lit(option.id)}, text: ${lit(option.text)}}`);
        return `Activity(${lit(b.question)}, [${options.join(", ")}], ${lit(b.correct_option_id)}, ${lit(b.hint)}, ${lit(b.explanation)})`;
      }
      case "diagram": {
        const nodes = b.nodes.map((n) => `{id: ${lit(n.id)}, label: ${lit(n.label)}}`);
        const edges = b.edges.map(
          (e) => `{source: ${lit(e.source)}, target: ${lit(e.target)}, label: ${lit(e.label)}}`,
        );
        return `Diagram(${lit(b.kind)}, ${lit(b.title)}, [${nodes.join(", ")}], [${edges.join(", ")}])`;
      }
      case "sources": {
        const cards = b.citation_indexes.map((index) => {
          const cited = intervention.citations[index];
          const view = citations.find((c) => c.chunk_id === cited?.chunk_id);
          if (!cited || !view) throw new AdapterError("missing citation view");
          return (
            `SourceCard(${num(index + 1)}, ${lit(view.document_title)}, ${num(view.page)}, ` +
            `${num(view.page_end)}, ${lit(cited.quote)}, ${lit(view.fragment)})`
          );
        });
        return `Sources([${cards.join(", ")}])`;
      }
    }
  });
  return `root = TutorResponse([${blocks.join(", ")}])`;
}

type Node = { type?: string; typeName?: string; props?: Record<string, unknown> };

function walk(value: unknown, visit: (node: Node) => void): void {
  if (Array.isArray(value)) return value.forEach((v) => walk(v, visit));
  if (typeof value !== "object" || value === null) return;
  const node = value as Node;
  if (node.type === "element") visit(node);
  for (const v of Object.values(node.props ?? node)) walk(v, visit);
}

/**
 * Parses and strictly validates a complete program against the library. The
 * OpenUI parser is permissive (it drops stray statements and renders what it
 * can), so any error, incompleteness, unresolved/orphaned name, unknown
 * component, extra statement or non-library root rejects the program.
 */
export function checkProgram(program: string): ProgramCheck {
  try {
    const parser = createParser(tutorLibrary.toJSONSchema(), tutorLibrary.root);
    const result = parser.parse(program);
    const meta = result.meta;
    if (!result.root) return { ok: false, reason: "no-root" };
    if (meta.errors.length) return { ok: false, reason: `error:${meta.errors[0].code}` };
    if (meta.incomplete) return { ok: false, reason: "incomplete" };
    if (meta.unresolved.length || meta.orphaned.length)
      return { ok: false, reason: "unresolved-or-orphaned" };
    const statements = program.split("\n").filter((line) => line.trim() !== "").length;
    if (meta.statementCount !== 1 || statements !== 1)
      return { ok: false, reason: "unexpected-statements" };
    if (result.root.typeName !== tutorLibrary.root)
      return { ok: false, reason: "wrong-root" };
    let unknown = false;
    walk(result.root, (node) => {
      if (!node.typeName || !tutorLibrary.components[node.typeName]) unknown = true;
    });
    return unknown ? { ok: false, reason: "unknown-component" } : { ok: true };
  } catch {
    return { ok: false, reason: "parse-failure" };
  }
}

/** Adapter plus strict check; never regenerates. */
export function buildCheckedProgram(
  intervention: TutorIntervention,
  citations: readonly CitationView[],
): { ok: true; program: string } | { ok: false; reason: string } {
  try {
    const program = toOpenUiLang(intervention, citations);
    const check = checkProgram(program);
    return check.ok ? { ok: true, program } : check;
  } catch {
    return { ok: false, reason: "adapter-failure" };
  }
}
