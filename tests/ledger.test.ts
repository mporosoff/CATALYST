import { DatabaseSync } from "node:sqlite";
import assert from "node:assert/strict";
import test from "node:test";
import { runStep } from "../lib/ledger";
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
