export type Modality = "reactor" | "synthesis" | "spectroscopy";
export type Issue = {
  code: string;
  message: string;
  severity: "error" | "warning" | "info";
  location?: string;
};
export type Cell = {
  value: string | number | boolean | null;
  formula?: string;
  cached?: unknown;
};
export type Artifact = {
  id: string;
  name: string;
  sha256: string;
  size: number;
  format: string;
  sheets: Record<string, Record<string, Cell>>;
};
export type Context = {
  specimenId?: string;
  runId?: string;
  acquiredBy?: string;
  processedBy?: string;
  acquiredAt?: string;
  reactorType?: string;
  temperatureC?: string;
  pressureKpaAbs?: string;
  catalystMassMg?: string;
  intervalMin?: string;
  technique?: string;
  axisUnit?: string;
  signalUnit?: string;
  synthesisMethod?: string;
  calibration?: string;
  flowBasis?: string;
  processingVersion?: string;
  identityNote?: string;
  reviewNote?: string;
  profileId?: string;
};
export type Preview = {
  schemaVersion: string;
  kind: string;
  artifacts: Omit<Artifact, "sheets">[];
  context: Context;
  normalization: Record<string, unknown>;
  processing: Record<string, unknown>;
  validation: { version: string; issues: Issue[] };
  summary: Record<string, unknown>;
  columns: string[];
  rows: Record<string, unknown>[];
  sourceTables: {
    artifactId: string;
    sheet: string;
    columns: string[];
    rows: Record<string, unknown>[];
  }[];
};
export const modalities = [
  { id: "reactor", name: "Reactor data" },
  { id: "synthesis", name: "Catalyst synthesis" },
  { id: "spectroscopy", name: "Spectroscopy" },
];
