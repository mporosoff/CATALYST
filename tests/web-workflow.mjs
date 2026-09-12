import assert from "node:assert/strict";
const base = "http://localhost:5173";
const auth = await fetch(base + "/signin-with-chatgpt", { redirect: "manual" });
assert.equal(auth.status, 302);
const cookie = auth.headers.get("set-cookie").split(";")[0];
let checks = 0;
async function api(path, body, expected = 200, headers = {}) {
  const res = await fetch(base + "/api/" + path, {
    method: body ? "POST" : "GET",
    headers: {
      Cookie: cookie,
      Origin: base,
      ...(body instanceof FormData
        ? {}
        : { "Content-Type": "application/json" }),
      ...headers,
    },
    body:
      body instanceof FormData ? body : body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let json;
  try {
    json = JSON.parse(text);
  } catch {
    json = { error: text };
  }
  assert.equal(res.status, expected, JSON.stringify(json));
  checks++;
  return json;
}
const unauth = await fetch(base + "/api/workspace", {
  headers: {
    "oai-authenticated-user-id": "fake",
    "oai-authenticated-user-email": "fake@test",
  },
});
assert.equal(unauth.status, 401);
checks++;
await api("workspace");
await api("submissions", {}, 403, { Origin: "https://example.com" });
const form = new FormData();
form.set("partner", "university-of-rochester");
form.set("modality", "synthesis");
form.set("title", "SYNTHETIC workflow test " + Date.now());
form.append("files", new Blob(["mass,name\n72,SYNTHETIC\n"]), "synthetic.csv");
const uploaded = await api("submissions", form, 201),
  path = "submissions/" + uploaded.id;
const first = await api(path);
assert.equal(first.preview.artifacts.length, 1);
assert.ok(
  first.preview.validation.issues.some((i) => i.code === "MAPPING_REQUIRED"),
);
await api(
  path + "/approve",
  {
    revision: first.revision.id,
    digest: first.revision.digest,
    acknowledge: true,
    note: "This should be blocked.",
  },
  422,
);
const source = first.preview.sourceTables[0];
const profile = await api(
  "profiles",
  {
    submissionId: uploaded.id,
    artifactId: source.artifactId,
    sheet: source.sheet,
    name: "Synthetic mass mapping",
    sourceVersion: "synthetic export v1",
    fields: [
      { source: "mass", target: "mass_g", unit: "mg" },
      { source: "name", target: "specimen_id", unit: "text" },
    ],
  },
  201,
);
await api(
  path,
  { parent: first.revision.id, digest: "wrong", context: {} },
  409,
);
const context = {
  specimenId: "SYNTHETIC",
  runId: "test",
  acquiredBy: "Test laboratory",
  synthesisMethod: "Synthetic test protocol",
  acquiredAt: "2026-01-01",
  profileId: profile.id,
};
await api(
  path,
  { parent: first.revision.id, digest: first.revision.digest, context },
  201,
);
const second = await api(path);
assert.equal(second.revision.number, 2);
assert.equal(second.preview.rows[0].mass_g, "0.072");
assert.equal(
  second.preview.validation.issues.filter((i) => i.severity === "error").length,
  0,
);
await api(
  path,
  { parent: first.revision.id, digest: first.revision.digest, context },
  409,
);
await api(
  path + "/approve",
  {
    revision: second.revision.id,
    digest: second.revision.digest,
    acknowledge: false,
    note: "Synthetic data review.",
  },
  400,
);
const approval = {
  revision: second.revision.id,
  digest: second.revision.digest,
  acknowledge: true,
  note: "Synthetic fixture reviewed for the automated workflow test.",
};
await api(path + "/approve", approval);
await api(path + "/approve", approval);
const approved = await api(path);
assert.equal(approved.approval.digest, second.revision.digest);
await api(
  path + "/publish",
  { revision: second.revision.id, digest: second.revision.digest },
  409,
);
await api(
  path,
  {
    parent: second.revision.id,
    digest: second.revision.digest,
    context: { ...context, synthesisMethod: "Revised synthetic protocol" },
  },
  201,
);
const third = await api(path);
assert.equal(third.approval, null);
assert.equal(third.revisions.length, 3);
await api(path + "/approve", approval, 409);
const download = await fetch(
  base + "/api/" + path + "/download?artifact=" + first.preview.artifacts[0].id,
  { headers: { Cookie: cookie } },
);
assert.equal(await download.text(), "mass,name\n72,SYNTHETIC\n");
checks++;
const original = await api(path + "?revision=" + first.revision.id);
assert.equal(original.preview.kind, "unmapped");
console.log(
  `Passed ${checks} local HTTP workflow checks. Only synthetic test data was created.`,
);
