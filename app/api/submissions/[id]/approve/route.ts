import {
  actor,
  requireSubmission,
  reviewer,
  db,
  checkOrigin,
  jsonBody,
  now,
  respond,
  fail,
  Problem,
  event,
} from "@/lib/server";
import { loadRevision } from "@/lib/revisions";
export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    checkOrigin(request);
    const user = await actor(),
      { id } = await params,
      s = await requireSubmission(user, id),
      body = await jsonBody(request);
    if (!reviewer(user, s.partner_id))
      throw new Problem(403, "Reviewer access is required.");
    if (body.revision !== s.latest_revision)
      throw new Problem(409, "Only the latest revision can be approved.");
    const { revision, preview } = await loadRevision(id, body.revision);
    if (body.digest !== revision.digest)
      throw new Problem(
        409,
        "The revision checksum does not match. Reload the review.",
      );
    if (preview.validation.issues.some((i) => i.severity === "error"))
      throw new Problem(422, "Resolve validation errors before approval.");
    if (
      body.acknowledge !== true ||
      typeof body.note !== "string" ||
      body.note.trim().length < 10 ||
      body.note.length > 4000
    )
      throw new Problem(
        400,
        "Acknowledge the warnings and record your review decision.",
      );
    const result = await db()
      .prepare(
        "INSERT OR IGNORE INTO approvals(revision_id,digest,reviewer,note,created_at) SELECT ?,?,?,?,? FROM submissions WHERE id=? AND latest_revision=?",
      )
      .bind(
        revision.id,
        revision.digest,
        user.userId,
        body.note.trim(),
        now(),
        id,
        revision.id,
      )
      .run();
    const approval = await db()
      .prepare("SELECT * FROM approvals WHERE revision_id=?")
      .bind(revision.id)
      .first();
    if (!approval)
      throw new Problem(
        409,
        "A newer revision was saved while you were reviewing.",
      );
    if (result.meta.changes)
      await event(user, "revision_approved", id, {
        revision: revision.id,
        digest: revision.digest,
      });
    return respond({ approval });
  } catch (e) {
    return fail(e);
  }
}
