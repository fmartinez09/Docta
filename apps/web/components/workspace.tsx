"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api, humanError, type Course } from "@/lib/docta";
import { MaterialPanel } from "./material-panel";
import { TutorPanel } from "./tutor-panel";

export function Workspace({
  initialCourse,
  initialConversation,
}: {
  initialCourse?: string;
  initialConversation?: string;
}) {
  const [courses, setCourses] = useState<Course[]>([]);
  const [selected, setSelected] = useState(initialCourse ?? "");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const creation = useRef<{ title: string; key: string } | null>(null);
  const refresh = useCallback(async () => {
    try {
      setCourses(await api<Course[]>("courses"));
      setError("");
    } catch (error) {
      setError(humanError(error));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    void api<Course[]>("courses", { signal: controller.signal })
      .then(setCourses)
      .catch((error) => {
        if (!controller.signal.aborted) setError(humanError(error));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, []);
  const course = courses.find((course) => course.id === selected) ?? courses[0];

  async function create(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const normalized = title.trim();
    if (creation.current?.title !== normalized)
      creation.current = { title: normalized, key: crypto.randomUUID() };
    try {
      const result = await api<Course>("courses", {
        body: { title: normalized },
        key: creation.current.key,
      });
      await refresh();
      setSelected(result.id);
      setCreating(false);
      setTitle("");
      creation.current = null;
      window.history.replaceState(null, "", `/?course=${result.id}`);
    } catch (error) {
      setError(humanError(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Link href="/" className="brand">
          <span className="brand-mark">d</span>docta
          <span className="brand-dot">.</span>
        </Link>
        <div className="side-heading">
          <span>MI ESPACIO</span>
          <span className="tiny-dot" />
        </div>
        <div className="side-title">
          Mis cursos <span>{courses.length}</span>
        </div>
        <nav aria-label="Mis cursos">
          {courses.map((item) => (
            <button
              key={item.id}
              className={`course-link ${course?.id === item.id ? "selected" : ""}`}
              onClick={() => {
                setSelected(item.id);
                window.history.replaceState(null, "", `/?course=${item.id}`);
              }}
            >
              <span className="course-icon">
                {item.title.slice(0, 1).toUpperCase()}
              </span>
              <span>
                {item.title}
                <small>
                  {item.role === "teacher" ? "Docente" : "Estudiante"}
                </small>
              </span>
              {course?.id === item.id && <span aria-hidden="true">›</span>}
            </button>
          ))}
        </nav>
        <button className="new-course" onClick={() => setCreating(!creating)}>
          ＋ Crear curso
        </button>
        <div className="sidebar-bottom">
          <p>
            Aprender empieza
            <br />
            con una buena pregunta.
          </p>
          <form method="post" action="/auth/logout">
            <button className="quiet">Cerrar sesión ↗</button>
          </form>
        </div>
      </aside>
      <main className="workspace">
        <header className="topbar">
          <span>Tu espacio de aprendizaje</span>
          <span className="session-label">
            <i /> Sesión iniciada
          </span>
        </header>
        {error && (
          <div className="notice error" role="alert">
            {error} <button onClick={refresh}>Actualizar</button>{" "}
            <a href="/auth/login">Iniciar sesión</a>
          </div>
        )}
        {creating && (
          <form className="create-form panel" onSubmit={create}>
            <label htmlFor="course-title">Nombre del nuevo curso</label>
            <div className="input-row">
              <input
                id="course-title"
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                maxLength={200}
                required
                placeholder="Por ejemplo, Física I"
              />
              <button className="primary" disabled={busy || !title.trim()}>
                {busy ? "Creando…" : "Crear"}
              </button>
              <button
                type="button"
                className="quiet"
                onClick={() => setCreating(false)}
              >
                Cancelar
              </button>
            </div>
          </form>
        )}
        {loading ? (
          <p className="empty" role="status">
            Cargando tus cursos…
          </p>
        ) : course ? (
          <>
            <section className="course-header">
              <p className="eyebrow">
                {course.role === "teacher"
                  ? "ESPACIO DOCENTE"
                  : "ESPACIO DE ESTUDIO"}
              </p>
              <h1>{course.title}</h1>
              <p>Comprende el material, conecta ideas y avanza a tu ritmo.</p>
              <span
                className={`pill ${course.active_corpus_version_id ? "ready" : ""}`}
              >
                {course.active_corpus_version_id
                  ? "● Material publicado"
                  : "○ Sin material publicado"}
              </span>
            </section>
            <div
              className={`study-layout ${course.role === "student" ? "student-layout" : ""}`}
            >
              {course.role === "teacher" && (
                <MaterialPanel
                  key={`material-${course.id}`}
                  course={course}
                  onPublished={refresh}
                />
              )}
              <TutorPanel
                key={`tutor-${course.id}`}
                course={course}
                initialConversation={
                  initialCourse === course.id ? initialConversation : undefined
                }
              />
            </div>
          </>
        ) : (
          <section className="welcome-empty">
            <span className="empty-symbol">✳</span>
            <h1>
              Tu próximo descubrimiento
              <br />
              empieza aquí.
            </h1>
            <p>
              Aún no tienes cursos asignados. Si eres estudiante, solicita
              acceso a tu docente. Si preparas material, crea tu primer curso.
            </p>
            <button className="primary" onClick={() => setCreating(true)}>
              Crear mi primer curso
            </button>
          </section>
        )}
        <footer className="workspace-footer">
          DOCTA <span>Preguntar. Comprender. Aprender.</span>
        </footer>
      </main>
    </div>
  );
}
