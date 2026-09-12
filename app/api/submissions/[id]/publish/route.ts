import {
  actor,
  requireSubmission,
  reviewer,
  db,
  checkOrigin,
  jsonBody,
  respond,
  fail,
  Problem,
} from "@/lib/server";
import { publish } from "@/lib/publication";
import { SciSureError } from "@/lib/scisure-client";
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
      throw new Problem(403, "Reviewer access is required for publication.");
    return respond(await publish(user, s, body));
  } catch (e) {
    return e instanceof SciSureError
      ? respond({ error: e.message }, e.status)
      : fail(e);
  }
}
export async function GET(
  request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const user = await actor(),
      { id } = await params;
    await requireSubmission(user, id);
    const operations = (
      await db()
        .prepare(
          "SELECT o.* FROM operations o JOIN publications p ON p.id=o.publication_id JOIN revisions r ON r.id=p.revision_id WHERE r.submission_id=? ORDER BY o.updated_at DESC",
        )
        .bind(id)
        .all()
    ).results;
    return respond({ operations });
  } catch (e) {
    return fail(e);
  }
}
