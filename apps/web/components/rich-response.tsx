"use client";

import { Renderer } from "@openuidev/react-lang";
import { Component, useMemo, useRef, useState, type ReactNode } from "react";
import { buildCheckedProgram, type CitationView } from "../lib/openui/adapter";
import { tutorLibrary } from "../lib/openui/library";
import { TutorActivity } from "./tutor-activity";
import {
  allowedActions,
  equivalentText,
  type Action,
  type TutorIntervention,
} from "../lib/tutor-contract";

export type RichResponseProps = {
  intervention: TutorIntervention;
  citations: CitationView[];
  /** Called with an action the application allowed; the model never decides. */
  onAction: (action: Action) => void;
  /** false => always the plain-text fallback with citations. Default true. */
  richEnabled?: boolean;
  /** Animate only newly completed responses after validation and persistence. */
  animate?: boolean;
  /** Disables every action button (double-click protection while a request runs). */
  disabled?: boolean;
};

const ACTION_LABELS: Record<Action, string> = {
  another_hint: "Otra pista",
  explain: "Explicar",
  view_source: "Ver fuente",
};

class RenderBoundary extends Component<
  { onFail: () => void; children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch() {
    this.props.onFail();
  }
  render() {
    return this.state.failed ? null : this.props.children;
  }
}

function CitationList({
  intervention,
  citations,
}: {
  intervention: TutorIntervention;
  citations: CitationView[];
}) {
  if (!intervention.citations.length) return null;
  return (
    <details className="rich-sources" aria-label="Fuentes">
      <summary>Fuentes consultadas <span>({intervention.citations.length})</span></summary>
    <ol className="rich-citations">
      {intervention.citations.map((cited, i) => {
        const view = citations.find((c) => c.chunk_id === cited.chunk_id);
        const pages = view
          ? view.page_end > view.page
            ? `páginas ${view.page}–${view.page_end}`
            : `página ${view.page}`
          : "";
        return (
          <li key={`${cited.chunk_id}-${i}`}>
            <strong>{`[${i + 1}] ${view?.document_title ?? "Fuente"}`}</strong>
            {view ? <span className="rich-meta">{` ${pages} · fragmento ${view.fragment}`}</span> : null}
            <blockquote>{cited.quote}</blockquote>
          </li>
        );
      })}
    </ol>
    </details>
  );
}

function TextFallback({
  intervention,
  citations,
}: {
  intervention: TutorIntervention;
  citations: CitationView[];
}) {
  return (
    <div className="rich-fallback" role="group" aria-label="Respuesta del tutor (texto)">
      {intervention.blocks.some((block) => block.type === "activity") ? (
        intervention.blocks.map((block, index) =>
          block.type === "activity" ? (
            <TutorActivity key={index} activity={block} />
          ) : (
            <p className="rich-text" key={index}>
              {equivalentText({ ...intervention, blocks: [block] })}
            </p>
          ),
        )
      ) : (
        <p className="rich-text">{equivalentText(intervention)}</p>
      )}
      <CitationList intervention={intervention} citations={citations} />
    </div>
  );
}

export function RichResponse({
  intervention,
  citations,
  onAction,
  richEnabled = true,
  animate = false,
  disabled = false,
}: RichResponseProps) {
  const [renderFailed, setRenderFailed] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const checked = useMemo(
    () => (richEnabled ? buildCheckedProgram(intervention, citations) : null),
    [richEnabled, intervention, citations],
  );
  const actions = allowedActions(intervention);
  const program = checked?.ok && !renderFailed ? checked.program : null;
  const fail = () => setRenderFailed(true);

  return (
    <div className={`rich-root${animate ? " rich-animate" : ""}`} ref={root}>
      {richEnabled && program === null && (
        <p className="rich-presentation-notice" role="status">
          No se pudo mostrar la vista enriquecida; se muestra la respuesta en texto.
        </p>
      )}
      {program !== null ? (
        <RenderBoundary onFail={fail}>
          <Renderer
            response={program}
            library={tutorLibrary}
            isStreaming={false}
            onError={(errors) => {
              if (errors.length) fail();
            }}
          />
        </RenderBoundary>
      ) : (
        <TextFallback intervention={intervention} citations={citations} />
      )}
      {program !== null && !intervention.blocks.some((block) => block.type === "sources") && (
        <CitationList intervention={intervention} citations={citations} />
      )}
      {actions.length ? (
        <div className="rich-actions" role="group" aria-label="Acciones">
          {actions.map((action) => (
            <button
              key={action}
              type="button"
              className="rich-action"
              disabled={disabled}
              onClick={() => {
                if (action === "view_source") {
                  const target = root.current?.querySelector<HTMLDetailsElement>('details[aria-label="Fuentes"]');
                  if (target) {
                    target.open = true;
                    target.scrollIntoView({ behavior: "smooth" });
                  }
                } else {
                  onAction(action);
                }
              }}
            >
              {ACTION_LABELS[action]}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
