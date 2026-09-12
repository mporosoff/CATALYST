import {
  actor,
  requireSubmission,
  reviewer,
  db,
  checkOrigin,
  jsonBody,
  digest,
  uid,
  now,
  respond,
  fail,
  Problem,
  event,
} from "@/lib/server";
import { fieldRule } from "@/lib/fields";
import { loadRevision } from "@/lib/revisions";
export async function GET(request: Request) {
  try {
    const user = await actor(),
      id = new URL(request.url).searchParams.get("submission") || "",
      s = await requireSubmission(user, id);
    const profiles = (
      await db()
        .prepare(
          "SELECT id,name,version,format,source_version,content,digest FROM profiles WHERE partner_id=? AND modality=? ORDER BY name,version DESC",
        )
        .bind(s.partner_id, s.modality)
        .all()
    ).results;
    return respond({ profiles });
  } catch (e) {
    return fail(e);
  }
}
export async function POST(request: Request) {
  try {
    checkOrigin(request);
    const user = await actor(),
      body = await jsonBody(request),
      s = await requireSubmission(user, body.submissionId, true);
    if (!reviewer(user, s.partner_id))
      throw new Problem(403, "A reviewer must create a mapping profile.");
    const { preview } = await loadRevision(s.id, s.latest_revision),
      source = preview.sourceTables.find(
        (t) => t.artifactId === body.artifactId && t.sheet === body.sheet,
      ),
      artifact = preview.artifacts.find((a) => a.id === body.artifactId);
    if (!source || !artifact)
      throw new Problem(
        400,
        "Choose a source table from the current revision.",
      );
    if (
      typeof body.name !== "string" ||
      !body.name.trim() ||
      body.name.length > 100 ||
      typeof body.sourceVersion !== "string" ||
      !body.sourceVersion.trim() ||
      body.sourceVersion.length > 100
    )
      throw new Problem(
        400,
        "Give the mapping a name and a source format/version label.",
      );
    if (
      !Array.isArray(body.fields) ||
      !body.fields.length ||
      body.fields.length > 30
    )
      throw new Problem(400, "Map between one and thirty fields.");
    const targets = new Set<string>(),
      sources = new Set<string>();
    const fields = body.fields.map((f: any) => {
      if (
        !f ||
        !source.columns.includes(f.source) ||
        targets.has(f.target) ||
        sources.has(f.source)
      )
        throw new Problem(
          400,
          "Map each source column and canonical field at most once.",
        );
      targets.add(f.target);
      sources.add(f.source);
      try {
        return fieldRule(f.source, f.target, f.unit, f.aliases);
      } catch (e) {
        throw new Problem(400, (e as Error).message);
      }
    });
    const content = JSON.stringify({
        sheet: body.sheet,
        headers: source.columns,
        sourceVersion: body.sourceVersion.trim(),
        fields,
        unmappedColumns: source.columns.filter((c) => !sources.has(c)),
      }),
      hash = await digest(content),
      id = uid();
    await db()
      .prepare(
        "INSERT INTO profiles(id,partner_id,modality,format,source_version,version,name,content,digest,created_by,created_at) SELECT ?,?,?,?,?,COALESCE(MAX(version),0)+1,?,?,?,?,? FROM profiles WHERE partner_id=? AND modality=? AND format=? AND source_version=? AND name=?",
      )
      .bind(
        id,
        s.partner_id,
        s.modality,
        artifact.format,
        body.sourceVersion.trim(),
        body.name.trim(),
        content,
        hash,
        user.userId,
        now(),
        s.partner_id,
        s.modality,
        artifact.format,
        body.sourceVersion.trim(),
        body.name.trim(),
      )
      .run();
    await event(user, "mapping_created", s.id, { id, digest: hash });
    return respond({ id }, 201);
  } catch (e) {
    return fail(e);
  }
}
