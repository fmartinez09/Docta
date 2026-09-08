"use client";

import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  ThreadPrimitive,
  useExternalStoreRuntime,
  type AppendMessage,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  DoctaError,
  history,
  humanError,
  type Conversation,
  type Course,
  type Message,
} from "@/lib/docta";
import { readEvents } from "@/lib/sse";

const phases: Record<string, string> = {
  "message.accepted": "Pregunta guardada. Buscando en el material…",
  "retrieval.started": "Buscando en el material…",
  "retrieval.completed": "Encontramos material para revisar.",
  "generation.started": "El tutor está preparando una respuesta…",
  "validation.started": "Revisando la respuesta y sus citas…",
};

export function TutorPanel({
  course,
  initialConversation,
}: {
  course: Course;
  initialConversation?: string;
}) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [selected, setSelected] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState("");
  const [error, setError] = useState("");
  const [retry, setRetry] = useState<{ question: string; key: string } | null>(
    null,
  );
  const creationKey = useRef(crypto.randomUUID());
  const acceptedId = useRef<string | null>(null);
  const mounted = useRef(true);
  const stream = useRef<AbortController | null>(null);
  const historyRequest = useRef(0);
  const pending = messages.some((message) => message.state === "pending");

  const refresh = useCallback(async (id: string) => {
    const request = ++historyRequest.current;
    const rows = await history(id);
    if (mounted.current && request === historyRequest.current) {
      setMessages(rows);
      if (
        acceptedId.current &&
        rows.some(
          (row) => row.id === acceptedId.current && row.state !== "pending",
        )
      )
        setRetry(null);
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    historyRequest.current++;
    const controller = new AbortController();
    void api<Conversation[]>(`courses/${course.id}/conversations`, {
      signal: controller.signal,
    })
      .then(async (rows) => {
        const id =
          rows.find((row) => row.id === initialConversation)?.id ??
          rows[0]?.id ??
          "";
        if (!controller.signal.aborted) {
          setConversations(rows);
          setSelected(id);
        }
        if (id) {
          const rows = await history(id, controller.signal);
          if (!controller.signal.aborted) setMessages(rows);
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted) setError(humanError(error));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => {
      mounted.current = false;
      controller.abort();
      stream.current?.abort();
    };
  }, [course.id, initialConversation]);

  useEffect(() => {
    if (!pending || !selected) return;
    const timer = setInterval(() => {
      void refresh(selected).catch((error) => {
        if (mounted.current) setError(humanError(error));
      });
    }, 2000);
    return () => clearInterval(timer);
  }, [pending, selected, refresh]);

  async function selectConversation(id: string) {
    setLoading(true);
    setError("");
    setRetry(null);
    acceptedId.current = null;
    try {
      await refresh(id);
      setSelected(id);
      window.history.replaceState(
        null,
        "",
        `/?course=${course.id}&conversation=${id}`,
      );
    } catch (error) {
      setError(humanError(error));
    } finally {
      setLoading(false);
    }
  }

  async function createConversation() {
    historyRequest.current++;
    const conversation = await api<Conversation>(
      `courses/${course.id}/conversations`,
      { key: creationKey.current },
    );
    creationKey.current = crypto.randomUUID();
    if (mounted.current) {
      setSelected(conversation.id);
      setMessages([]);
      setConversations((rows) => [
        conversation,
        ...rows.filter((row) => row.id !== conversation.id),
      ]);
    }
    window.history.replaceState(
      null,
      "",
      `/?course=${course.id}&conversation=${conversation.id}`,
    );
    return conversation.id;
  }

  async function newConversation() {
    setLoading(true);
    setError("");
    try {
      await createConversation();
    } catch (error) {
      setError(humanError(error));
    } finally {
      setLoading(false);
    }
  }

  async function send(question: string, key: string) {
    acceptedId.current = null;
    setBusy(true);
    setError("");
    setPhase("Enviando tu pregunta…");
    let id = selected;
    let terminal = false;
    try {
      if (!id) id = await createConversation();
      stream.current = new AbortController();
      const response = await fetch(`/api/docta/conversations/${id}/messages`, {
        method: "POST",
        credentials: "same-origin",
        signal: stream.current.signal,
        headers: { "Content-Type": "application/json", "Idempotency-Key": key },
        body: JSON.stringify({ question }),
      });
      if (!response.ok) {
        const error = await response.json();
        throw new DoctaError(error.code);
      }
      if (
        !response.body ||
        !response.headers.get("content-type")?.includes("text/event-stream")
      )
        throw new Error("Invalid response");
      await readEvents(response.body, (event) => {
        if (!mounted.current) return;
        if (event.event === "message.accepted") {
          const data = event.data as { message_id?: string };
          if (data.message_id) acceptedId.current = data.message_id;
          void refresh(id).catch(() => {});
        }
        if (phases[event.event]) setPhase(phases[event.event]);
        if (
          event.event === "message.completed" ||
          event.event === "message.failed"
        )
          terminal = true;
      });
      // Render answers only from durable JSON history, never arbitrary SSE content.
      await refresh(id);
      if (!terminal) throw new Error("Delivery interrupted");
      if (mounted.current) setRetry(null);
    } catch (error) {
      if (mounted.current) {
        setError(humanError(error));
        setRetry({ question, key });
      }
    } finally {
      if (mounted.current) {
        setBusy(false);
        setPhase("");
      }
    }
  }

  const externalMessages: ThreadMessageLike[] = messages.flatMap((message) => {
    const user: ThreadMessageLike = {
      id: `${message.id}-question`,
      role: "user",
      content: [{ type: "text", text: message.question }],
      createdAt: new Date(message.created_at),
    };
    if (!message.response) return [user];
    return [
      user,
      {
        id: `${message.id}-answer`,
        role: "assistant",
        content: [{ type: "text", text: message.response.answer }],
        status: { type: "complete", reason: "stop" },
      },
    ];
  });
  const runtime = useExternalStoreRuntime({
    messages: externalMessages,
    convertMessage: (message) => message,
    isRunning: busy || pending,
    isDisabled: loading || !course.active_corpus_version_id || !!retry,
    onNew: async (message: AppendMessage) => {
      const question = message.content
        .filter((part) => part.type === "text")
        .map((part) => part.text)
        .join("\n")
        .trim();
      if (question) await send(question, crypto.randomUUID());
    },
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadPrimitive.Root className="tutor panel">
        <div className="tutor-heading">
          <div className="panel-heading">
            <span className="tutor-avatar">✳</span>
            <div>
              <h2>Aprende con Docta</h2>
              <p>Una pregunta, una pista, un paso más.</p>
            </div>
          </div>
          <button
            className="quiet"
            disabled={busy || pending || loading || !!retry}
            onClick={newConversation}
          >
            ＋ Nueva conversación
          </button>
        </div>
        {conversations.length > 0 && (
          <div className="conversation-picker">
            <label htmlFor="conversation-select">Conversación</label>
            <select
              id="conversation-select"
              value={selected}
              disabled={busy || pending || loading || !!retry}
              onChange={(event) => selectConversation(event.target.value)}
            >
              {conversations.map((row, index) => (
                <option value={row.id} key={row.id}>
                  {new Date(row.created_at).toLocaleDateString("es-CL")} ·
                  Conversación {conversations.length - index}
                </option>
              ))}
            </select>
          </div>
        )}
        <ThreadPrimitive.Viewport className="thread-body">
          {loading ? (
            <p className="empty" role="status">
              Recuperando la conversación…
            </p>
          ) : (
            messages.length === 0 && (
              <div className="thread-welcome">
                <span className="welcome-star">✳</span>
                <h3>
                  Hagamos espacio
                  <br />
                  para tus preguntas.
                </h3>
                <p>
                  Cuéntame qué concepto estás revisando o qué paso te cuesta
                  entender. Buscaremos una pista en el material del curso.
                </p>
                <div className="study-prompts">
                  <span>Comprender un concepto</span>
                  <span>Revisar un procedimiento</span>
                  <span>Encontrar una pista</span>
                </div>
              </div>
            )
          )}
          {messages.map((message) => (
            <article className="turn" key={message.id}>
              <div className="student-message">
                <span className="message-label">TÚ</span>
                <p>{message.question}</p>
              </div>
              {message.state === "pending" && (
                <p className="pending-state" role="status">
                  <span className="pulse" />
                  {phase || "Pregunta guardada. El tutor está trabajando…"}
                </p>
              )}
              {message.state === "failed" && (
                <div className="failed-message" role="alert">
                  <strong>No pudimos completar esta respuesta</strong>
                  <p>
                    {humanError(
                      new DoctaError(message.failure_code ?? "INTERNAL_ERROR"),
                    )}
                  </p>
                </div>
              )}
              {message.state === "completed" && message.response && (
                <div className="assistant-message">
                  <span className="message-label">
                    ✳ DOCTA{" "}
                    {message.response.mode === "abstain" && (
                      <span className="abstention-label">
                        · Evidencia insuficiente
                      </span>
                    )}
                  </span>
                  <p className="answer-text">{message.response.answer}</p>
                  {message.response.citations.length > 0 && (
                    <div className="citations">
                      <span className="citation-label">
                        EN EL MATERIAL DEL CURSO
                      </span>
                      {message.response.citations.map((citation) => (
                        <details key={citation.chunk_id} className="citation">
                          <summary>
                            <span>▤ {citation.document_title}</span>
                            <span>
                              Pág. {citation.page}
                              {citation.page_end !== citation.page
                                ? `–${citation.page_end}`
                                : ""}{" "}
                              ↗
                            </span>
                          </summary>
                          <blockquote>{citation.quote}</blockquote>
                          <small>
                            Fragmento {citation.fragment} · Versión{" "}
                            {citation.document_version_id}
                          </small>
                        </details>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </article>
          ))}
        </ThreadPrimitive.Viewport>
        <div className="composer-area">
          {!course.active_corpus_version_id && (
            <p className="notice">
              El docente debe publicar un PDF para comenzar a estudiar.
            </p>
          )}
          {error && (
            <div className="notice error" role="alert">
              {error}{" "}
              {retry && (
                <button
                  disabled={busy}
                  onClick={() => send(retry.question, retry.key)}
                >
                  Recuperar envío
                </button>
              )}{" "}
              <a href="/auth/login">Iniciar sesión</a>
            </div>
          )}
          {busy && !pending && (
            <p className="inline-status" role="status">
              {phase}
            </p>
          )}
          <ComposerPrimitive.Root className="composer">
            <ComposerPrimitive.Input
              aria-label="Tu pregunta"
              placeholder="¿Qué te gustaría comprender?"
              maxLength={4000}
              rows={2}
            />
            <ComposerPrimitive.Send
              className="send-button"
              aria-label="Enviar pregunta"
            >
              ↑
            </ComposerPrimitive.Send>
          </ComposerPrimitive.Root>
          <p className="composer-note">
            Orientación basada en tu material. Abre las citas para revisar las
            fuentes.
          </p>
        </div>
      </ThreadPrimitive.Root>
    </AssistantRuntimeProvider>
  );
}
