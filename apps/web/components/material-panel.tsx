"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  DoctaError,
  humanError,
  type Course,
  type DocumentSummary,
} from "@/lib/docta";

type Upload = {
  document_id: string;
  version_id: string;
  upload_url: string;
  headers: Record<string, string>;
};
const working = new Set(["QUEUED", "PROCESSING"]);
const labels: Record<string, string> = {
  AWAITING_UPLOAD: "Carga por completar",
  QUEUED: "En espera",
  PROCESSING: "Procesando páginas…",
  INDEXED: "Listo para publicar",
  FAILED: "No se pudo procesar",
  REJECTED: "PDF rechazado",
  OCR_REQUIRED: "Requiere un PDF con texto",
};

export function MaterialPanel({
  course,
  onPublished,
}: {
  course: Course;
  onPublished: () => Promise<void>;
}) {
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const attempt = useRef<{
    file: File;
    key: string;
    confirmation: string;
    uploaded: boolean;
    upload?: Upload;
  } | null>(null);
  const mounted = useRef(true);
  const refresh = useCallback(async () => {
    try {
      const data = await api<DocumentSummary[]>(
        `courses/${course.id}/documents`,
      );
      if (mounted.current) setDocuments(data);
    } catch (error) {
      if (mounted.current) setError(humanError(error));
    }
  }, [course.id]);
  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    void api<DocumentSummary[]>(`courses/${course.id}/documents`, {
      signal: controller.signal,
    })
      .then((data) => {
        if (!controller.signal.aborted) setDocuments(data);
      })
      .catch((error) => {
        if (!controller.signal.aborted) setError(humanError(error));
      });
    return () => {
      mounted.current = false;
      controller.abort();
    };
  }, [course.id]);
  useEffect(() => {
    if (!documents.some((document) => working.has(document.state))) return;
    const timer = setInterval(() => {
      void refresh();
    }, 2000);
    return () => clearInterval(timer);
  }, [documents, refresh]);

  async function uploadFile(event: React.FormEvent) {
    event.preventDefault();
    if (!file) return;
    setError("");
    setBusy(true);
    setStatus("Preparando el PDF…");
    try {
      if (
        !file.name.toLowerCase().endsWith(".pdf") ||
        file.size === 0 ||
        file.size > 10 * 1024 * 1024
      )
        throw new DoctaError("invalid_pdf");
      if (attempt.current?.file !== file)
        attempt.current = {
          file,
          key: crypto.randomUUID(),
          confirmation: crypto.randomUUID(),
          uploaded: false,
        };
      const operation = attempt.current;
      if (!operation.upload) {
        const digest = await crypto.subtle.digest(
          "SHA-256",
          await file.arrayBuffer(),
        );
        const sha256 = Array.from(new Uint8Array(digest), (value) =>
          value.toString(16).padStart(2, "0"),
        ).join("");
        operation.upload = await api<Upload>(
          `courses/${course.id}/documents/uploads`,
          {
            key: operation.key,
            body: {
              filename: file.name,
              size_bytes: file.size,
              sha256,
              content_type: "application/pdf",
            },
          },
        );
      }
      const target = operation.upload;
      if (!operation.uploaded) {
        setStatus("Subiendo el archivo…");
        const response = await fetch(target.upload_url, {
          method: "PUT",
          headers: target.headers,
          body: file,
          credentials: "omit",
          redirect: "error",
        });
        if (!response.ok) throw new DoctaError("upload_failed");
        operation.uploaded = true;
      }
      setStatus("Confirmando la carga…");
      await api(
        `courses/${course.id}/documents/${target.document_id}/versions/${target.version_id}/complete`,
        { key: operation.confirmation },
      );
      if (mounted.current) {
        setStatus("PDF recibido. Estamos preparando el material.");
        setFile(null);
        attempt.current = null;
      }
      await refresh();
    } catch (error) {
      if (attempt.current && !attempt.current.uploaded)
        attempt.current.upload = undefined;
      if (mounted.current) {
        setError(humanError(error));
        setStatus("");
      }
      await refresh();
    } finally {
      if (mounted.current) setBusy(false);
    }
  }

  async function publish(document: DocumentSummary) {
    setBusy(true);
    setError("");
    try {
      await api(`courses/${course.id}/corpus/activate`, {
        key: crypto.randomUUID(),
        body: {
          corpus_version_id: document.corpus_version_id,
          expected_course_version: course.version,
        },
      });
      await onPublished();
      setStatus("El material ya está disponible para estudiar.");
    } catch (error) {
      setError(humanError(error));
      await onPublished();
    } finally {
      setBusy(false);
    }
  }

  return (
    <aside className="material panel" aria-label="Material del curso">
      <div className="panel-heading">
        <span className="icon-box">▤</span>
        <div>
          <h2>Material del curso</h2>
          <p>El punto de partida para aprender.</p>
        </div>
      </div>
      <form onSubmit={uploadFile}>
        <label className="file-drop" htmlFor="pdf-file">
          <span aria-hidden="true">↑</span>
          <strong>{file?.name ?? "Añade un PDF"}</strong>
          <small>Texto seleccionable · Hasta 10 MB</small>
        </label>
        <input
          id="pdf-file"
          type="file"
          accept="application/pdf,.pdf"
          disabled={busy}
          onChange={(event) => {
            setFile(event.target.files?.[0] ?? null);
            setError("");
          }}
        />
        <button className="primary full" disabled={!file || busy}>
          {busy ? "Preparando material…" : "Subir PDF"}
        </button>
      </form>
      {status && (
        <p className="inline-status" role="status">
          {status}
        </p>
      )}
      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}
      <div className="document-list">
        {documents.map((document) => {
          const active =
            !!document.corpus_version_id &&
            document.corpus_version_id === course.active_corpus_version_id;
          return (
            <article className="document" key={document.version_id}>
              <span className="pdf-tag">PDF</span>
              <div className="document-info">
                <strong>{document.title}</strong>
                <small className={active ? "text-green" : ""}>
                  {active
                    ? "Publicado · Disponible para el tutor"
                    : (labels[document.state] ?? document.state)}
                </small>
                {document.failure_code && (
                  <p className="document-error">
                    {humanError(new DoctaError(document.failure_code))}
                  </p>
                )}
                {document.state === "INDEXED" && !active && (
                  <button
                    className="publish"
                    disabled={busy}
                    onClick={() => publish(document)}
                  >
                    Publicar material →
                  </button>
                )}
              </div>
            </article>
          );
        })}
      </div>
      <p className="material-note">
        El tutor consultará el material publicado. Las citas anteriores se
        conservan aunque publiques otro PDF.
      </p>
    </aside>
  );
}
