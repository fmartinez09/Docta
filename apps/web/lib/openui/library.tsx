import { createLibrary, defineComponent } from "@openuidev/react-lang";
import type { ReactNode } from "react";
import { z } from "zod/v4";
import { TutorActivity } from "../../components/tutor-activity";

/**
 * Closed OpenUI Lang library for tutor interventions (tutor-intervention-v1/v2).
 * Only these components exist: no arbitrary HTML, SVG, scripts or styles.
 * Arguments are positional, in the order of each Zod object below.
 */

export const COMPONENT_NAMES = [
  "TutorResponse",
  "GuidedQuestion",
  "Hint",
  "Explanation",
  "Activity",
  "Diagram",
  "Sources",
  "SourceCard",
] as const;

const GuidedQuestion = defineComponent({
  name: "GuidedQuestion",
  description: "The single guiding question for the student.",
  props: z.object({ text: z.string() }),
  component: ({ props }) => (
    <section className="rich-block rich-question" aria-label="Pregunta guía" tabIndex={0}>
      <h3 className="rich-label">Pregunta guía</h3>
      <p>{props.text}</p>
    </section>
  ),
});

const Hint = defineComponent({
  name: "Hint",
  description: "A graded hint, level 1 (subtle) to 3 (most direct).",
  props: z.object({ level: z.number(), text: z.string() }),
  component: ({ props }) => (
    <section
      className="rich-block rich-hint"
      aria-label={`Pista ${props.level} de 3`}
      tabIndex={0}
    >
      <h3 className="rich-label">{`Pista ${props.level} de 3`}</h3>
      <p>{props.text}</p>
    </section>
  ),
});

const Explanation = defineComponent({
  name: "Explanation",
  description: "A short explanation with a title.",
  props: z.object({ title: z.string(), text: z.string() }),
  component: ({ props }) => (
    <section className="rich-block rich-explanation" aria-label="Explicación" tabIndex={0}>
      <h3 className="rich-label">{props.title}</h3>
      <Prose text={props.text} />
    </section>
  ),
});

/** Render simple lists and emphasis as React text, never as model-supplied HTML. */
function Prose({ text }: { text: string }) {
  const groups = text.split(/\n\s*\n/).filter(Boolean);
  const inline = (value: string) => value.split(/(\*\*[^*\n]+\*\*)/g).map((part, i) =>
    part.startsWith("**") && part.endsWith("**")
      ? <strong key={i}>{part.slice(2, -2)}</strong>
      : part,
  );
  return <div className="rich-prose">{groups.map((group, i) => {
    const lines = group.split("\n");
    if (lines.every((line) => /^\s*[-*] +/.test(line))) {
      return <ul key={i}>{lines.map((line, j) =>
        <li key={j}>{inline(line.replace(/^\s*[-*] +/, ""))}</li>)}</ul>;
    }
    return <p key={i}>{inline(group)}</p>;
  })}</div>;
}

const Activity = defineComponent({
  name: "Activity",
  description: "A single-choice reasoning activity with a hint and feedback.",
  props: z.object({
    question: z.string(),
    options: z.array(z.object({ id: z.string(), text: z.string() })),
    correct_option_id: z.string(),
    hint: z.string(),
    explanation: z.string(),
  }),
  component: ({ props }) => <TutorActivity activity={{ type: "activity", ...props }} />,
});

const diagramNode = z.object({ id: z.string(), label: z.string() });
const diagramEdge = z.object({ source: z.string(), target: z.string(), label: z.string() });

function linearSequence(nodes: { id: string; label: string }[], edges: { source: string; target: string; label: string }[]) {
  if (edges.length !== nodes.length - 1) return null;
  const starts = nodes.filter((node) => !edges.some((edge) => edge.target === node.id));
  if (starts.length !== 1) return null;
  const steps: { label: string; edgeLabel: string }[] = [];
  const visited = new Set<string>();
  let current = starts[0];
  while (current && !visited.has(current.id)) {
    visited.add(current.id);
    const outgoing = edges.filter((edge) => edge.source === current.id);
    if (outgoing.length > 1) return null;
    steps.push({ label: current.label, edgeLabel: outgoing[0]?.label ?? "" });
    const next = outgoing[0];
    if (!next) break;
    if (edges.filter((edge) => edge.target === next.target).length !== 1) return null;
    current = nodes.find((node) => node.id === next.target)!;
  }
  return visited.size === nodes.length ? steps : null;
}

const Diagram = defineComponent({
  name: "Diagram",
  description: "A sequence of steps or a set of relations between labelled nodes.",
  props: z.object({
    kind: z.enum(["sequence", "relations"]),
    title: z.string(),
    nodes: z.array(diagramNode),
    edges: z.array(diagramEdge),
  }),
  component: ({ props }) => {
    const labels = new Map(props.nodes.map((n) => [n.id, n.label]));
    const name = (id: string) => labels.get(id) ?? id;
    const sequence = props.kind === "sequence";
    const steps = sequence ? linearSequence(props.nodes, props.edges) : null;
    const connected = new Set(props.edges.flatMap((edge) => [edge.source, edge.target]));
    const items = props.edges.map((e, i) => (
      <li key={i}>
        <span className="rich-node">{name(e.source)}</span>
        <span className="rich-edge">
          {sequence ? " luego " : " se relaciona con "}
          {e.label ? `(${e.label}) ` : ""}
        </span>
        <span className="rich-node">{name(e.target)}</span>
      </li>
    ));
    return (
      <figure className="rich-block rich-diagram" tabIndex={0}>
        <figcaption className="rich-label">
          {`${sequence ? "Secuencia" : "Relaciones"}: ${props.title}`}
        </figcaption>
        {steps ? (
          <ol className="rich-sequence" aria-label={`Secuencia: ${props.title}`}>
            {steps.map((step, i) => (
              <li key={i}>
                <div className="rich-sequence-card"><strong>{i + 1}. {step.label}</strong></div>
                {i < steps.length - 1 && <div className="rich-sequence-arrow"><span aria-hidden="true">↓</span>{step.edgeLabel && <span>{step.edgeLabel}</span>}</div>}
              </li>
            ))}
          </ol>
        ) : (
          <>
            <ul aria-label={`${sequence ? "Secuencia" : "Relaciones"}: ${props.title}`}>{items}</ul>
            {props.nodes.filter((node) => !connected.has(node.id)).map((node) => (
              <p key={node.id} className="rich-node">{node.label}</p>
            ))}
          </>
        )}
      </figure>
    );
  },
});

const SourceCard = defineComponent({
  name: "SourceCard",
  description: "One citation card: number, document title, pages, quote and fragment id.",
  props: z.object({
    number: z.number(),
    title: z.string(),
    page: z.number(),
    pageEnd: z.number(),
    quote: z.string(),
    fragment: z.string(),
  }),
  component: ({ props }) => {
    const pages =
      props.pageEnd > props.page
        ? `páginas ${props.page}–${props.pageEnd}`
        : `página ${props.page}`;
    return (
      <article className="rich-source" aria-label={`Fuente ${props.number}`} tabIndex={0}>
        <h4>{`[${props.number}] ${props.title}`}</h4>
        <p className="rich-meta">{`${pages} · fragmento ${props.fragment}`}</p>
        <blockquote>{props.quote}</blockquote>
      </article>
    );
  },
});

const Sources = defineComponent({
  name: "Sources",
  description: "Citation cards for the evidence used.",
  props: z.object({ cards: z.array(SourceCard.ref) }),
  component: ({ props, renderNode }) => (
    <details className="rich-sources" aria-label="Fuentes">
      <summary>Fuentes consultadas <span>({props.cards.length})</span></summary>
      {(props.cards as unknown[]).map((card, i) => (
        <div key={i}>{renderNode(card) as ReactNode}</div>
      ))}
    </details>
  ),
});

const TutorResponse = defineComponent({
  name: "TutorResponse",
  description: "Container for the ordered blocks of one tutor turn.",
  props: z.object({
    blocks: z.array(
      z.union([GuidedQuestion.ref, Hint.ref, Explanation.ref, Diagram.ref, Activity.ref, Sources.ref]),
    ),
  }),
  component: ({ props, renderNode }) => (
    <div className="rich-response" role="group" aria-label="Respuesta del tutor">
      {(props.blocks as unknown[]).map((block, i) => (
        <div key={i}>{renderNode(block) as ReactNode}</div>
      ))}
    </div>
  ),
});

export const tutorLibrary = createLibrary({
  components: [TutorResponse, GuidedQuestion, Hint, Explanation, Diagram, Activity, Sources, SourceCard],
  root: "TutorResponse",
});
