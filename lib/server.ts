import "server-only";
import { env } from "cloudflare:workers";
import { getChatGPTUser } from "@/app/chatgpt-auth";
import { Problem } from "./problem";
export { Problem } from "./problem";
export const now = () => new Date().toISOString();
export const uid = () => crypto.randomUUID();
export async function digest(data: Uint8Array | string) {
  const b = typeof data === "string" ? new TextEncoder().encode(data) : data;
  return [
    ...new Uint8Array(await crypto.subtle.digest("SHA-256", b as BufferSource)),
  ]
    .map((x) => x.toString(16).padStart(2, "0"))
    .join("");
}
export function db() {
  if (!env.DB)
    throw new Problem(
      503,
      "The private database is not available. Please try again.",
    );
  return env.DB;
}
export function bucket() {
  if (!env.BUCKET)
    throw new Problem(
      503,
      "Private file storage is not available. Please try again.",
    );
  return env.BUCKET;
}
export async function setting(key: string) {
  return (
    await db()
      .prepare("SELECT value FROM settings WHERE key=?")
      .bind(key)
      .first<{ value: string }>()
  )?.value;
}
export async function actor() {
  const user = await getChatGPTUser();
  if (!user) throw new Problem(401, "Sign in to access CATALYST.");
  let owner = await setting("owner");
  if (!owner) {
    if (
      env.ALLOW_OWNER_BOOTSTRAP !== "true" &&
      process.env.NODE_ENV === "production"
    )
      throw new Problem(503, "The owner account has not been configured.");
    await db().batch([
      db()
        .prepare("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)")
        .bind("owner", user.userId),
      db()
        .prepare(
          "INSERT OR IGNORE INTO partners(id,name,created_at) VALUES(?,?,?)",
        )
        .bind("university-of-rochester", "University of Rochester", now()),
    ]);
    owner = await setting("owner");
  }
  const memberships = (
    await db()
      .prepare("SELECT partner_id,role FROM members WHERE user_id=?")
      .bind(user.userId)
      .all<{ partner_id: string; role: string }>()
  ).results;
  if (owner !== user.userId && !memberships.length)
    throw new Problem(
      403,
      "Your account has not been assigned to a partner. Contact the CATALYST owner.",
    );
  return { ...user, admin: owner === user.userId, memberships };
}
export type Actor = Awaited<ReturnType<typeof actor>>;
export function permitted(user: Actor, partner: string, write = false) {
  return (
    user.admin ||
    user.memberships.some(
      (m) =>
        m.partner_id === partner &&
        (!write || ["contributor", "reviewer"].includes(m.role)),
    )
  );
}
export function reviewer(user: Actor, partner: string) {
  return (
    user.admin ||
    user.memberships.some(
      (m) => m.partner_id === partner && m.role === "reviewer",
    )
  );
}
export function checkOrigin(request: Request) {
  if (["GET", "HEAD", "OPTIONS"].includes(request.method)) return;
  const origin = request.headers.get("origin");
  const expected = env.SITE_ORIGIN || new URL(request.url).origin;
  if (!origin || origin !== expected)
    throw new Problem(403, "This request must come from the CATALYST page.");
}
export async function jsonBody(request: Request, max = 65536) {
  if (!request.headers.get("content-type")?.startsWith("application/json"))
    throw new Problem(415, "Send JSON.");
  const text = await boundedText(request, max);
  try {
    return JSON.parse(text);
  } catch {
    throw new Problem(400, "Invalid JSON.");
  }
}
export async function boundedText(request: Request, max: number) {
  return new TextDecoder("utf-8", { fatal: true }).decode(
    await boundedBytes(request.body, max),
  );
}
export async function boundedBytes(
  body: ReadableStream<Uint8Array> | null,
  max: number,
) {
  if (!body) return new Uint8Array();
  const reader = body.getReader(),
    chunks: Uint8Array[] = [];
  let length = 0;
  for (;;) {
    const item = await reader.read();
    if (item.done) break;
    length += item.value.length;
    if (length > max) {
      await reader.cancel();
      throw new Problem(413, "The upload exceeds the supported size.");
    }
    chunks.push(item.value);
  }
  const all = new Uint8Array(length);
  let pos = 0;
  for (const part of chunks) {
    all.set(part, pos);
    pos += part.length;
  }
  return all;
}
export function respond(value: unknown, status = 200) {
  return Response.json(value, {
    status,
    headers: {
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    },
  });
}
export function fail(error: unknown) {
  if (error instanceof Problem)
    return respond({ error: error.message }, error.status);
  console.error(
    "CATALYST request failed",
    error instanceof Error ? error.name : "UnknownError",
  );
  return respond(
    {
      error:
        "The request could not be completed. Your saved revisions are retained. Please try again.",
    },
    500,
  );
}
export async function event(
  user: Actor,
  action: string,
  submission: string | null,
  detail: unknown = {},
) {
  await db()
    .prepare(
      "INSERT INTO audit(id,user_id,submission_id,action,detail,created_at) VALUES(?,?,?,?,?,?)",
    )
    .bind(uid(), user.userId, submission, action, JSON.stringify(detail), now())
    .run();
}
export async function requireSubmission(
  user: Actor,
  id: string,
  write = false,
) {
  const row = await db()
    .prepare("SELECT * FROM submissions WHERE id=?")
    .bind(id)
    .first<any>();
  if (!row || !permitted(user, row.partner_id, write))
    throw new Problem(404, "Submission not found.");
  return row;
}
