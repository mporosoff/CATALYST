import assert from "node:assert/strict";
import test from "node:test";
import "./ledger.test";
import {
  parseFile,
  csv,
  buildPreview,
  InputError,
  number,
  rawCellEquivalent,
  validateToolkit,
} from "../lib/ingest";
import { fieldRule } from "../lib/fields";
import { parseScientificJSON } from "../lib/strict-json";
import {
  SciSureClient,
  SciSureError,
  writableExperiment,
} from "../lib/scisure-client";
import { zipSync, strToU8 } from "fflate";
const encode = (s: string) => new TextEncoder().encode(s);
const ctx = {
  specimenId: "synthetic",
  runId: "test",
  acquiredBy: "Test laboratory",
  synthesisMethod: "Test protocol",
  acquiredAt: "2026-01-01",
};
test("CSV preserves quoted multiline content and rejects ragged rows", () => {
  assert.deepEqual(csv('a,b\r\n"line\nvalue","x""y"\r\n'), [
    ["a", "b"],
    ["line\nvalue", 'x"y'],
  ]);
  assert.throws(() => csv("a,b\n1,2,3"), InputError);
  assert.throws(() => csv('a\n"broken'), InputError);
});
test("Scientific JSON rejects duplicate keys and preserves exact numeric tokens", () => {
  assert.throws(() => parseScientificJSON('[{"x":1,"x":2}]'));
  assert.throws(() => parseScientificJSON('[{"x":01}]'));
  assert.throws(() => parseScientificJSON('[{"x":NaN}]'));
  assert.equal(
    (
      parseScientificJSON(
        '[{"x":9007199254740993,"y":0.10000000000000001}]',
      ) as any
    )[0].x,
    "9007199254740993",
  );
});
test("JSON format rejects nested and inconsistent records", async () => {
  await assert.rejects(
    parseFile(encode('[{"x":{"a":1}}]'), "bad.json", "x"),
    InputError,
  );
  await assert.rejects(
    parseFile(encode('[{"x":1},{"y":2}]'), "bad.json", "x"),
    InputError,
  );
});
test("Decimal normalization converts units and exact names without changing source", async () => {
  const a = await parseFile(
    encode("mass,name\n72,ZnMn\n"),
    "synthetic.csv",
    "a",
  );
  const content = {
    sheet: "Table",
    headers: ["mass", "name"],
    fields: [
      fieldRule("mass", "mass_g", "mg"),
      fieldRule("name", "specimen_id", "text", { ZnMn: "Zn-Mn" }),
    ],
    unmappedColumns: [],
  };
  const p = buildPreview([a], "lab", "synthesis", ctx, {
    id: "p",
    partner_id: "lab",
    modality: "synthesis",
    format: "csv",
    version: 1,
    digest: "test",
    content,
  });
  assert.equal(p.rows[0].mass_g, "0.072");
  assert.equal(p.rows[0].specimen_id, "Zn-Mn");
  assert.equal(a.sheets.Table.A2.value, "72");
  assert.equal(p.processing.executed, false);
  assert.equal(
    p.validation.issues.filter((x) => x.severity === "error").length,
    0,
  );
});
test("Unconfirmed gauge/absolute pressure and unsupported units cannot be mapped", () => {
  assert.throws(() => fieldRule("p", "pressure_Pa_abs", "bar gauge"));
  assert.throws(() => fieldRule("m", "mass_g", "mL"));
  assert.equal(fieldRule("t", "temperature_K", "degC").offset, "273.15");
});
test("Ambiguous matching tables fail; missing values block approval", async () => {
  const a = await parseFile(encode("mass\n\n"), "synthetic.csv", "a");
  const profile = {
    id: "p",
    partner_id: "lab",
    modality: "synthesis",
    format: "csv",
    version: 1,
    content: {
      sheet: "Table",
      headers: ["mass"],
      fields: [fieldRule("mass", "mass_g", "mg")],
    },
  };
  assert.throws(
    () =>
      buildPreview([a, { ...a, id: "b" }], "lab", "synthesis", ctx, profile),
    InputError,
  );
  assert.ok(
    buildPreview([a], "lab", "synthesis", ctx, profile).validation.issues.some(
      (x) => x.code === "MISSING_mass_g",
    ),
  );
});
function book(cells: string) {
  return zipSync({
    "xl/workbook.xml": strToU8(
      '<workbook xmlns:r="r"><sheets><sheet name="Data" r:id="rId1"/></sheets></workbook>',
    ),
    "xl/_rels/workbook.xml.rels": strToU8(
      '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
    ),
    "xl/worksheets/sheet1.xml": strToU8(
      "<worksheet><sheetData>" + cells + "</sheetData></worksheet>",
    ),
  });
}
test("XLSX retains formulas separately and blocks using cached calculation results", async () => {
  const a = await parseFile(
    book(
      '<row><c r="A1" t="inlineStr"><is><t>mass</t></is></c></row><row><c r="A2"><f>1+1</f><v>2</v></c></row>',
    ),
    "formula.xlsx",
    "a",
  );
  assert.equal(a.sheets.Data.A2.value, null);
  assert.equal(a.sheets.Data.A2.cached, "2");
  const p = buildPreview([a], "lab", "synthesis", ctx, {
    id: "p",
    partner_id: "lab",
    modality: "synthesis",
    format: "xlsx",
    version: 1,
    content: {
      sheet: "Data",
      headers: ["mass"],
      fields: [fieldRule("mass", "mass_g", "g")],
    },
  });
  assert.ok(p.validation.issues.some((x) => x.code === "FORMULA_INPUT"));
});
test("Archive expansion and path traversal limits reject unsafe workbooks", async () => {
  await assert.rejects(
    parseFile(zipSync({ "../outside.xml": strToU8("x") }), "bad.xlsx", "a"),
    InputError,
  );
  await assert.rejects(
    parseFile(
      zipSync({ "huge.xml": new Uint8Array(9 * 1024 * 1024) }),
      "big.xlsx",
      "a",
    ),
    InputError,
  );
});
test("Nonfinite numbers and booleans are rejected", () => {
  for (const v of ["Infinity", "NaN", "1e1000", true])
    assert.throws(() => number(v));
  assert.equal(number("22.4")!.mul(60).toString(), "1344");
});
test("SciSure uses the raw API token only in a server request header", async () => {
  let calls = 0;
  const c = new SciSureClient(
    "https://sandbox.elabjournal.com",
    "test-only-token",
    async (url: any, opts: any) => {
      calls++;
      assert.equal(opts.headers.Authorization, "test-only-token");
      assert.equal(opts.redirect, "manual");
      assert.ok(
        String(url).startsWith("https://sandbox.elabjournal.com/api/v1/"),
      );
      return Response.json({ data: [], hasNextPage: false });
    },
  );
  assert.deepEqual(await c.list("/api/v1/experiments"), []);
  assert.equal(calls, 1);
});
test("SciSure pagination reads all pages", async () => {
  let calls = 0;
  const c = new SciSureClient(
    "https://sandbox.elabjournal.com",
    "test",
    async () => Response.json({ data: [++calls], hasNextPage: calls === 1 }),
  );
  assert.deepEqual(await c.list("/api/v1/projects"), [1, 2]);
});
test("Uncertain writes are never automatically retried or echoed", async () => {
  let calls = 0;
  const c = new SciSureClient(
    "https://sandbox.elabjournal.com",
    "secret-sentinel",
    async () => {
      calls++;
      throw new Error("secret-sentinel");
    },
  );
  await assert.rejects(
    c.request("/api/v1/experiments", { method: "POST", json: {} }),
    (e: any) =>
      e instanceof SciSureError &&
      e.uncertain &&
      !e.message.includes("secret-sentinel"),
  );
  assert.equal(calls, 1);
});
test("Redirects do not forward credentials; unexpected tenants are rejected", async () => {
  assert.throws(
    () =>
      new SciSureClient("https://sandbox.elabjournal.com.evil.test", "test"),
  );
  assert.throws(
    () => new SciSureClient("https://sandbox.elabjournal.com/path", "test"),
  );
  const c = new SciSureClient(
    "https://sandbox.elabjournal.com",
    "test",
    async () =>
      new Response(null, {
        status: 302,
        headers: { Location: "https://example.com" },
      }),
  );
  await assert.rejects(c.request("/api/v1/groups/active"), SciSureError);
});
test("Signed, deleted, or wrong-group experiments fail closed", () => {
  const d = { experimentId: 1, groupId: 2, studyId: 3 },
    e = {
      experimentID: 1,
      groupID: 2,
      studyID: 3,
      deleted: false,
      template: false,
      signatureStatus: "None",
    };
  writableExperiment(e, d);
  for (const patch of [
    { signatureStatus: "Signed" },
    { signatureStatus: "Pending" },
    { deleted: true },
    { groupID: 9 },
    { signatureStatus: undefined },
  ])
    assert.throws(() => writableExperiment({ ...e, ...patch }, d));
});

test("Raw quantity comparison preserves identity text and checks exact decimals", () => {
  assert.ok(rawCellEquivalent("N7", { value: "36.390" }, { value: "36.39" }));
  assert.ok(!rawCellEquivalent("A7", { value: "001" }, { value: "1" }));
  assert.ok(!rawCellEquivalent("N7", { value: "36.390" }, { value: "36.391" }));
  assert.ok(
    !rawCellEquivalent(
      "N7",
      { value: "36.39" },
      { value: null, formula: "=1" },
    ),
  );
});

test("Toolkit settings, counts, and identity must agree", () => {
  const settings = {
    bypass_file: "same input file",
    catalyst_id: "synthetic",
    mass: 2,
    n_bypass: 0,
    n_reaction: 1,
    n_blank_excluded: 0,
    plot_reaction_points: 1,
    bypass_omit_initial: 0,
    bypass_points_used: 0,
    bypass_selected_points: 0,
    ss_inj_start: 1,
    ss_inj_end: 1,
  };
  const rows = [
    {
      catalyst_id: "synthetic",
      is_blank: "False",
      is_bypass: "False",
      analysis_include: "True",
      inj_num: "1",
      conversion: "0.1",
      time_on_stream_h: "0",
      H2: "10",
      CO2: "2",
      Ar: "1",
      CO: "0.2",
      CH4: "0",
    },
  ];
  const analysis: any = {
    sheets: { Settings: { A1: { value: "mass" }, B1: { value: "2" } } },
  };
  const good: any[] = [];
  validateToolkit(settings, rows, analysis, good);
  assert.equal(good.length, 0);
  const bad: any[] = [];
  validateToolkit(
    { ...settings, n_reaction: 2, mass: 3 },
    [{ ...rows[0], catalyst_id: "other" }],
    analysis,
    bad,
  );
  assert.ok(bad.some((x) => x.code === "ROW_COUNT_MISMATCH"));
  assert.ok(bad.some((x) => x.code === "SETTINGS_MISMATCH"));
  assert.ok(bad.some((x) => x.code === "CATALYST_ID_MISMATCH"));
});
