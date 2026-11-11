/**
 * Server-sent events from a `fetch` response body: `event:` and `data:` lines, blank-line
 * separated (the subset `POST /snapshots/{id}/ask` sends). Data is parsed as JSON.
 */

export interface ServerEvent {
  event: string;
  data: unknown;
}

function parseBlock(block: string): ServerEvent | null {
  let event = "message";
  const data: string[] = [];
  for (const raw of block.split("\n")) {
    const line = raw.endsWith("\r") ? raw.slice(0, -1) : raw;
    if (!line || line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    else if (field === "data") data.push(value);
  }
  if (data.length === 0) return null;
  const text = data.join("\n");
  try {
    return { event, data: JSON.parse(text) as unknown };
  } catch {
    return { event, data: text };
  }
}

/** Every event in the stream, as it arrives. */
export async function* readEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<ServerEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true }).replaceAll("\r\n", "\n");
      let end = buffer.indexOf("\n\n");
      while (end !== -1) {
        const parsed = parseBlock(buffer.slice(0, end));
        buffer = buffer.slice(end + 2);
        if (parsed) yield parsed;
        end = buffer.indexOf("\n\n");
      }
    }
    const rest = parseBlock(buffer + decoder.decode());
    if (rest) yield rest;
  } finally {
    reader.releaseLock();
  }
}
