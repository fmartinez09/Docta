type ApiStatus = Readonly<{
  available: boolean;
}>;

type Fetch = typeof fetch;

export async function getApiStatus(apiBaseUrl: string, fetcher: Fetch = fetch): Promise<ApiStatus> {
  try {
    const response = await fetcher(`${apiBaseUrl}/api/v1/health/live`, {
      cache: "no-store",
      signal: AbortSignal.timeout(2000),
    });
    if (!response.ok) {
      return { available: false };
    }
    const payload: unknown = await response.json();
    return {
      available:
        typeof payload === "object" &&
        payload !== null &&
        "status" in payload &&
        payload.status === "ok",
    };
  } catch {
    return { available: false };
  }
}

