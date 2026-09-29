export type Course = {
  id: string;
  title: string;
  role: "teacher" | "student";
  version: number;
  active_corpus_version_id: string | null;
};
export type Conversation = {
  id: string;
  course_id: string;
  created_at: string;
};
export type DocumentSummary = {
  document_id: string;
  version_id: string;
  title: string;
  state: string;
  failure_code: string | null;
  corpus_version_id: string | null;
};
export type Citation = {
  chunk_id: string;
  document_version_id: string;
  document_title: string;
  page: number;
  page_end: number;
  fragment: string;
  quote: string;
};
export type Message = {
  id: string;
  sequence: number;
  question: string;
  state: "pending" | "completed" | "failed";
  response: {
    mode: string;
    answer: string;
    grounded: boolean;
    citations: Citation[];
  } | null;
  failure_code: string | null;
  created_at: string;
};
export type History = {
  messages: Message[];
  next_after_sequence: number | null;
};

export class DoctaError extends Error {
  constructor(public code: string) {
    super(code);
  }
}

export function humanError(error: unknown): string {
  const code = error instanceof DoctaError ? error.code : "network";
  const messages: Record<string, string> = {
    authentication_required:
      "Tu sesión venció. Vuelve a iniciar sesión para continuar.",
    conversation_not_found: "No tienes acceso a esta conversación.",
    course_not_found: "No tienes acceso a este curso.",
    conversation_busy: "Hay una pregunta en curso. Espera a que termine.",
    publication_conflict:
      "El material activo cambió. Actualiza el curso antes de volver a publicar.",
    MODEL_NOT_CONFIGURED:
      "El tutor aún no está configurado. Tu pregunta quedó guardada.",
    MODEL_UNAVAILABLE:
      "El tutor no está disponible en este momento. Tu pregunta quedó guardada.",
    MODEL_OUTPUT_INVALID:
      "No pudimos validar la respuesta del tutor. Tu pregunta quedó guardada.",
    COURSE_CORPUS_NOT_READY:
      "El docente debe publicar el material antes de consultar al tutor.",
    PROCESSING_INTERRUPTED:
      "La respuesta se interrumpió. Tu pregunta quedó guardada.",
    OCR_REQUIRED:
      "Este PDF no contiene texto seleccionable. Usa un PDF digital.",
    invalid_pdf: "Selecciona un PDF digital de hasta 10 MB.",
    upload_failed:
      "No pudimos confirmar la carga. Puedes reintentarlo con el mismo archivo.",
  };
  return (
    messages[code] ??
    "No pudimos completar la operación. Comprueba la conexión y vuelve a consultar el estado."
  );
}

export async function api<T>(
  path: string,
  options: { body?: unknown; key?: string; signal?: AbortSignal } = {},
): Promise<T> {
  const response = await fetch(`/api/docta/${path}`, {
    method: options.key ? "POST" : "GET",
    credentials: "same-origin",
    cache: "no-store",
    signal: options.signal,
    headers: {
      ...(options.body !== undefined
        ? { "Content-Type": "application/json" }
        : {}),
      ...(options.key ? { "Idempotency-Key": options.key } : {}),
    },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new DoctaError(error.code ?? "network");
  }
  return response.json();
}

export async function history(
  id: string,
  signal?: AbortSignal,
): Promise<Message[]> {
  const messages: Message[] = [];
  let cursor = 0;
  while (true) {
    const page = await api<History>(
      `conversations/${id}?limit=100&after_sequence=${cursor}`,
      { signal },
    );
    messages.push(...page.messages);
    if (page.next_after_sequence === null) return messages;
    if (page.next_after_sequence <= cursor)
      throw new DoctaError("invalid_history");
    cursor = page.next_after_sequence;
  }
}
