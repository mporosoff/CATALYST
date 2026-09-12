import {
  actor,
  requireSubmission,
  db,
  bucket,
  digest,
  Problem,
  fail,
} from "@/lib/server";
import { loadRevision } from "@/lib/revisions";
export async function GET(
  request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const user = await actor(),
      { id } = await params,
      s = await requireSubmission(user, id),
      query = new URL(request.url).searchParams,
      artifact = query.get("artifact");
    let bytes: Uint8Array,
      name: string,
      type = "application/octet-stream";
    if (artifact) {
      const a = await db()
        .prepare("SELECT * FROM artifacts WHERE id=? AND submission_id=?")
        .bind(artifact, id)
        .first<any>();
      if (!a) throw new Problem(404, "File not found.");
      const object = await bucket().get(a.object_key);
      if (!object) throw new Problem(503, "File storage is unavailable.");
      bytes = new Uint8Array(await object.arrayBuffer());
      if ((await digest(bytes)) !== a.sha256)
        throw new Problem(409, "File integrity check failed.");
      name = a.name;
    } else {
      const { revision, preview } = await loadRevision(
        id,
        query.get("revision") || s.latest_revision,
      );
      bytes = new TextEncoder().encode(JSON.stringify(preview, null, 2));
      name = `catalyst-revision-${revision.number}.json`;
      type = "application/json";
    }
    return new Response(bytes as BodyInit, {
      headers: {
        "Content-Type": type,
        "Content-Disposition": `attachment; filename*=UTF-8''${encodeURIComponent(name)}`,
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
      },
    });
  } catch (e) {
    return fail(e);
  }
}
