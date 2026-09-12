import "server-only";
import { env } from "cloudflare:workers";
import { db, setting, now, Problem } from "./server";
import { SciSureClient, remoteId, writableExperiment } from "./scisure-client";
export type Destination = {
  tenant: string;
  groupId: number;
  studyId: number;
  experimentId: number;
  projectId: number;
  groupName: string;
  studyName: string;
  experimentName: string;
  verifiedAt: string;
};
export function client() {
  return new SciSureClient(
    env.SCISURE_BASE_URL || "https://sandbox.elabjournal.com",
    env.SCISURE_API_TOKEN || "",
  );
}
export async function destination(partner: string) {
  const raw = await setting("destination/" + partner);
  if (!raw)
    throw new Problem(
      409,
      "Choose and verify a SciSure test destination first.",
    );
  return JSON.parse(raw) as Destination;
}
export async function verifyDestination(c: SciSureClient, d: Destination) {
  if (c.origin !== d.tenant)
    throw new Problem(
      409,
      "The tenant has changed. Verify the destination again.",
    );
  const group = await c.request("/api/v1/groups/active");
  if (remoteId(group.groupID) !== d.groupId)
    throw new Problem(
      409,
      "The API account’s active group has changed. Restore the verified group in SciSure.",
    );
  const experiment = await c.request("/api/v1/experiments/" + d.experimentId);
  writableExperiment(experiment, d);
  return experiment;
}
export async function saveDestination(partner: string, experimentId: unknown) {
  const c = client(),
    group = await c.request("/api/v1/groups/active"),
    e = await c.request("/api/v1/experiments/" + remoteId(experimentId));
  const d: Destination = {
    tenant: c.origin,
    groupId: remoteId(group.groupID),
    studyId: remoteId(e.studyID),
    experimentId: remoteId(e.experimentID),
    projectId: remoteId(e.projectID),
    groupName: String(group.name || group.groupName || group.groupID),
    studyName: String(e.studyName || e.studyID),
    experimentName: String(e.name),
    verifiedAt: now(),
  };
  writableExperiment(e, d);
  await db()
    .prepare(
      "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
    )
    .bind("destination/" + partner, JSON.stringify(d))
    .run();
  return d;
}
