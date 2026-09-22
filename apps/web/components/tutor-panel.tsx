"use client";

import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useAuiState,
  useExternalStoreRuntime,
  type AppendMessage,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import {
  ArrowDown,
  ArrowUp,
  BookOpen,
  ChevronDown,
  CircleAlert,
  LoaderCircle,
  MessageSquarePlus,
  Sparkles,
} from "lucide-react";
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
  "message.accepted": "Pregunta guardada",
  "retrieval.started": "Consultando el material",
  "retrieval.completed": "Material encontrado",
  "generation.started": "Preparando una orientación",
  "validation.started": "Verificando respuesta y citas",
};

type TutorMessageMetadata = {
  state?: Message["state"];
  response?: Message["response"];
  failureCode?: string | null;
  phase?: string;
};

function useTutorMessageMetadata(): TutorMessageMetadata {
  return useAuiState(
    (state) => state.message.metadata.custom,
  ) as unknown as TutorMessageMetadata;
}

function StudentMessage() {
  return (
    <MessagePrimitive.Root className="turn user-turn">
      <div className="student-message">
        <MessagePrimitive.Parts />
      </div>
    </MessagePrimitive.Root>
  );
}

function TutorMessage() {
  const metadata = useTutorMessageMetadata();
  const status = useAuiState((state) => state.message.status);
  const pending = metadata.state === "pending" || status?.type === "running";

  if (pending) {
    return (
      <MessagePrimitive.Root className="turn assistant-turn">
        <p className="pending-state" role="status">
          <LoaderCircle className="spin" size={16} />
          {metadata.phase || "Preparando respuesta"}
        </p>
      </MessagePrimitive.Root>
    );
  }

  if (metadata.state === "failed") {
    return (
      <MessagePrimitive.Root className="turn assistant-turn">
        <div className="failed-message" role="alert">
          <CircleAlert size={17} />
          <div>
            <strong>No pudimos completar esta respuesta</strong>
            <p>
              {humanError(
                new DoctaError(metadata.failureCode ?? "INTERNAL_ERROR"),
              )}
            </p>
          </div>
        </div>
      </MessagePrimitive.Root>
    );
  }

  const response = metadata.response;
  return (
    <MessagePrimitive.Root className="turn assistant-turn">
      <div className="assistant-message">
        <div className="assistant-identity">
          <span className="assistant-avatar" aria-hidden="true">
            <Sparkles size={13} />
          </span>
          <strong>Docta</strong>
          {response?.mode === "abstain" && (
            <span className="abstention-label">Evidencia insuficiente</span>
          )}
        </div>
        <div className="answer-text">
          <MessagePrimitive.Parts />
        </div>
        {!!response?.citations.length && (
          <details className="citation">
            <summary>
              <span>
                <BookOpen size={15} />
                {response.citations.length}{" "}
                {response.citations.length === 1 ? "fuente" : "fuentes"}
              </span>
              <ChevronDown size={15} className="citation-chevron" />
            </summary>
            <div className="citation-list">
              {response.citations.map((citation) => (
                <div className="citation-item" key={citation.chunk_id}>
                  <div className="citation-heading">
                    <strong>{citation.document_title}</strong>
                    <span>
                      Pág. {citation.page}
                      {citation.page_end !== citation.page
                        ? `–${citation.page_end}`
                        : ""}
                    </span>
                  </div>
                  <blockquote>{citation.quote}</blockquote>
                  <small>
                    Fragmento {citation.fragment} · Versión{" "}
                    {citation.document_version_id}
                  </small>
                </div>
              ))}
            </div>
          </details>
        )}
      </div>
    </MessagePrimitive.Root>
  );
}

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
    const assistant: ThreadMessageLike = {
      id: `${message.id}-answer`,
      role: "assistant",
      content: message.response
        ? [{ type: "text", text: message.response.answer }]
        : [],
      status:
        message.state === "pending"
          ? { type: "running" }
          : message.state === "failed"
            ? { type: "incomplete", reason: "error" }
            : { type: "complete", reason: "stop" },
      metadata: {
        custom: {
          state: message.state,
          response: message.response,
          failureCode: message.failure_code,
          phase: message.state === "pending" ? phase : undefined,
        },
      },
    };
    return [
      user,
      assistant,
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
      <ThreadPrimitive.Root className="tutor">
        <div className="chat-toolbar">
          <div className="conversation-control">
            <label htmlFor="conversation-select">Conversación</label>
            {conversations.length > 0 ? (
              <div className="select-wrap">
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
                <ChevronDown size={15} aria-hidden="true" />
              </div>
            ) : (
              <span>Nueva conversación</span>
            )}
          </div>
          <button
            className="secondary-button compact-button"
            disabled={busy || pending || loading || !!retry}
            onClick={newConversation}
          >
            <MessageSquarePlus size={16} />
            <span>Nueva</span>
          </button>
        </div>
        <ThreadPrimitive.Viewport
          className={`thread-body ${!loading && messages.length === 0 ? "empty-thread" : ""}`}
        >
          {loading ? (
            <div className="message-skeleton" role="status">
              <span className="skeleton skeleton-line" />
              <span className="skeleton skeleton-line short" />
            </div>
          ) : null}
          <ThreadPrimitive.Messages>
            {({ message }) =>
              message.role === "user" ? (
                <StudentMessage />
              ) : message.role === "assistant" ? (
                <TutorMessage />
              ) : null
            }
          </ThreadPrimitive.Messages>
          <ThreadPrimitive.ViewportFooter className="composer-area">
            {!loading && messages.length === 0 && (
              <div className="thread-welcome">
                <h2>¿Cómo puedo ayudarte hoy?</h2>
              </div>
            )}
            <ThreadPrimitive.ScrollToBottom
              className="scroll-to-bottom"
              aria-label="Ir al final de la conversación"
            >
              <ArrowDown size={16} />
            </ThreadPrimitive.ScrollToBottom>
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
                )}
              </div>
            )}
            {busy && !pending && (
              <p className="inline-status" role="status">
                <LoaderCircle className="spin" size={15} /> {phase}
              </p>
            )}
            <ComposerPrimitive.Root className="composer">
              <ComposerPrimitive.Input
                aria-label="Tu pregunta"
                placeholder="Pregunta sobre el material…"
                maxLength={4000}
                rows={1}
              />
              <div className="composer-controls">
                <span className="composer-context">
                  <BookOpen size={14} />
                  Material del curso
                </span>
                <ComposerPrimitive.Send
                  className="send-button"
                  aria-label="Enviar pregunta"
                >
                  <ArrowUp size={16} strokeWidth={2} />
                </ComposerPrimitive.Send>
              </div>
            </ComposerPrimitive.Root>
          </ThreadPrimitive.ViewportFooter>
        </ThreadPrimitive.Viewport>
      </ThreadPrimitive.Root>
    </AssistantRuntimeProvider>
  );
}
