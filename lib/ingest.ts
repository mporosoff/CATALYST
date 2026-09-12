import { unzipSync } from "fflate";
import { XMLParser } from "fast-xml-parser";
import Decimal from "decimal.js";
import { parseScientificJSON } from "./strict-json";
Decimal.set({ precision: 80 });
import type {
  Artifact,
  Cell,
  Context,
  Modality,
  Preview,
  Issue,
} from "./contracts";

export class InputError extends Error {}
export const LIMITS = {
  file: 4 * 1024 * 1024,
  total: 8 * 1024 * 1024,
  expanded: 8 * 1024 * 1024,
  entries: 512,
  cells: 50000,
  rows: 10000,
  columns: 256,
  text: 8192,
};
export function coordinate(address: string): [number, number] {
  const m = /^([A-Z]+)([1-9]\d*)$/.exec(address);
  if (!m) throw new InputError("Invalid worksheet cell coordinate.");
  let col = 0;
  for (const c of m[1]) col = col * 26 + c.charCodeAt(0) - 64;
  const row = Number(m[2]);
  if (row > LIMITS.rows || col > LIMITS.columns)
    throw new InputError("Worksheet dimensions exceed the supported limits.");
  return [row, col];
}
export function column(n: number) {
  let s = "";
  for (; n; n = Math.floor((n - 1) / 26))
    s = String.fromCharCode(65 + ((n - 1) % 26)) + s;
  return s;
}
export function number(value: unknown, optional = false): Decimal | null {
  if ((value === "" || value == null) && optional) return null;
  if (
    typeof value === "boolean" ||
    !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(String(value))
  )
    throw new InputError(
      "A numeric field contains an ambiguous or invalid number.",
    );
  if (
    String(value)
      .split(/[eE]/)[0]
      .replace(/[^0-9]/g, "").length > 60
  )
    throw new InputError("Numeric precision exceeds 60 source digits.");
  const n = new Decimal(String(value));
  if (!n.isFinite() || Math.abs(n.e) > 300)
    throw new InputError("Numeric value exceeds the supported range.");
  return n;
}
function text(value: unknown) {
  const s = String(value ?? "");
  if (s.length > LIMITS.text) throw new InputError("A text field is too long.");
  return s;
}
export function csv(source: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [],
    cell = "",
    quoted = false,
    closed = false;
  for (let i = 0; i < source.length; i++) {
    const c = source[i];
    if (quoted) {
      if (c === '"') {
        if (source[i + 1] === '"') {
          cell += '"';
          i++;
        } else {
          quoted = false;
          closed = true;
        }
      } else cell += c;
    } else if (c === '"') {
      if (cell || closed) throw new InputError("Malformed CSV quoting.");
      quoted = true;
    } else if (c === "," || c === "\r" || c === "\n") {
      row.push(text(cell));
      cell = "";
      closed = false;
      if (c !== ",") {
        if (c === "\r" && source[i + 1] === "\n") i++;
        rows.push(row);
        row = [];
      }
    } else {
      if (closed)
        throw new InputError("Unexpected text after a quoted CSV field.");
      cell += c;
    }
    if (
      cell.length > LIMITS.text ||
      rows.length > LIMITS.rows ||
      row.length > LIMITS.columns
    )
      throw new InputError("CSV limits exceeded.");
  }
  if (quoted) throw new InputError("Unclosed CSV field.");
  if (cell || closed || row.length) {
    row.push(text(cell));
    rows.push(row);
  }
  if (rows.length && rows.some((r) => r.length !== rows[0].length))
    throw new InputError(
      "CSV rows must have the same number of fields as the header.",
    );
  return rows;
}
function toCells(rows: unknown[][]) {
  const cells: Record<string, Cell> = {};
  let count = 0;
  rows.forEach((row, r) => {
    if (r >= LIMITS.rows || row.length > LIMITS.columns)
      throw new InputError("Table dimensions exceed the supported limits.");
    row.forEach((value, c) => {
      if (++count > LIMITS.cells) throw new InputError("Too many cells.");
      cells[column(c + 1) + (r + 1)] = {
        value:
          value === null
            ? null
            : typeof value === "number" || typeof value === "boolean"
              ? value
              : text(value),
      };
    });
  });
  return cells;
}
const array = (x: any): any[] =>
  x === undefined ? [] : Array.isArray(x) ? x : [x];
function xmlText(x: any): string {
  if (x == null) return "";
  if (typeof x !== "object") return String(x);
  if (x.t !== undefined) return xmlText(x.t);
  if (x.r !== undefined) return array(x.r).map(xmlText).join("");
  return String(x["#text"] ?? "");
}
function xml(bytes: Uint8Array) {
  const source = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  if (/<!DOCTYPE|<!ENTITY/i.test(source))
    throw new InputError("XML declarations and entities are not supported.");
  return new XMLParser({
    ignoreAttributes: false,
    parseTagValue: false,
    parseAttributeValue: false,
    removeNSPrefix: true,
    processEntities: true,
    allowBooleanAttributes: false,
  }).parse(source);
}
function xlsx(bytes: Uint8Array) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let end = -1;
  for (let i = bytes.length - 22; i >= Math.max(0, bytes.length - 65557); i--)
    if (view.getUint32(i, true) === 0x06054b50) {
      end = i;
      break;
    }
  if (end < 0) throw new InputError("Invalid workbook archive.");
  const count = view.getUint16(end + 10, true);
  if (count > LIMITS.entries || count === 65535)
    throw new InputError("Workbook archive contains too many entries.");
  let offset = view.getUint32(end + 16, true),
    size = 0;
  const seen = new Set<string>();
  for (let i = 0; i < count; i++) {
    if (
      offset + 46 > bytes.length ||
      view.getUint32(offset, true) !== 0x02014b50
    )
      throw new InputError("Invalid workbook directory.");
    const flags = view.getUint16(offset + 8, true),
      length = view.getUint16(offset + 28, true),
      extra = view.getUint16(offset + 30, true),
      comment = view.getUint16(offset + 32, true),
      name = new TextDecoder().decode(
        bytes.slice(offset + 46, offset + 46 + length),
      );
    if (
      flags & 1 ||
      name.includes("\\") ||
      name.startsWith("/") ||
      name.split("/").includes("..") ||
      seen.has(name)
    )
      throw new InputError("Unsafe or duplicate workbook entry.");
    seen.add(name);
    size += view.getUint32(offset + 24, true);
    if (size > LIMITS.expanded)
      throw new InputError("Expanded workbook is too large.");
    offset += 46 + length + extra + comment;
  }
  if (
    [...seen].some((x) =>
      /vbaProject|externalLinks|activeX|embeddings/i.test(x),
    )
  )
    throw new InputError(
      "Macros, embedded objects, and external workbook links are not supported.",
    );
  const files = unzipSync(bytes, {
    filter: (f) => f.name.endsWith(".xml") || f.name.endsWith(".rels"),
  });
  const book = xml(files["xl/workbook.xml"]).workbook,
    rels = xml(files["xl/_rels/workbook.xml.rels"]).Relationships;
  const targets = new Map<string, string>();
  for (const rel of array(rels.Relationship)) {
    if (rel["@_TargetMode"] === "External")
      throw new InputError(
        "External workbook relationships are not supported.",
      );
    targets.set(rel["@_Id"], rel["@_Target"]);
  }
  const strings = files["xl/sharedStrings.xml"]
    ? array(xml(files["xl/sharedStrings.xml"]).sst.si).map(xmlText)
    : [];
  const sheets: Artifact["sheets"] = {};
  let total = 0;
  const declarations = array(book.sheets.sheet);
  if (declarations.length > 12)
    throw new InputError("Too many workbook sheets.");
  for (const sheet of declarations) {
    const name = text(sheet["@_name"]),
      target = targets.get(sheet["@_id"]);
    if (!target) throw new InputError("Missing worksheet relationship.");
    const path = target.startsWith("/") ? target.slice(1) : "xl/" + target;
    if (
      !/^xl\/worksheets\/[^/]+\.xml$/.test(path) ||
      !files[path] ||
      sheets[name]
    )
      throw new InputError("Unsupported worksheet location.");
    const cells: Record<string, Cell> = {};
    for (const row of array(xml(files[path]).worksheet.sheetData?.row)) {
      for (const c of array(row.c)) {
        if (++total > LIMITS.cells)
          throw new InputError("Too many workbook cells.");
        const address = c["@_r"];
        coordinate(address);
        if (cells[address]) throw new InputError("Duplicate worksheet cell.");
        let value: any = c.v ?? null;
        if (c["@_t"] === "s") {
          const n = Number(value);
          if (!Number.isInteger(n) || n < 0 || n >= strings.length)
            throw new InputError("Invalid shared string.");
          value = strings[n];
        } else if (c["@_t"] === "inlineStr") value = xmlText(c.is);
        else if (c["@_t"] === "b") value = value === "1";
        const entry: Cell = { value: value === null ? null : text(value) };
        if (c.f !== undefined) {
          entry.formula = "=" + xmlText(c.f);
          entry.cached = value;
          entry.value = null;
        }
        cells[address] = entry;
      }
    }
    sheets[name] = cells;
  }
  return sheets;
}
export async function parseFile(
  bytes: Uint8Array,
  name: string,
  id: string,
): Promise<Artifact> {
  if (!bytes.length || bytes.length > LIMITS.file)
    throw new InputError("Each file must be nonempty and no larger than 4 MB.");
  if (
    name.includes("/") ||
    name.includes("\\") ||
    /[\u0000-\u001f]/.test(name) ||
    name.length > 200
  )
    throw new InputError("Unsupported filename.");
  const format = name.split(".").pop()?.toLowerCase() || "";
  let sheets: Artifact["sheets"];
  if (format === "xlsx") {
    try {
      sheets = xlsx(bytes);
    } catch (e) {
      if (e instanceof InputError) throw e;
      throw new InputError("The workbook could not be read safely.");
    }
  } else {
    const source = new TextDecoder("utf-8", { fatal: true })
      .decode(bytes)
      .replace(/^\uFEFF/, "");
    if (format === "csv") sheets = { Table: toCells(csv(source)) };
    else if (format === "json") {
      let data;
      try {
        data = parseScientificJSON(source) as any;
      } catch {
        throw new InputError(
          "Invalid JSON, duplicate field names, or excessive nesting.",
        );
      }
      if (
        !Array.isArray(data) ||
        !data.length ||
        data.some((x) => !x || Array.isArray(x) || typeof x !== "object")
      )
        throw new InputError("JSON must be an array of flat records.");
      const names = Object.keys(data[0]);
      if (
        data.some(
          (x) =>
            Object.keys(x).length !== names.length ||
            names.some(
              (n) => !(n in x) || (typeof x[n] === "object" && x[n] !== null),
            ),
        )
      )
        throw new InputError("JSON records must have consistent, flat fields.");
      sheets = {
        Table: toCells([names, ...data.map((x) => names.map((n) => x[n]))]),
      };
    } else throw new InputError("Use CSV, XLSX, or JSON files.");
  }
  const hash = [
    ...new Uint8Array(
      await crypto.subtle.digest("SHA-256", bytes as BufferSource),
    ),
  ]
    .map((x) => x.toString(16).padStart(2, "0"))
    .join("");
  return { id, name, sha256: hash, size: bytes.length, format, sheets };
}
export function table(cells: Record<string, Cell>, header = 1) {
  if (
    Object.keys(cells).some(
      (a) =>
        coordinate(a)[0] > header &&
        cells[a].value !== null &&
        !cells[column(coordinate(a)[1]) + header]?.value,
    )
  )
    throw new InputError("A populated column has no heading.");
  const cols = Object.entries(cells)
    .filter(([a]) => coordinate(a)[0] === header)
    .sort(([a], [b]) => coordinate(a)[1] - coordinate(b)[1]);
  const columns = cols.map(([, c]) => String(c.value ?? ""));
  if (
    !columns.length ||
    columns.some((x) => !x) ||
    new Set(columns).size !== columns.length
  )
    throw new InputError("A table needs unique, nonempty column headings.");
  const numbers = [
    ...new Set(
      Object.keys(cells)
        .map((a) => coordinate(a)[0])
        .filter((r) => r > header),
    ),
  ].sort((a, b) => a - b);
  const rows = numbers.map((r) =>
    Object.fromEntries([
      ["_source_row", r],
      ...cols.map(([a], i) => [
        columns[i],
        cells[column(coordinate(a)[1]) + r]?.value ?? null,
      ]),
    ]),
  );
  return { columns, rows };
}
export function rawCellEquivalent(
  address: string,
  a: Cell | undefined,
  b: Cell | undefined,
) {
  if (a?.formula || b?.formula) return false;
  const x = a?.value ?? null,
    y = b?.value ?? null;
  if (x === y) return true;
  if (x === null || y === null) return false;
  const [row, col] = coordinate(address);
  if (row >= 6 && col >= 3) {
    try {
      const a = number(x)!,
        b = number(y)!;
      return (
        a.eq(b) ||
        a.sub(b).abs().lte(Decimal.max(a.abs(), b.abs()).mul("2e-15"))
      );
    } catch {
      return false;
    }
  }
  return false;
}
export function validateToolkit(
  settings: Record<string, any>,
  rows: Record<string, any>[],
  analysis: Artifact | undefined,
  issues: Issue[],
) {
  const error = (code: string, message: string) =>
    issues.push({ code, severity: "error", message });
  if (settings.bypass_file !== "same input file")
    error(
      "BYPASS_FILE",
      "The toolkit bypass source is not the same original report.",
    );
  const counts = [
    "n_bypass",
    "n_reaction",
    "n_blank_excluded",
    "plot_reaction_points",
    "bypass_omit_initial",
    "bypass_points_used",
    "bypass_selected_points",
    "ss_inj_start",
    "ss_inj_end",
  ];
  for (const key of counts) {
    const value = number(settings[key])!;
    if (!value.isInteger() || value.lt(0))
      throw new InputError(
        "Toolkit count and selection fields must be nonnegative integers.",
      );
  }
  if (
    number(settings.ss_inj_start)!.gt(number(settings.ss_inj_end)!) ||
    number(settings.bypass_omit_initial)!.gt(
      number(settings.bypass_points_used)!,
    )
  )
    error("SELECTION_RANGE", "Toolkit selection ranges are inconsistent.");
  const measured = {
    n_bypass: 0,
    n_reaction: 0,
    n_blank_excluded: 0,
    plot_reaction_points: 0,
  };
  for (const row of rows) {
    for (const flag of ["is_blank", "is_bypass", "analysis_include"])
      if (!["True", "False"].includes(String(row[flag])))
        throw new InputError("Toolkit inclusion flags must be True or False.");
    if (row.catalyst_id !== settings.catalyst_id)
      error(
        "CATALYST_ID_MISMATCH",
        "The flows CSV catalyst identifier differs from the summary.",
      );
    measured.n_bypass += Number(row.is_bypass === "True");
    measured.n_blank_excluded += Number(row.is_blank === "True");
    measured.n_reaction += Number(
      row.is_bypass === "False" && row.is_blank === "False",
    );
    measured.plot_reaction_points += Number(row.analysis_include === "True");
    const injection = number(row.inj_num, true);
    if (injection && (!injection.isInteger() || injection.lt(0)))
      error(
        "INJECTION_NUMBER",
        "Injection numbers must be nonnegative integers.",
      );
    for (const key of [
      "conversion",
      "time_on_stream_h",
      "H2",
      "CO2",
      "Ar",
      "CO",
      "CH4",
    ]) {
      if (!(key in row))
        throw new InputError("The flows CSV lacks " + key + ".");
      number(row[key], true);
    }
  }
  for (const [key, value] of Object.entries(measured))
    if (!number(settings[key])!.eq(value))
      error(
        "ROW_COUNT_MISMATCH",
        "The summary " + key + " does not match the flows CSV.",
      );
  const sheet = analysis?.sheets.Settings;
  if (!sheet) {
    error(
      "WORKBOOK_SETTINGS",
      "The analysis workbook must contain its Settings worksheet.",
    );
    return;
  }
  for (const [address, cell] of Object.entries(sheet)) {
    if (!/^A\d+$/.test(address)) continue;
    const key = String(cell.value),
      other = sheet["B" + address.slice(1)];
    if (!(key in settings) || !other || other.value === null || other.formula)
      continue;
    const left = String(other.value),
      right = String(settings[key]);
    if (left === right) continue;
    let close = false;
    try {
      const a = number(left)!,
        b = number(right)!;
      if (a.eq(b)) continue;
      close = a.sub(b).abs().lte(Decimal.max(a.abs(), b.abs()).mul("1e-14"));
    } catch {
      /* Non-numeric settings require exact text equality. */
    }
    issues.push({
      code: close ? "SETTINGS_SERIALIZATION" : "SETTINGS_MISMATCH",
      severity: close ? "info" : "error",
      message: close
        ? "Workbook and CSV serialize " +
          key +
          " at slightly different precision (relative tolerance 1e-14)."
        : "Workbook and CSV disagree on " + key + ".",
      location: "Settings!B" + address.slice(1),
    });
  }
  if (
    Object.values(analysis!.sheets).some((cells) =>
      Object.values(cells).some((c) => c.formula),
    )
  )
    issues.push({
      code: "PARTNER_FORMULAS",
      severity: "warning",
      message:
        "The analysis workbook contains formulas. Numerical results come from the companion CSV files; no formulas were evaluated.",
    });
}
export function buildPreview(
  artifacts: Artifact[],
  partner: string,
  modality: Modality,
  context: Context,
  profile?: any,
): Preview {
  const issues: Issue[] = [],
    sourceTables: Preview["sourceTables"] = [];
  for (const a of artifacts)
    for (const [sheet, cells] of Object.entries(a.sheets)) {
      try {
        const t = table(cells);
        sourceTables.push({ artifactId: a.id, sheet, ...t });
      } catch {
        /* Non-tabular source sheets are retained in the parsed artifact. */
      }
    }
  const preview: Preview = {
    schemaVersion: "1.0.0",
    kind: "unmapped",
    artifacts: artifacts.map(({ sheets, ...a }) => a),
    context,
    normalization: { profile: null },
    processing: { executed: false },
    validation: { version: "catalyst-scientific-context/1.0.0", issues },
    summary: {},
    columns: [],
    rows: [],
    sourceTables,
  };
  const summaryArtifact = artifacts.find((a) =>
      a.name.endsWith("_gc_summary.csv"),
    ),
    flowArtifact = artifacts.find((a) => a.name.endsWith("_gc_flows.csv"));
  if (
    partner === "university-of-rochester" &&
    modality === "reactor" &&
    summaryArtifact &&
    flowArtifact
  ) {
    const st = table(summaryArtifact.sheets.Table),
      ft = table(flowArtifact.sheets.Table);
    if (st.rows.length !== 1)
      throw new InputError("Choose one GC processing revision at a time.");
    const settings = st.rows[0],
      prefix = String(settings.output_prefix);
    const analysis = artifacts.find(
        (a) => a.name === prefix + "_gc_analysis.xlsx",
      ),
      raw = artifacts.find((a) => a.name === settings.source_file);
    if (
      !analysis ||
      !raw ||
      flowArtifact.name !== prefix + "_gc_flows.csv" ||
      summaryArtifact.name !== prefix + "_gc_summary.csv"
    )
      issues.push({
        code: "BUNDLE_MISMATCH",
        severity: "error",
        message:
          "Include matching original XLSX, analysis XLSX, summary CSV, and flows CSV from one toolkit revision.",
      });
    if (
      settings.reaction_type !== "rwgs" ||
      settings.bypass_source !== "same_file"
    )
      issues.push({
        code: "METHOD_UNSUPPORTED",
        severity: "error",
        message: "This GC profile supports toolkit RWGS with same-file bypass.",
      });
    if (raw && analysis) {
      const original = Object.values(raw.sheets).find(
          (c) => c.B1?.value === "Sequence Name",
        ),
        embedded = analysis.sheets["Raw Original"];
      if (
        !original ||
        !embedded ||
        Object.entries({ ...original, ...embedded }).some(
          ([address]) =>
            !rawCellEquivalent(address, original[address], embedded[address]),
        )
      )
        issues.push({
          code: "RAW_MISMATCH",
          severity: "error",
          message: "The embedded raw table differs from the original report.",
        });
      if (original && embedded) {
        const rounded = Object.keys(original).filter((address) => {
          const [r, c] = coordinate(address);
          if (r < 6 || c < 3) return false;
          try {
            return !number(original[address].value)!.eq(
              number(embedded[address]?.value)!,
            );
          } catch {
            return false;
          }
        });
        if (rounded.length)
          issues.push({
            code: "RAW_SERIALIZATION",
            severity: "warning",
            message:
              rounded.length +
              " embedded raw cells have numeric differences. Only relative differences within 2e-15 (Excel serialization) are accepted; original values remain unchanged.",
          });
      }
      if (original) {
        const labels = Object.entries(original)
          .filter(([a]) => /^A\d+$/.test(a) && coordinate(a)[0] >= 6)
          .sort(([a], [b]) => coordinate(a)[0] - coordinate(b)[0])
          .map(([, c]) => c.value);
        if (
          labels.length !== ft.rows.length ||
          labels.some((v, i) => v !== ft.rows[i].label)
        )
          issues.push({
            code: "ROW_MISMATCH",
            severity: "error",
            message:
              "The processed row labels do not match the original source order.",
          });
      }
    }
    preview.kind = "rochester_toolkit_rwgs";
    preview.normalization = {
      profile: "ur-toolkit-rwgs/web-1.0.0",
      species_basis: "Toolkit RWGS configuration",
      original_values_preserved: true,
      raw_comparison:
        "Exact text; numeric report cells from C6 onward allow 2e-15 relative Excel serialization differences, separately flagged.",
    };
    preview.summary = { ...settings };
    validateToolkit(settings, ft.rows, analysis, issues);
    const interval = number(
      context.intervalMin || settings.injection_interval_min,
      true,
    );
    let accepted = 0;
    const start = number(settings.ss_inj_start)!,
      end = number(settings.ss_inj_end)!;
    preview.rows = ft.rows.map((row) => {
      for (const flag of ["analysis_include", "is_blank", "is_bypass"])
        if (!["True", "False"].includes(String(row[flag])))
          throw new InputError("Invalid toolkit inclusion flag.");
      const included = row.analysis_include === "True",
        blank = row.is_blank === "True",
        bypass = row.is_bypass === "True";
      if (
        included &&
        (blank ||
          bypass ||
          /\bstandby\b|\bleak[\s_-]*check\b|\bblank\b/i.test(String(row.label)))
      )
        issues.push({
          code: "NONREACTION_INCLUDED",
          severity: "error",
          message: "A nonreaction row is included in the analysis.",
        });
      const injection = number(row.inj_num, true);
      const result = {
        ...row,
        source_time_on_stream_h: row.time_on_stream_h,
        time_on_stream_s:
          included && interval
            ? interval.mul(60).mul(accepted++).toString()
            : null,
        steady_state_include:
          included &&
          !blank &&
          !bypass &&
          !!injection &&
          injection.gte(start) &&
          injection.lte(end),
        conversion_fraction: number(row.conversion, true)?.toString() ?? null,
      };
      for (const key of [
        "H2",
        "CO2",
        "Ar",
        "CO",
        "CH4",
        "C2H6",
        "C2H4",
        "C3H8",
        "C3H6",
      ])
        if (key in row) number(row[key], true);
      return result;
    });
    preview.columns = [
      "label",
      "inj_num",
      "analysis_include",
      "steady_state_include",
      "source_time_on_stream_h",
      "time_on_stream_s",
      "conversion_fraction",
      "CO2",
      "CO",
      "CH4",
    ];
    for (const key of [
      "conversion_%",
      "carbon_balance_%",
      "sel_CO_%",
      "sel_CH4_%",
    ])
      if (settings[key] != null)
        preview.summary[key.replace("_%", "_fraction")] = number(settings[key])!
          .div(100)
          .toString();
    preview.processing = {
      executed: !!interval,
      method: interval ? "nominal-gc-time-axis/1.0.0" : null,
      interval_min: interval?.toString(),
      basis:
        "Sequential included points; nominal time, not measured timestamps.",
      partner_scientific_results:
        "Imported unchanged; GC conversion/selectivity not reprocessed.",
      producer_version_recorded_by_source: null,
    };
    if (
      interval &&
      context.intervalMin &&
      !interval.eq(number(settings.injection_interval_min)!)
    )
      issues.push({
        code: "INTERVAL_CORRECTED",
        severity: "info",
        message: `The new time axis uses ${interval} minutes. The original ${settings.injection_interval_min}-minute axis is preserved separately.`,
      });
    if (context.catalystMassMg) {
      const mass = number(context.catalystMassMg)!;
      if (mass.lte(0)) throw new InputError("Catalyst mass must be positive.");
      preview.processing.mass_normalized_flow = {
        method: "flow-times-60-divided-by-mass-g/1.0.0",
        mass_mg: mass.toString(),
        source_total_inlet_flow_sccm: settings.total_inlet_flow_sccm,
        requires_confirmed_flow_reference: true,
      };
      preview.summary.confirmed_catalyst_mass_g = mass.div(1000).toString();
      if (settings.total_inlet_flow_sccm)
        preview.summary.confirmed_flow_per_mass_mL_g_h = number(
          settings.total_inlet_flow_sccm,
        )!
          .mul(60)
          .div(mass.div(1000))
          .toString();
    }
    issues.push(
      {
        code: "PARTNER_PROCESSING",
        severity: "warning",
        message:
          "GC quantities are imported toolkit results. Confirm method and calibration provenance before approval.",
      },
      {
        code: "FLOW_BASIS",
        severity: "warning",
        message:
          "Toolkit standard-flow conventions must agree with instrument/MFC calibration. The GHSV-like mass-normalized quantity has units mL/(g h).",
      },
    );
  } else if (profile) {
    const p =
      typeof profile.content === "string"
        ? JSON.parse(profile.content)
        : profile.content;
    if (profile.partner_id !== partner || profile.modality !== modality)
      throw new InputError(
        "Mapping profile belongs to another partner or modality.",
      );
    const candidates = sourceTables.filter(
      (t) =>
        t.sheet === p.sheet &&
        artifacts.find((a) => a.id === t.artifactId)?.format ===
          profile.format &&
        JSON.stringify(t.columns) === JSON.stringify(p.headers),
    );
    const source = candidates[0];
    if (candidates.length !== 1 || !source)
      throw new InputError("Source columns do not match this mapping version.");
    preview.kind = "mapped_table";
    preview.normalization = {
      profile: profile.id,
      version: profile.version,
      digest: profile.digest,
      rules: p.fields,
    };
    preview.columns = p.fields.map((f: any) => f.target);
    preview.rows = source.rows.map((row) => {
      const result: Record<string, unknown> = {
        _source_row: row._source_row,
        _source_artifact: source.artifactId,
      };
      for (const f of p.fields) {
        const val = row[f.source];
        result[f.target] =
          f.kind === "number"
            ? (number(val, true)
                ?.mul(f.factor)
                .add(f.offset || 0)
                .toString() ?? null)
            : Object.hasOwn(f.aliases || {}, String(val))
              ? f.aliases[String(val)]
              : val;
      }
      return result;
    });
    if (p.unmappedColumns?.length)
      issues.push({
        code: "UNMAPPED_FIELDS",
        severity: "warning",
        message:
          "Source columns retained but not standardized: " +
          p.unmappedColumns.join(", "),
      });
    const sourceArtifact = artifacts.find((a) => a.id === source.artifactId)!;
    if (
      Object.values(sourceArtifact.sheets[source.sheet]).some((c) => c.formula)
    )
      issues.push({
        code: "FORMULA_INPUT",
        severity: "error",
        message:
          "The selected worksheet contains formulas. Supply exported values; formulas and cached values are preserved but never evaluated.",
      });
    for (const f of p.fields)
      if (f.kind === "number" && preview.rows.some((r) => r[f.target] === null))
        issues.push({
          code: "MISSING_" + f.target,
          severity: "error",
          message:
            "Mapped numeric field " + f.target + " contains missing values.",
        });
    if (
      modality === "spectroscopy" &&
      !p.fields.some((f: any) =>
        ["wavenumber_cm_inverse", "wavelength_nm", "energy_eV"].includes(
          f.target,
        ),
      )
    )
      issues.push({
        code: "SPECTRAL_AXIS",
        severity: "error",
        message: "Map a supported spectral axis with explicit units.",
      });
    if (
      modality === "spectroscopy" &&
      !p.fields.some((f: any) => f.target === "signal")
    )
      issues.push({
        code: "SPECTRAL_SIGNAL",
        severity: "error",
        message: "Map the measured signal.",
      });
  } else
    issues.push({
      code: "MAPPING_REQUIRED",
      severity: "error",
      message:
        "Choose or create a versioned field mapping for this source format.",
    });
  for (const [key, label] of [
    ["specimenId", "Specimen identifier"],
    ["runId", "Run or dataset identifier"],
    ["acquiredBy", "Acquiring entity"],
    ...(modality === "reactor"
      ? [
          ["reactorType", "Reactor configuration"],
          ["temperatureC", "Temperature"],
          ["pressureKpaAbs", "Absolute pressure"],
          ["catalystMassMg", "Catalyst mass"],
          ["intervalMin", "Injection interval"],
          ["flowBasis", "Flow reference conditions"],
          ["calibration", "Calibration provenance"],
        ]
      : modality === "spectroscopy"
        ? [
            ["technique", "Spectroscopy technique"],
            ["axisUnit", "Independent-axis unit"],
            ["signalUnit", "Signal unit"],
            ["calibration", "Calibration provenance"],
          ]
        : [["synthesisMethod", "Synthesis method"]]),
  ])
    if (!(context as any)[key]?.trim())
      issues.push({
        code: "CONTEXT_" + key,
        severity: "error",
        message: label + " is required.",
      });
  if (
    !context.processingVersion?.trim() &&
    preview.kind === "rochester_toolkit_rwgs"
  )
    issues.push({
      code: "METHOD_CONTEXT",
      severity: "error",
      message:
        "Describe the toolkit method/version evidence or documented legacy provenance limitation.",
    });
  if (!context.acquiredAt)
    issues.push({
      code: "DATE_UNCONFIRMED",
      severity: "warning",
      message: "Acquisition date has not been confirmed.",
    });
  for (const field of [
    "temperatureC",
    "pressureKpaAbs",
    "catalystMassMg",
    "intervalMin",
  ])
    if ((context as any)[field]) {
      const n = number((context as any)[field])!;
      if (field === "temperatureC" ? n.lt("-273.15") : n.lte(0))
        issues.push({
          code: "INVALID_" + field,
          severity: "error",
          message:
            "The " + field + " value is outside its valid physical range.",
        });
    }
  if (!preview.rows.length)
    issues.push({
      code: "NO_RESULTS",
      severity: "error",
      message: "No standardized rows are available.",
    });
  return preview;
}
