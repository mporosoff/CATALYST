import {
  actor,
  permitted,
  Problem,
  checkOrigin,
  boundedBytes,
  db,
  bucket,
  uid,
  now,
  digest,
  respond,
  fail,
  event,
} from "@/lib/server";
import { parseFile, buildPreview, InputError, LIMITS } from "@/lib/ingest";
import type { Modality } from "@/lib/contracts";
export async function POST(request: Request) {
  try {
    checkOrigin(request);
    const user = await actor();
    const contentType = request.headers.get("content-type") || "";
    if (!contentType.startsWith("multipart/form-data;"))
      throw new Problem(415, "Choose files to upload.");
    const body = await boundedBytes(request.body, LIMITS.total + 65536),
      form = await new Response(body, {
        headers: { "Content-Type": contentType },
      }).formData();
    const partner = String(form.get("partner") || ""),
      modality = String(form.get("modality") || "") as Modality,
      title = String(form.get("title") || "").trim();
    if (!permitted(user, partner, true))
      throw new Problem(403, "You cannot upload for this partner.");
    if (
      !(await db()
        .prepare("SELECT id FROM partners WHERE id=?")
        .bind(partner)
        .first())
    )
      throw new Problem(400, "Choose an existing partner.");
    if (
      !["reactor", "synthesis", "spectroscopy"].includes(modality) ||
      !title ||
      title.length > 160
    )
      throw new Problem(400, "Choose a data type and name this dataset.");
    const files = form.getAll("files");
    if (
      !files.length ||
      files.length > 6 ||
      files.some((f) => typeof f === "string")
    )
      throw new Problem(400, "Choose between one and six files.");
    let total = 0,
      cellCount = 0;
    const parsed = [],
      inputs = [];
    const names = new Set<string>();
    for (const file of files as File[]) {
      total += file.size;
      if (total > LIMITS.total)
        throw new Problem(413, "The combined upload exceeds 8 MB.");
      if (names.has(file.name))
        throw new Problem(
          400,
          "Files in one submission must have unique names.",
        );
      names.add(file.name);
      const bytes = new Uint8Array(await file.arrayBuffer());
      const artifact = await parseFile(bytes, file.name, uid());
      cellCount += Object.values(artifact.sheets).reduce(
        (n, s) => n + Object.keys(s).length,
        0,
      );
      if (cellCount > LIMITS.cells)
        throw new Problem(
          413,
          "The combined upload exceeds 50,000 cells. Split it into smaller datasets.",
        );
      parsed.push(artifact);
      inputs.push(bytes);
    }
    const id = uid(),
      revision = uid(),
      time = now(),
      preview = buildPreview(parsed, partner, modality, {}),
      payload = JSON.stringify({ preview, parsed }),
      hash = await digest(payload),
      key = `revisions/${id}/${revision}.json`;
    const statements = [
      db()
        .prepare(
          "INSERT INTO submissions(id,partner_id,modality,title,created_by,created_at,latest_revision) VALUES(?,?,?,?,?,?,?)",
        )
        .bind(id, partner, modality, title, user.userId, time, revision),
    ];
    for (let i = 0; i < parsed.length; i++) {
      const a = parsed[i],
        objectKey = `originals/${id}/${a.id}`;
      await bucket().put(objectKey, inputs[i], {
        sha256: a.sha256,
        httpMetadata: { contentType: "application/octet-stream" },
      });
      statements.push(
        db()
          .prepare(
            "INSERT INTO artifacts(id,submission_id,name,sha256,size,object_key,format) VALUES(?,?,?,?,?,?,?)",
          )
          .bind(a.id, id, a.name, a.sha256, a.size, objectKey, a.format),
      );
    }
    await bucket().put(key, payload, {
      httpMetadata: { contentType: "application/json" },
    });
    statements.push(
      db()
        .prepare(
          "INSERT INTO revisions(id,submission_id,parent_id,number,payload_key,digest,context,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
        )
        .bind(revision, id, null, 1, key, hash, "{}", user.userId, time),
    );
    await db().batch(statements);
    await event(user, "upload", id, { revision, artifactCount: parsed.length });
    return respond({ id, revision }, 201);
  } catch (e) {
    return e instanceof InputError
      ? respond({ error: e.message }, 400)
      : fail(e);
  }
}
