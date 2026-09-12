export const canonicalFields = [
  {
    id: "specimen_id",
    name: "Specimen identifier",
    kind: "text",
    units: ["text"],
  },
  { id: "species", name: "Species name", kind: "text", units: ["text"] },
  {
    id: "time_s",
    name: "Elapsed time (s)",
    kind: "number",
    units: ["s", "min", "h"],
  },
  {
    id: "temperature_K",
    name: "Temperature (K)",
    kind: "number",
    units: ["K", "degC"],
  },
  {
    id: "pressure_Pa_abs",
    name: "Absolute pressure (Pa)",
    kind: "number",
    units: ["Pa absolute", "kPa absolute", "bar absolute", "atm absolute"],
  },
  { id: "mass_g", name: "Mass (g)", kind: "number", units: ["g", "mg", "kg"] },
  {
    id: "flow_mL_min",
    name: "Volumetric flow (mL/min)",
    kind: "number",
    units: ["mL/min", "L/min"],
  },
  {
    id: "conversion_fraction",
    name: "Conversion (fraction)",
    kind: "number",
    units: ["fraction", "%"],
  },
  {
    id: "selectivity_fraction",
    name: "Selectivity (fraction)",
    kind: "number",
    units: ["fraction", "%"],
  },
  {
    id: "loading_fraction",
    name: "Loading (mass fraction)",
    kind: "number",
    units: ["fraction", "%"],
  },
  {
    id: "wavenumber_cm_inverse",
    name: "Wavenumber (1/cm)",
    kind: "number",
    units: ["1/cm"],
  },
  {
    id: "wavelength_nm",
    name: "Wavelength (nm)",
    kind: "number",
    units: ["nm", "um"],
  },
  {
    id: "energy_eV",
    name: "Energy (eV)",
    kind: "number",
    units: ["eV", "keV"],
  },
  {
    id: "signal",
    name: "Signal (unit specified in context)",
    kind: "number",
    units: ["as recorded"],
  },
  {
    id: "precursor_name",
    name: "Precursor name",
    kind: "text",
    units: ["text"],
  },
  { id: "support_name", name: "Support name", kind: "text", units: ["text"] },
] as const;
const conversions: Record<string, [string, string]> = {
  s: ["1", "0"],
  min: ["60", "0"],
  h: ["3600", "0"],
  K: ["1", "0"],
  degC: ["1", "273.15"],
  "Pa absolute": ["1", "0"],
  "kPa absolute": ["1000", "0"],
  "bar absolute": ["100000", "0"],
  "atm absolute": ["101325", "0"],
  g: ["1", "0"],
  mg: ["0.001", "0"],
  kg: ["1000", "0"],
  "mL/min": ["1", "0"],
  "L/min": ["1000", "0"],
  fraction: ["1", "0"],
  "%": ["0.01", "0"],
  "1/cm": ["1", "0"],
  nm: ["1", "0"],
  um: ["1000", "0"],
  eV: ["1", "0"],
  keV: ["1000", "0"],
  "as recorded": ["1", "0"],
  text: ["1", "0"],
};
export function fieldRule(
  source: string,
  target: string,
  unit: string,
  aliases: unknown = {},
) {
  const field = canonicalFields.find((f) => f.id === target);
  if (!field || !(field.units as readonly string[]).includes(unit))
    throw new Error("Choose a supported field and its source unit.");
  if (
    !aliases ||
    typeof aliases !== "object" ||
    Array.isArray(aliases) ||
    Object.keys(aliases).length > 100
  )
    throw new Error(
      "Name aliases must be a small object of exact source names and canonical names.",
    );
  if (
    Object.entries(aliases).some(
      ([k, v]) =>
        !k || typeof v !== "string" || !v || k.length > 200 || v.length > 200,
    )
  )
    throw new Error("Every name alias needs a source name and canonical name.");
  if (field.kind === "number" && Object.keys(aliases).length)
    throw new Error("Name aliases apply only to text fields.");
  const [factor, offset] = conversions[unit];
  return { source, target, kind: field.kind, unit, factor, offset, aliases };
}
