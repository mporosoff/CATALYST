import {
  actor,
  requireSubmission,
  db,
  bucket,
  digest,
  Problem,
  respond,
  fail,
  reviewer,
  permitted,
  checkOrigin,
  jsonBody,
  uid,
  now,
  event,
} from "@/lib/server";
import { buildPreview, InputError } from "@/lib/ingest";
import { loadRevision, validateContext } from "@/lib/revisions";
export async function GET(
  request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const user = await actor(),
      { id } = await params,
      submission = await requireSubmission(user, id);
    const selected =
      new URL(request.url).searchParams.get("revision") ||
      submission.latest_revision;
    const { revision, preview } = await loadRevision(id, selected);
    const revisions = (
      await db()
        .prepare(
          "SELECT id,number,created_at,parent_id,digest FROM revisions WHERE submission_id=? ORDER BY number DESC",
        )
        .bind(id)
        .all()
    ).results;
    const approval = await db()
      .prepare("SELECT * FROM approvals WHERE revision_id=?")
      .bind(selected)
      .first();
    const publications = (
      await db()
        .prepare("SELECT * FROM publications WHERE revision_id=?")
        .bind(selected)
        .all()
    ).results;
    const audit = (
      await db()
        .prepare(
          "SELECT action,detail,created_at,user_id FROM audit WHERE submission_id=? ORDER BY created_at DESC LIMIT 100",
        )
        .bind(id)
        .all()
    ).results;
    return respond({
      submission,
      revision,
      preview,
      revisions,
      approval,
      publications,
      audit,
      admin: user.admin,
      canReview: reviewer(user, submission.partner_id),
      canEdit: permitted(user, submission.partner_id, true),
    });
  } catch (e) {
    return fail(e);
  }
}
export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    checkOrigin(request);
    const user = await actor(),
      { id } = await params,
      s = await requireSubmission(user, id, true),
      body = await jsonBody(request);
    if (body.parent !== s.latest_revision)
      throw new Problem(409, "A newer revision exists. Reload before saving.");
    const { revision: parent, parsed } = await loadRevision(
      id,
      s.latest_revision,
    );
    if (body.digest !== parent.digest)
      throw new Problem(
        409,
        "The reviewed revision changed. Reload before saving.",
      );
    const context = validateContext(body.context),
      profile = context.profileId
        ? await db()
            .prepare("SELECT * FROM profiles WHERE id=?")
            .bind(context.profileId)
            .first()
        : undefined;
    if (context.profileId && !profile)
      throw new Problem(400, "Mapping profile not found.");
    const preview = buildPreview(
        parsed,
        s.partner_id,
        s.modality,
        context,
        profile,
      ),
      revision = uid(),
      key = `revisions/${id}/${revision}.json`,
      payload = JSON.stringify({ preview, parsed }),
      hash = await digest(payload),
      time = now();
    await bucket().put(key, payload, {
      httpMetadata: { contentType: "application/json" },
    });
    // Insert and compare-and-swap are one D1 transaction. A losing request never creates a visible revision.
    const results = await db().batch([
      db()
        .prepare(
          "INSERT INTO revisions(id,submission_id,parent_id,number,payload_key,digest,context,created_by,created_at) SELECT ?,?,?,?,?,?,?,?,? FROM submissions s WHERE s.id=? AND s.latest_revision=? AND NOT EXISTS(SELECT 1 FROM publications p WHERE p.revision_id=s.latest_revision AND p.state IN ('publishing','unknown'))",
        )
        .bind(
          revision,
          id,
          parent.id,
          parent.number + 1,
          key,
          hash,
          JSON.stringify(context),
          user.userId,
          time,
          id,
          parent.id,
        ),
      db()
        .prepare(
          "UPDATE submissions SET latest_revision=? WHERE id=? AND latest_revision=? AND EXISTS(SELECT 1 FROM revisions WHERE id=?)",
        )
        .bind(revision, id, parent.id, revision),
    ]);
    if (!results[0].meta.changes)
      throw new Problem(
        409,
        "A newer revision or an unfinished publication prevents this change. Reload the review.",
      );
    await event(user, "revision_created", id, {
      revision,
      parent: parent.id,
      digest: hash,
    });
    return respond({ revision }, 201);
  } catch (e) {
    return e instanceof InputError
      ? respond({ error: e.message }, 400)
      : fail(e);
  }
}
