import "server-only";
import { db, bucket, digest, Problem } from "./server";
import type { Artifact, Preview, Context } from "./contracts";
export async function loadRevision(submissionId: string, revisionId: string) {
  const revision = await db()
    .prepare("SELECT * FROM revisions WHERE id=? AND submission_id=?")
    .bind(revisionId, submissionId)
    .first<any>();
  if (!revision) throw new Problem(404, "Revision not found.");
  const object = await bucket().get(revision.payload_key);
  if (!object) throw new Problem(503, "Saved revision storage is unavailable.");
  const payload = await object.text();
  if ((await digest(payload)) !== revision.digest)
    throw new Problem(409, "Revision integrity check failed.");
  const data = JSON.parse(payload) as { preview: Preview; parsed: Artifact[] };
  return { revision, ...data };
}
export function validateContext(input: unknown): Context {
  const allowed = [
    "specimenId",
    "runId",
    "acquiredBy",
    "processedBy",
    "acquiredAt",
    "reactorType",
    "temperatureC",
    "pressureKpaAbs",
    "catalystMassMg",
    "intervalMin",
    "technique",
    "axisUnit",
    "signalUnit",
    "synthesisMethod",
    "calibration",
    "flowBasis",
    "processingVersion",
    "identityNote",
    "reviewNote",
    "profileId",
  ];
  if (!input || typeof input !== "object" || Array.isArray(input))
    throw new Problem(400, "Scientific context must be an object.");
  const result: Record<string, string> = {};
  for (const [key, value] of Object.entries(input)) {
    if (
      !allowed.includes(key) ||
      typeof value !== "string" ||
      value.length > 4000
    )
      throw new Problem(400, "Unsupported context field.");
    result[key] = value.trim();
  }
  return result;
}
