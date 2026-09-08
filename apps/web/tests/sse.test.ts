import { expect, it } from "vitest";
import { readEvents } from "../lib/sse";

it("decodes UTF-8 across byte boundaries, heartbeats and CRLF lifecycle events", async () => {
  const source =
    ': heartbeat\r\n\r\nevent: message.accepted\r\ndata: {"message_id":"uno"}\r\n\r\nevent: message.completed\ndata: {"text":"¿Física?"}\n\n';
  const bytes = new TextEncoder().encode(source);
  const events: unknown[] = [];
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const byte of bytes) controller.enqueue(Uint8Array.of(byte));
      controller.close();
    },
  });
  await readEvents(stream, (event) => events.push(event));
  expect(events).toEqual([
    { event: "message.accepted", data: { message_id: "uno" } },
    { event: "message.completed", data: { text: "¿Física?" } },
  ]);
});

it("rejects malformed events so the caller can recover durable history", async () => {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(
        new TextEncoder().encode("event: message.completed\ndata: invalid\n\n"),
      );
      controller.close();
    },
  });
  await expect(readEvents(stream, () => {})).rejects.toThrow();
});
