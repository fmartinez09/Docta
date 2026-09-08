import { cookies } from "next/headers";
import Link from "next/link";
import { Workspace } from "@/components/workspace";
import { authConfig, unseal } from "@/lib/auth";

export const dynamic = "force-dynamic";

export default async function Home({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  let authenticated = false;
  let configured = true;
  try {
    const config = authConfig();
    authenticated =
      unseal((await cookies()).get("docta_session")?.value, config.secret)
        ?.kind === "session";
  } catch {
    configured = false;
  }
  if (authenticated)
    return (
      <Workspace
        initialCourse={
          typeof params.course === "string" ? params.course : undefined
        }
        initialConversation={
          typeof params.conversation === "string"
            ? params.conversation
            : undefined
        }
      />
    );
  return (
    <main className="landing">
      <header>
        <Link href="/" className="brand">
          <span className="brand-mark">d</span>docta
          <span className="brand-dot">.</span>
        </Link>
        <span className="eyebrow">TU ESPACIO PARA COMPRENDER</span>
      </header>
      <section className="landing-content">
        <div>
          <p className="eyebrow">APRENDER, UNA PREGUNTA A LA VEZ</p>
          <h1>
            Las buenas preguntas
            <br />
            abren nuevos caminos.
          </h1>
          <p className="landing-description">
            Un espacio para explorar el material de tu curso, encontrar pistas y
            construir tus propias respuestas. Siempre con las fuentes a mano.
          </p>
          {configured ? (
            <a className="primary login-button" href="/auth/login">
              Entrar a mi espacio <span>↗</span>
            </a>
          ) : (
            <p className="notice">
              El inicio de sesión aún no está disponible. Contacta a quien
              administra Docta.
            </p>
          )}
          {params.error && (
            <p className="notice error" role="alert">
              No pudimos iniciar tu sesión. Comprueba la conexión e inténtalo
              nuevamente.
            </p>
          )}
          <p className="landing-note">
            Tu material. Tus preguntas. Tu aprendizaje.
          </p>
        </div>
        <div className="learning-card" aria-label="Cómo te acompaña Docta">
          <span className="large-star">✳</span>
          <p className="eyebrow">UN POCO DE CURIOSIDAD</p>
          <h2>
            ¿Y si empezamos
            <br />
            por entender el porqué?
          </h2>
          <div className="learning-steps">
            <p>
              <span>01</span> Explora el material de tu curso
            </p>
            <p>
              <span>02</span> Pregunta, intenta y descubre
            </p>
            <p>
              <span>03</span> Comprueba las fuentes y avanza
            </p>
          </div>
        </div>
      </section>
      <footer>
        DOCTA <span>Hecho para acompañar el aprendizaje.</span>
      </footer>
    </main>
  );
}
