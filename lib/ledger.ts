import { Problem } from "./problem";
import { remoteId, SciSureError } from "./scisure-client";
const uid = () => crypto.randomUUID(),
  now = () => new Date().toISOString();
export type Step = {
  name: string;
  find: () => Promise<number | null>;
  write: () => Promise<number>;
  before: () => Promise<unknown>;
};
async function finish(
  store: D1Database,
  operation: string,
  id: number,
  evidence: string,
) {
  await store
    .prepare(
      "UPDATE operations SET state='done',remote_id=?,evidence=?,updated_at=? WHERE id=?",
    )
    .bind(String(id), evidence, now(), operation)
    .run();
  return id;
}
export async function runStep(
  store: D1Database,
  publication: string,
  step: Step,
  reconcileOnly = false,
) {
  await store
    .prepare(
      "INSERT OR IGNORE INTO operations(id,publication_id,name,state,updated_at) VALUES(?,?,?,'pending',?)",
    )
    .bind(uid(), publication, step.name, now())
    .run();
  const op = await store
    .prepare("SELECT * FROM operations WHERE publication_id=? AND name=?")
    .bind(publication, step.name)
    .first<any>();
  if (op.state === "done") return remoteId(op.remote_id);
  // Never reissue a write after a lost response. Only exact remote read-back can complete it.
  if (op.state === "running" && Date.now() - Date.parse(op.updated_at) < 60000)
    throw new Problem(
      409,
      "This operation is still running. Refresh its status shortly.",
    );
  const found = await step.find();
  if (found)
    return finish(store, op.id, found, "Verified by exact remote read-back.");
  if (op.state !== "pending" || reconcileOnly)
    throw new Problem(
      409,
      "No matching remote record was found. This uncertain write remains paused; an owner must investigate before any retry.",
    );
  await step.before();
  const claim = await store
    .prepare(
      "UPDATE operations SET state='running',updated_at=? WHERE id=? AND state='pending'",
    )
    .bind(now(), op.id)
    .run();
  if (!claim.meta.changes)
    throw new Problem(
      409,
      "Another request claimed this operation. Refresh status.",
    );
  try {
    const id = await step.write();
    const verified = await step.find();
    if (verified !== id)
      throw new SciSureError(
        502,
        "SciSure accepted a write but read-back did not confirm its identity.",
        true,
      );
    return await finish(
      store,
      op.id,
      id,
      "Write response and remote read-back agree.",
    );
  } catch (e) {
    await store
      .prepare(
        "UPDATE operations SET state='unknown',evidence=?,updated_at=? WHERE id=? AND state='running'",
      )
      .bind(
        e instanceof SciSureError
          ? e.message
          : "The operation outcome requires reconciliation.",
        now(),
        op.id,
      )
      .run();
    throw e;
  }
}
