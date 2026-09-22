"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import * as Dialog from "@radix-ui/react-dialog";
import {
  BookOpen,
  ChevronLeft,
  ChevronRight,
  FileText,
  LogOut,
  Menu,
  Plus,
  X,
} from "lucide-react";
import { api, humanError, type Course } from "@/lib/docta";
import { MaterialPanel } from "./material-panel";
import { TutorPanel } from "./tutor-panel";
import { Sheet } from "./ui/sheet";

function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <Link href="/" className={`brand ${compact ? "brand-compact" : ""}`}>
      <span className="brand-mark">d</span>
      {!compact && <span>docta</span>}
    </Link>
  );
}

function CourseNavigation({
  courses,
  activeCourse,
  compact,
  onSelect,
  onCreate,
}: {
  courses: Course[];
  activeCourse?: Course;
  compact?: boolean;
  onSelect: (id: string) => void;
  onCreate: () => void;
}) {
  return (
    <>
      <button
        className={`new-course ${compact ? "icon-button" : ""}`}
        onClick={onCreate}
        aria-label="Crear curso"
        title={compact ? "Crear curso" : undefined}
      >
        <Plus size={16} />
        {!compact && <span>Nuevo curso</span>}
      </button>
      {!compact && <p className="nav-label">Cursos</p>}
      <nav className="course-nav" aria-label="Mis cursos">
        {courses.map((item) => (
          <button
            key={item.id}
            className={`course-link ${activeCourse?.id === item.id ? "selected" : ""}`}
            aria-current={activeCourse?.id === item.id ? "page" : undefined}
            onClick={() => onSelect(item.id)}
            aria-label={compact ? item.title : undefined}
            title={compact ? item.title : undefined}
          >
            <span className="course-icon">
              {item.title.slice(0, 1).toUpperCase()}
            </span>
            {!compact && (
              <span className="course-copy">
                <strong>{item.title}</strong>
                <small>
                  {item.role === "teacher" ? "Docente" : "Estudiante"}
                </small>
              </span>
            )}
          </button>
        ))}
      </nav>
    </>
  );
}

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
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileNavigation, setMobileNavigation] = useState(false);
  const [materialOpen, setMaterialOpen] = useState(false);
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

  function selectCourse(id: string) {
    setSelected(id);
    setMobileNavigation(false);
    window.history.replaceState(null, "", `/?course=${id}`);
  }

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
      <aside
        className={`sidebar desktop-sidebar ${sidebarCollapsed ? "sidebar-collapsed" : ""}`}
      >
        <div className="sidebar-header">
          <Brand compact={sidebarCollapsed} />
          <button
            className="icon-button sidebar-toggle"
            onClick={() => setSidebarCollapsed((value) => !value)}
            aria-label={sidebarCollapsed ? "Expandir navegación" : "Contraer navegación"}
          >
            {sidebarCollapsed ? (
              <ChevronRight size={17} />
            ) : (
              <ChevronLeft size={17} />
            )}
          </button>
        </div>
        <CourseNavigation
          courses={courses}
          activeCourse={course}
          compact={sidebarCollapsed}
          onSelect={selectCourse}
          onCreate={() => setCreating(true)}
        />
        <form className="sidebar-bottom" method="post" action="/auth/logout">
          <button
            className={sidebarCollapsed ? "icon-button" : "sidebar-action"}
            aria-label="Cerrar sesión"
            title={sidebarCollapsed ? "Cerrar sesión" : undefined}
          >
            <LogOut size={17} />
            {!sidebarCollapsed && <span>Cerrar sesión</span>}
          </button>
        </form>
      </aside>

      <Sheet
        open={mobileNavigation}
        onOpenChange={setMobileNavigation}
        side="left"
        title="Docta"
        description="Selecciona un curso."
      >
        <CourseNavigation
          courses={courses}
          activeCourse={course}
          onSelect={selectCourse}
          onCreate={() => {
            setMobileNavigation(false);
            setCreating(true);
          }}
        />
        <form className="mobile-logout" method="post" action="/auth/logout">
          <button className="sidebar-action">
            <LogOut size={17} /> Cerrar sesión
          </button>
        </form>
      </Sheet>

      <main className="workspace">
        <header className="workspace-header">
          <button
            className="icon-button mobile-menu"
            onClick={() => setMobileNavigation(true)}
            aria-label="Abrir navegación"
          >
            <Menu size={19} />
          </button>
          {course ? (
            <>
              <div className="course-heading">
                <h1>{course.title}</h1>
                <span
                  className={`course-status ${course.active_corpus_version_id ? "ready" : ""}`}
                >
                  <i />
                  {course.active_corpus_version_id
                    ? "Material listo"
                    : "Sin material"}
                </span>
              </div>
              {course.role === "teacher" && (
                <Sheet
                  open={materialOpen}
                  onOpenChange={setMaterialOpen}
                  title="Material del curso"
                  description="Sube, revisa y publica el PDF que consultará el tutor."
                  trigger={
                    <button className="secondary-button">
                      <FileText size={17} /> Material
                    </button>
                  }
                >
                  <MaterialPanel
                    key={`material-${course.id}`}
                    course={course}
                    onPublished={refresh}
                  />
                </Sheet>
              )}
            </>
          ) : (
            <Brand />
          )}
        </header>
        {error && (
          <div className="notice error workspace-notice" role="alert">
            {error} <button onClick={refresh}>Actualizar</button>{" "}
            <a href="/auth/login">Iniciar sesión</a>
          </div>
        )}
        <Dialog.Root open={creating} onOpenChange={setCreating}>
          <Dialog.Portal>
            <Dialog.Overlay className="dialog-overlay" />
            <Dialog.Content className="modal">
              <div className="modal-header">
                <div>
                  <Dialog.Title>Crear curso</Dialog.Title>
                  <Dialog.Description>
                    Dale un nombre breve para reconocerlo fácilmente.
                  </Dialog.Description>
                </div>
                <Dialog.Close className="icon-button" aria-label="Cerrar">
                  <X size={18} />
                </Dialog.Close>
              </div>
              <form className="create-form" onSubmit={create}>
                <label htmlFor="course-title">Nombre del curso</label>
                <input
                  id="course-title"
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  maxLength={200}
                  required
                  autoFocus
                  placeholder="Por ejemplo, Física I"
                />
                <div className="modal-actions">
                  <Dialog.Close asChild>
                    <button type="button" className="secondary-button">
                      Cancelar
                    </button>
                  </Dialog.Close>
                  <button className="primary" disabled={busy || !title.trim()}>
                    {busy ? "Creando…" : "Crear curso"}
                  </button>
                </div>
              </form>
            </Dialog.Content>
          </Dialog.Portal>
        </Dialog.Root>
        {loading ? (
          <div className="workspace-loading" role="status">
            <span className="skeleton skeleton-title" />
            <span className="skeleton skeleton-line" />
          </div>
        ) : course ? (
          <div className="study-layout">
            <TutorPanel
              key={`tutor-${course.id}`}
              course={course}
              initialConversation={
                initialCourse === course.id ? initialConversation : undefined
              }
            />
          </div>
        ) : (
          <section className="welcome-empty">
            <span className="empty-symbol"><BookOpen size={25} /></span>
            <h1>Crea tu primer curso</h1>
            <p>
              Organiza un material y comienza una conversación de estudio.
            </p>
            <button className="primary" onClick={() => setCreating(true)}>
              <Plus size={17} /> Crear curso
            </button>
          </section>
        )}
      </main>
    </div>
  );
}
