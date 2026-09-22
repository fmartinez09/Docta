import { cookies } from "next/headers";
import Link from "next/link";
import { ArrowRight } from "lucide-react";
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
          <span className="brand-mark">d</span>
          <span>docta</span>
        </Link>
      </header>
      <section className="landing-content">
        <div className="landing-copy">
          <span className="landing-kicker">Tutor de estudio con fuentes</span>
          <h1>Entiende. No memorices.</h1>
          <p className="landing-description">
            Pregunta sobre el material de tu curso y recibe orientación con
            citas verificables.
          </p>
          {configured ? (
            <a className="primary login-button" href="/auth/login">
              Entrar a Docta <ArrowRight size={17} />
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
        </div>
      </section>
    </main>
  );
}
