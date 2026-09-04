import { getApiStatus } from "@/lib/api-status";
import { readWebConfig } from "@/lib/config";

export const dynamic = "force-dynamic";

export default async function Home() {
  const config = readWebConfig();
  const api = await getApiStatus(config.apiBaseUrl);

  return (
    <main>
      <p className="eyebrow">DOCTA · WALKING SKELETON</p>
      <h1>La base ejecutable está lista.</h1>
      <p className="summary">
        Esta pantalla comprueba el límite web y consulta la sonda pública del API. Los flujos de
        curso, documento y tutor se incorporan en los siguientes incrementos.
      </p>
      <section aria-labelledby="system-status">
        <div>
          <p className="label" id="system-status">
            Estado del sistema
          </p>
          <p className="detail">Web</p>
          <strong>Disponible</strong>
        </div>
        <div>
          <p className="detail">API</p>
          <strong className={api.available ? "available" : "unavailable"}>
            {api.available ? "Disponible" : "No disponible"}
          </strong>
        </div>
      </section>
    </main>
  );
}

