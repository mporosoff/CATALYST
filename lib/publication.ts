import "server-only";
import { env } from "cloudflare:workers";
import {
  db,
  bucket,
  uid,
  now,
  digest,
  Problem,
  event,
  type Actor,
} from "./server";
import { loadRevision } from "./revisions";
import {
  client,
  destination,
  verifyDestination,
  type Destination,
} from "./connector";
import { SciSureError, remoteId, type SciSureClient } from "./scisure-client";

import { runStep, type Step } from "./ledger";
export const durableStep = (
  publication: string,
  step: Step,
  reconcileOnly = false,
) => runStep(db(), publication, step, reconcileOnly);
function unique(items: any[], key: string) {
  if (items.length > 1)
    throw new Problem(
      409,
      "Multiple matching SciSure records were found. Publication is paused.",
    );
  return items.length ? remoteId(items[0][key]) : null;
}
async function section(
  c: SciSureClient,
  pub: string,
  d: Destination,
  kind: string,
  heading: string,
  reconcile: boolean,
) {
  return durableStep(
    pub,
    {
      name: "section/" + kind,
      before: () => verifyDestination(c, d),
      find: async () =>
        unique(
          (
            await c.list(`/api/v1/experiments/${d.experimentId}/sections`)
          ).filter(
            (x) =>
              x.sectionHeader === heading &&
              x.sectionType === "FILES" &&
              !x.deleted,
          ),
          "expJournalID",
        ),
      write: async () =>
        remoteId(
          await c.request(`/api/v1/experiments/${d.experimentId}/sections`, {
            method: "POST",
            json: { sectionType: "FILES", sectionHeader: heading },
          }),
        ),
    },
    reconcile,
  );
}
async function upload(
  c: SciSureClient,
  pub: string,
  d: Destination,
  sectionId: number,
  name: string,
  bytes: Uint8Array,
  key: string,
  reconcile: boolean,
) {
  const hash = await digest(bytes),
    path = `/api/v1/experiments/sections/${sectionId}/files`;
  return durableStep(
    pub,
    {
      name: key,
      before: () => verifyDestination(c, d),
      find: async () => {
        const candidates = (await c.list(path)).filter(
          (x) => x.realName === name && !x.parentExperimentFileID,
        );
        if (candidates.length > 1)
          throw new Problem(
            409,
            "Duplicate destination filenames need an owner review.",
          );
        if (!candidates.length) return null;
        const file = candidates[0],
          id = remoteId(file.experimentFileID);
        if (file.fileSize !== bytes.length)
          throw new Problem(
            409,
            "The remote file size differs from the approved artifact.",
          );
        const received = await c.request(path + "/" + id, { binary: true });
        if (
          !(received instanceof Uint8Array) ||
          (await digest(received)) !== hash
        )
          throw new Problem(
            409,
            "The remote file checksum differs from the approved artifact.",
          );
        return id;
      },
      write: async () =>
        remoteId(
          await c.request(path + "?fileName=" + encodeURIComponent(name), {
            method: "POST",
            bytes,
          }),
        ),
    },
    reconcile,
  );
}
export async function publish(user: Actor, s: any, body: any) {
  if (env.SCISURE_PUBLICATION_ENABLED !== "true")
    throw new Problem(
      409,
      "SciSure publishing is disabled in the deployment configuration.",
    );
  if (body.revision !== s.latest_revision)
    throw new Problem(
      409,
      "Only the latest approved revision can be published.",
    );
  const { revision, preview } = await loadRevision(s.id, body.revision),
    approval = await db()
      .prepare("SELECT * FROM approvals WHERE revision_id=? AND digest=?")
      .bind(revision.id, revision.digest)
      .first<any>();
  if (
    !approval ||
    body.digest !== revision.digest ||
    preview.validation.issues.some((i) => i.severity === "error")
  )
    throw new Problem(409, "This exact revision needs a valid approval.");
  const d = await destination(s.partner_id),
    c = client();
  await verifyDestination(c, d);
  const destinationKey = JSON.stringify({
    groupId: d.groupId,
    studyId: d.studyId,
    experimentId: d.experimentId,
  });
  await db()
    .prepare(
      "INSERT OR IGNORE INTO publications(id,revision_id,tenant,destination,state,created_at,updated_at) SELECT ?,?,?,?,'ready',?,? FROM submissions WHERE id=? AND latest_revision=?",
    )
    .bind(
      uid(),
      revision.id,
      c.origin,
      destinationKey,
      now(),
      now(),
      s.id,
      revision.id,
    )
    .run();
  const pub = await db()
    .prepare(
      "SELECT * FROM publications WHERE revision_id=? AND tenant=? AND destination=?",
    )
    .bind(revision.id, c.origin, destinationKey)
    .first<any>();
  if (!pub)
    throw new Problem(
      409,
      "The revision changed before publication could start.",
    );
  if (pub.state === "published") return { publication: pub, complete: true };
  if (
    pub.state === "publishing" &&
    Date.now() - Date.parse(pub.updated_at) < 60000
  )
    throw new Problem(
      409,
      "Publication is already running. Refresh its status shortly.",
    );
  const claim = await db()
    .prepare(
      "UPDATE publications SET state='publishing',updated_at=? WHERE id=? AND state=? AND updated_at=?",
    )
    .bind(now(), pub.id, pub.state, pub.updated_at)
    .run();
  if (!claim.meta.changes)
    throw new Problem(409, "Another request is publishing this revision.");
  const reconcile = body.reconcile === true,
    tag = "CATALYST " + revision.id;
  try {
    const rawSection = await section(
      c,
      pub.id,
      d,
      "source",
      tag + " — Source files",
      reconcile,
    );
    for (const artifact of preview.artifacts) {
      const stored = await db()
        .prepare("SELECT * FROM artifacts WHERE id=? AND submission_id=?")
        .bind(artifact.id, s.id)
        .first<any>();
      if (!stored) throw new Problem(409, "A source artifact is missing.");
      const object = await bucket().get(stored.object_key);
      if (!object) throw new Problem(503, "Source storage is unavailable.");
      const bytes = new Uint8Array(await object.arrayBuffer());
      if ((await digest(bytes)) !== artifact.sha256)
        throw new Problem(409, "Source integrity check failed.");
      await upload(
        c,
        pub.id,
        d,
        rawSection,
        artifact.name,
        bytes,
        "source/" + artifact.id,
        reconcile,
      );
    }
    const resultSection = await section(
      c,
      pub.id,
      d,
      "approved",
      tag + " — Approved data and provenance",
      reconcile,
    );
    const packageData = {
      format: "catalyst-approved-record/1.0.0",
      submission: {
        id: s.id,
        title: s.title,
        partner: s.partner_id,
        modality: s.modality,
      },
      revision: {
        id: revision.id,
        number: revision.number,
        parent: revision.parent_id,
        digest: revision.digest,
        createdAt: revision.created_at,
        createdBy: revision.created_by,
      },
      approval,
      normalization: preview.normalization,
      scientificProcessing: preview.processing,
      validation: preview.validation,
      context: preview.context,
      sourceArtifacts: preview.artifacts,
      standardized: {
        columns: preview.columns,
        rows: preview.rows,
        summary: preview.summary,
      },
      publication: {
        id: pub.id,
        tenant: c.origin,
        destination: JSON.parse(destinationKey),
      },
    };
    await upload(
      c,
      pub.id,
      d,
      resultSection,
      "catalyst-approved-" + revision.id + ".json",
      new TextEncoder().encode(JSON.stringify(packageData, null, 2)),
      "approved/json",
      reconcile,
    );
    await db()
      .prepare(
        "UPDATE publications SET state='published',updated_at=? WHERE id=?",
      )
      .bind(now(), pub.id)
      .run();
    await event(user, "published", s.id, {
      publication: pub.id,
      revision: revision.id,
      experimentId: d.experimentId,
    });
    return { publication: { ...pub, state: "published" }, complete: true };
  } catch (e) {
    await db()
      .prepare(
        "UPDATE publications SET state='unknown',updated_at=? WHERE id=?",
      )
      .bind(now(), pub.id)
      .run();
    await event(user, "publication_paused", s.id, { publication: pub.id });
    throw e;
  }
}
