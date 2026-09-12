import { DatabaseSync } from "node:sqlite";
import assert from "node:assert/strict";
import test from "node:test";
import { runStep } from "../lib/ledger";
import { SciSureClient } from "../lib/scisure-client";
import { createTestStructure } from "../lib/scisure-test-setup";
import { readFileSync } from "node:fs";
function database() {
  const sql = new DatabaseSync(":memory:");
  sql.exec(
    "CREATE TABLE operations(id TEXT PRIMARY KEY,publication_id TEXT,name TEXT,state TEXT,remote_id TEXT,evidence TEXT,updated_at TEXT,UNIQUE(publication_id,name));",
  );
  const store = {
    prepare(query: string) {
      let values: any[] = [];
      return {
        bind(...args: any[]) {
          values = args;
          return this;
        },
        async first() {
          return sql.prepare(query).get(...values);
        },
        async run() {
          const result = sql.prepare(query).run(...values);
          return { meta: { changes: Number(result.changes) } };
        },
      };
    },
  } as unknown as D1Database;
  return { sql, store };
}
test("Durable publication performs a successful write only once", async () => {
  const { store } = database();
  let writes = 0,
    remote: number | null = null;
  const step = {
    name: "file",
    before: async () => {},
    find: async () => remote,
    write: async () => {
      writes++;
      return (remote = 123);
    },
  };
  assert.equal(await runStep(store, "pub", step), 123);
  assert.equal(await runStep(store, "pub", step), 123);
  assert.equal(writes, 1);
});
test("A lost response is reconciled by read-back without duplicate writes", async () => {
  const { store } = database();
  let remote: number | null = null,
    writes = 0;
  const step = {
    name: "file",
    before: async () => {},
    find: async () => remote,
    write: async () => {
      writes++;
      remote = 456;
      throw new Error("Connection lost after remote save");
    },
  };
  await assert.rejects(runStep(store, "pub", step));
  assert.equal(await runStep(store, "pub", step), 456);
  assert.equal(writes, 1);
});
test("Uncertain absence stays paused and never blindly retries", async () => {
  const { store } = database();
  let writes = 0;
  const step = {
    name: "file",
    before: async () => {},
    find: async () => null,
    write: async () => {
      writes++;
      throw new Error("Uncertain outcome");
    },
  };
  await assert.rejects(runStep(store, "pub", step));
  await assert.rejects(
    runStep(store, "pub", step),
    /uncertain write remains paused/,
  );
  assert.equal(writes, 1);
});
test("Concurrent publication claims cannot both write", async () => {
  const { store } = database();
  let writes = 0,
    remote: number | null = null;
  const step = {
    name: "file",
    before: async () => {},
    find: async () => remote,
    write: async () => {
      writes++;
      await new Promise((r) => setTimeout(r, 15));
      return (remote = 789);
    },
  };
  const results = await Promise.allSettled([
    runStep(store, "pub", step),
    runStep(store, "pub", step),
  ]);
  assert.equal(writes, 1);
  assert.ok(results.some((x) => x.status === "fulfilled"));
  assert.equal(await runStep(store, "pub", step), 789);
});
test("Changed permissions stop the request before a write is claimed", async () => {
  const { store } = database();
  let writes = 0;
  await assert.rejects(
    runStep(store, "pub", {
      name: "file",
      before: async () => {
        throw new Error("Destination changed");
      },
      find: async () => null,
      write: async () => ++writes,
    }),
  );
  assert.equal(writes, 0);
});

test("Test destination satisfies SciSure project validation and repeated setup does not duplicate records", async () => {
  const { store } = database();
  const catalog: Record<string, any[]> = {
    projects: [],
    studies: [],
    experiments: [],
  };
  let writes = 0;
  const c = new SciSureClient(
    "https://sandbox.elabjournal.com",
    "dummy-test-token",
    async (input, init) => {
      const url = new URL(String(input));
      if (url.pathname === "/api/v1/groups/active")
        return Response.json({ groupID: 7265 });
      const kind = url.pathname.split("/").at(-1)!;
      if (init?.method !== "POST")
        return Response.json({ data: catalog[kind], hasNextPage: false });
      const body = JSON.parse(String(init.body));
      // Match the service's documented validation, including required notes.
      if (kind === "projects" && (!body.name?.trim() || !body.notes?.trim()))
        return Response.json({}, { status: 400 });
      if (kind === "studies" && body.projectID !== 101)
        return Response.json({}, { status: 400 });
      if (kind === "experiments" && body.studyID !== 102)
        return Response.json({}, { status: 400 });
      const id = 101 + writes++;
      const key =
        kind === "studies"
          ? "studyID"
          : kind === "projects"
            ? "projectID"
            : "experimentID";
      catalog[kind].push({
        ...body,
        [key]: id,
        groupID: 7265,
        active: true,
        deleted: false,
        template: false,
      });
      // Exercise all documented response shapes and project read-back fallback.
      return kind === "projects"
        ? new Response(null)
        : Response.json(kind === "studies" ? { studyID: id } : id);
    },
  );
  assert.deepEqual(await createTestStructure(c, store, 7265), {
    projectId: 101,
    studyId: 102,
    experimentId: 103,
  });
  await createTestStructure(c, store, 7265);
  assert.equal(writes, 3);
  assert.deepEqual(
    Object.values(catalog).map((x) => x.length),
    [1, 1, 1],
  );
});

const repair = readFileSync(
  "drizzle/0002_repair_rejected_test_project.sql",
  "utf8",
);
const rejectedId = "d1233930-1850-40f3-90db-ea63b60b6469";
function rejectedFixture(overrides: Record<string, unknown> = {}) {
  const d = database();
  d.sql.exec(
    "CREATE TABLE audit(id TEXT PRIMARY KEY,user_id TEXT,action TEXT,detail TEXT,created_at TEXT)",
  );
  const row = {
    id: rejectedId,
    publication_id: "sandbox-setup/7265",
    name: "project",
    state: "unknown",
    remote_id: null,
    evidence:
      "SciSure returned HTTP 400. The operation is paused for reconciliation.",
    updated_at: "2026-09-12T17:22:20.489Z",
    ...overrides,
  };
  d.sql
    .prepare("INSERT INTO operations VALUES(?,?,?,?,?,?,?)")
    .run(...(Object.values(row) as any[]));
  return d;
}
test("Reviewed project rejection repair preserves evidence and runs only once", () => {
  const { sql } = rejectedFixture();
  sql.exec(repair);
  assert.equal(
    sql.prepare("SELECT state FROM operations").get()!.state,
    "pending",
  );
  const original = JSON.parse(
    String(sql.prepare("SELECT detail FROM audit").get()!.detail),
  );
  assert.equal(original.priorState, "unknown");
  assert.match(original.priorEvidence, /HTTP 400/);
  sql.exec(repair);
  assert.equal(sql.prepare("SELECT COUNT(*) AS n FROM audit").get()!.n, 1);
});
test("The repair cannot reset unrelated, changed, successful or uncertain network operations", () => {
  for (const change of [
    { id: "other" },
    { publication_id: "sandbox-setup/9999" },
    { name: "file" },
    { state: "done" },
    { state: "running" },
    { remote_id: "123" },
    { updated_at: "2026-09-12T18:00:00.000Z" },
    {
      evidence:
        "SciSure returned HTTP 500. The operation is paused for reconciliation.",
    },
  ]) {
    const { sql } = rejectedFixture(change);
    const before = sql.prepare("SELECT * FROM operations").get();
    sql.exec(repair);
    assert.deepEqual(sql.prepare("SELECT * FROM operations").get(), before);
    assert.equal(sql.prepare("SELECT COUNT(*) AS n FROM audit").get()!.n, 0);
  }
});
