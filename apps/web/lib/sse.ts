export type LifecycleEvent = { event: string; data: unknown };

export async function readEvents(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: LifecycleEvent) => void,
) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      buffer = buffer.replace(/\r\n/g, "\n");
      if (buffer.length > 1_000_000) throw new Error("Event too large");
      let boundary: number;
      while ((boundary = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const lines = block.split("\n");
        const name = lines
          .find((line) => line.startsWith("event:"))
          ?.slice(6)
          .trim();
        const data = lines
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart())
          .join("\n");
        if (name && data) onEvent({ event: name, data: JSON.parse(data) });
      }
      if (done) return;
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
