export type WebConfig = Readonly<{
  apiBaseUrl: string;
}>;

type WebEnvironment = Readonly<Record<string, string | undefined>>;

export function readWebConfig(environment: WebEnvironment = process.env): WebConfig {
  const rawUrl = environment.DOCTA_API_BASE_URL ?? "http://127.0.0.1:8000";
  const url = new URL(rawUrl);
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("DOCTA_API_BASE_URL must use http or https");
  }
  return { apiBaseUrl: url.toString().replace(/\/$/, "") };
}
