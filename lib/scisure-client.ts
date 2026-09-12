export class SciSureError extends Error {
  constructor(
    public status: number,
    message: string,
    public uncertain = false,
  ) {
    super(message);
  }
}
export function tenantOrigin(value: string) {
  const u = new URL(value);
  if (
    u.origin !== value ||
    u.protocol !== "https:" ||
    u.hostname !== "sandbox.elabjournal.com" ||
    u.port ||
    u.username ||
    u.password
  )
    throw new SciSureError(
      503,
      "This release only connects to the verified SciSure sandbox hostname.",
    );
  return u.origin;
}
export function remoteId(value: unknown): number {
  const n = Number(value);
  if (
    (typeof value !== "number" && typeof value !== "string") ||
    !/^[1-9]\d*$/.test(String(value)) ||
    !Number.isSafeInteger(n) ||
    n <= 0
  )
    throw new SciSureError(
      502,
      "SciSure returned an invalid record identifier.",
    );
  return n;
}
export function writableExperiment(
  e: any,
  d: { groupId: number; studyId: number; experimentId: number },
) {
  if (
    e.experimentID !== d.experimentId ||
    e.groupID !== d.groupId ||
    e.studyID !== d.studyId ||
    e.deleted !== false ||
    e.template !== false ||
    e.signatureStatus !== "None"
  )
    throw new SciSureError(
      409,
      "The destination changed, is signed, or is unavailable for editing. Verify it again.",
    );
}
export class SciSureClient {
  readonly origin: string;
  constructor(
    base: string,
    private token: string,
    private send: typeof fetch = fetch,
  ) {
    this.origin = tenantOrigin(base);
    if (!token.trim() || /[\r\n]/.test(token))
      throw new SciSureError(
        503,
        "Add the SciSure API token to the deployment secret store.",
      );
  }
  async request(
    path: string,
    options: {
      method?: "GET" | "POST" | "PUT";
      json?: unknown;
      bytes?: Uint8Array;
      binary?: boolean;
    } = {},
  ) {
    if (
      !path.startsWith("/api/v1/") ||
      path.includes("://") ||
      path.includes("..")
    )
      throw new SciSureError(400, "Unsupported SciSure operation.");
    const method = options.method || "GET",
      write = method !== "GET";
    let response: Response;
    try {
      response = await this.send(this.origin + path, {
        method,
        redirect: "manual",
        signal: AbortSignal.timeout(20000),
        headers: {
          Authorization: this.token,
          "X-Requested-With": "Swagger",
          Accept: options.binary
            ? "application/octet-stream"
            : "application/json",
          ...(options.json !== undefined
            ? { "Content-Type": "application/json" }
            : options.bytes
              ? { "Content-Type": "application/octet-stream" }
              : {}),
        },
        body:
          options.json !== undefined
            ? JSON.stringify(options.json)
            : (options.bytes as BodyInit | undefined),
      });
    } catch {
      throw new SciSureError(
        502,
        write
          ? "The SciSure write outcome is unknown. Use reconciliation before attempting another write."
          : "SciSure could not be reached. Try the connection check again.",
        write,
      );
    }
    if (!response.ok) {
      await response.body?.cancel();
      throw new SciSureError(
        response.status === 401 || response.status === 403 ? 403 : 502,
        `SciSure returned HTTP ${response.status}. ${write ? "The operation is paused for reconciliation." : "Check the token, permissions, and selected group."}`,
        write,
      );
    }
    try {
      const reader = response.body?.getReader(),
        chunks: Uint8Array[] = [];
      let size = 0;
      if (reader)
        for (;;) {
          const part = await reader.read();
          if (part.done) break;
          size += part.value.length;
          if (size > 12 * 1024 * 1024) {
            await reader.cancel();
            throw new Error("Response size");
          }
          chunks.push(part.value);
        }
      const bytes = new Uint8Array(size);
      let offset = 0;
      for (const chunk of chunks) {
        bytes.set(chunk, offset);
        offset += chunk.length;
      }
      if (options.binary) return bytes;
      if (!bytes.length) return null;
      return JSON.parse(
        new TextDecoder("utf-8", { fatal: true }).decode(bytes),
      );
    } catch {
      throw new SciSureError(
        502,
        write
          ? "SciSure returned an unreadable write response. Reconcile the operation."
          : "SciSure returned an unsupported response.",
        write,
      );
    }
  }
  async list(path: string) {
    const rows: any[] = [];
    for (let page = 0; page < 10; page++) {
      const result = await this.request(
        path +
          (path.includes("?") ? "&" : "?") +
          "%24page=" +
          page +
          "&%24records=100",
      );
      if (
        !result ||
        !Array.isArray(result.data) ||
        typeof result.hasNextPage !== "boolean"
      )
        throw new SciSureError(502, "Unexpected SciSure pagination response.");
      rows.push(...result.data);
      if (!result.hasNextPage) return rows;
    }
    throw new SciSureError(
      502,
      "The result exceeds 1,000 records. Narrow the SciSure test workspace.",
    );
  }
}
