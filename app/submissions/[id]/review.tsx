"use client";
import { useEffect, useState } from "react";
import { call, Choice } from "@/app/workspace";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {
  Table,
  TableHeader,
  TableRow,
  TableHead,
  TableBody,
  TableCell,
} from "@/components/ui/table";
import { canonicalFields } from "@/lib/fields";
import { registerReviewTools } from "@/lib/webmcp";
import type { Context } from "@/lib/contracts";
const common = [
  ["specimenId", "Specimen identifier"],
  ["runId", "Run or dataset identifier"],
  ["acquiredBy", "Acquiring entity / laboratory"],
  ["processedBy", "Processing entity / laboratory"],
  ["acquiredAt", "Confirmed acquisition date"],
  ["identityNote", "Source label corrections / identity evidence"],
];
const reactor = [
  ["reactorType", "Reactor configuration"],
  ["temperatureC", "Temperature (°C)"],
  ["pressureKpaAbs", "Absolute pressure (kPa)"],
  ["catalystMassMg", "Confirmed catalyst mass (mg)"],
  ["intervalMin", "GC injection interval (min)"],
  ["flowBasis", "Flow reference conditions / evidence"],
  ["calibration", "Calibration reference / method"],
  ["processingVersion", "Processing method / version evidence"],
];
const spectroscopy = [
  ["technique", "Spectroscopy technique"],
  ["axisUnit", "Independent-axis unit"],
  ["signalUnit", "Signal unit"],
  ["calibration", "Calibration reference / method"],
  ["processingVersion", "Processing method / version evidence"],
];
export default function Review({ id }: { id: string }) {
  const [data, setData] = useState<any>(null),
    [context, setContext] = useState<Context>({}),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false),
    [ack, setAck] = useState(false),
    [note, setNote] = useState(""),
    [profiles, setProfiles] = useState<any[]>([]),
    [source, setSource] = useState(""),
    [mappingName, setMappingName] = useState(""),
    [sourceVersion, setSourceVersion] = useState(""),
    [fields, setFields] = useState<any[]>([]),
    [selected, setSelected] = useState(""),
    [operations, setOperations] = useState<any[]>([]);
  async function refresh(revision = "") {
    const result = await call(
      "submissions/" + id + (revision ? "?revision=" + revision : ""),
    );
    setData(result);
    setContext(result.preview.context);
    setAck(false);
    setNote("");
    setSelected(result.revision.id);
    setProfiles((await call("profiles?submission=" + id)).profiles);
    setOperations((await call("submissions/" + id + "/publish")).operations);
    return result;
  }
  useEffect(() => {
    refresh().catch((e) => setError(e.message));
  }, [id]);
  const dirty =
      data && JSON.stringify(context) !== JSON.stringify(data.preview.context),
    latest = data && selected === data.submission.latest_revision;
  async function action(fn: () => Promise<any>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function saveContext(next: Context = context) {
    if (!data || !latest)
      throw new Error("Select the current revision before saving.");
    const result = await call("submissions/" + id, {
      method: "POST",
      body: JSON.stringify({
        parent: data.revision.id,
        digest: data.revision.digest,
        context: next,
      }),
    });
    await refresh(result.revision);
    setNotice("A new revision is saved. Earlier revisions remain available.");
    return { revision: result.revision };
  }
  useEffect(
    () => registerReviewTools(() => data, saveContext),
    [data, context],
  );
  async function createMapping() {
    const t = data.preview.sourceTables[Number(source)],
      result = await call("profiles", {
        method: "POST",
        body: JSON.stringify({
          submissionId: id,
          artifactId: t.artifactId,
          sheet: t.sheet,
          name: mappingName,
          sourceVersion,
          fields: fields.map((f) => ({
            ...f,
            aliases: JSON.parse(f.aliasText || "{}"),
          })),
        }),
      });
    await saveContext({ ...context, profileId: result.id });
  }
  async function approve() {
    await call("submissions/" + id + "/approve", {
      method: "POST",
      body: JSON.stringify({
        revision: data.revision.id,
        digest: data.revision.digest,
        note,
        acknowledge: ack,
      }),
    });
    await refresh();
    setNotice("This exact revision is approved.");
  }
  async function publish() {
    try {
      await call("submissions/" + id + "/publish", {
        method: "POST",
        body: JSON.stringify({
          revision: data.revision.id,
          digest: data.revision.digest,
        }),
      });
      setNotice("Approved files were published and verified in SciSure.");
    } finally {
      await refresh();
    }
  }
  function field(index: number, patch: any) {
    setFields(fields.map((f, i) => (i === index ? { ...f, ...patch } : f)));
  }
  if (!data)
    return (
      <main className="detail">
        <a href="/">← CATALYST</a>
        <h1>Review your upload</h1>
        <p role={error ? "alert" : "status"}>
          {error || "Loading the saved revision…"}
        </p>
      </main>
    );
  const p = data.preview,
    issues = p.validation.issues,
    errors = issues.filter((i: any) => i.severity === "error"),
    table = p.sourceTables[Number(source)],
    contextFields = [
      ...common,
      ...(data.submission.modality === "reactor"
        ? reactor
        : data.submission.modality === "spectroscopy"
          ? spectroscopy
          : [
              ["synthesisMethod", "Synthesis method / protocol"],
              ["processingVersion", "Processing method / version evidence"],
            ]),
    ];
  return (
    <main className="detail">
      <a href="/">← CATALYST workspace</a>
      <h1>{data.submission.title}</h1>
      <p className="muted">
        {data.submission.modality} ·{" "}
        <span className="status-tag">
          {data.approval ? "Approved revision" : "Awaiting review"}
        </span>
      </p>
      <div className="actions">
        <label htmlFor="revision">Revision</label>
        <Choice
          id="revision"
          value={selected}
          onChange={(v) => action(() => refresh(v))}
          options={data.revisions.map((r: any) => ({
            id: r.id,
            name:
              "Revision " +
              r.number +
              " · " +
              new Date(r.created_at).toLocaleString(),
          }))}
        />
        <Button variant="outline" asChild>
          <a href={`/api/submissions/${id}/download?revision=${selected}`}>
            Download standardized JSON
          </a>
        </Button>
      </div>
      {!latest && (
        <p className="warning-box">
          This is an earlier revision. Select the latest revision to make
          changes.
        </p>
      )}
      {error && (
        <p className="error-box" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="success-box" role="status">
          {notice}
        </p>
      )}
      <div className="metrics">
        {[
          ["Source files", p.artifacts.length],
          ["Standardized rows", p.rows.length],
          ["Blocking issues", errors.length],
          ["Saved revision", data.revision.number],
        ].map(([label, value]) => (
          <div className="metric" key={label}>
            <small>{label}</small>
            <strong>{value}</strong>
          </div>
        ))}
      </div>
      <div className="detail-grid">
        <section>
          <Tabs defaultValue="context">
            <TabsList className="work-tabs" variant="line">
              <TabsTrigger value="context">Scientific context</TabsTrigger>
              <TabsTrigger value="data">Data preview</TabsTrigger>
              <TabsTrigger value="mapping">Field mapping</TabsTrigger>
              <TabsTrigger value="history">Provenance</TabsTrigger>
            </TabsList>
            <TabsContent value="context">
              <div className="panel">
                <h2>Confirm what this dataset means</h2>
                <p className="small">
                  Enter confirmed values and references. Saving creates a new
                  revision and reruns validation.
                </p>
                <fieldset
                  disabled={busy || !latest || !data.canEdit}
                  className="context-grid mt-5"
                >
                  {contextFields.map(([key, label]) => (
                    <div
                      key={key}
                      className={
                        [
                          "calibration",
                          "flowBasis",
                          "processingVersion",
                          "identityNote",
                          "synthesisMethod",
                        ].includes(key)
                          ? "wide"
                          : ""
                      }
                    >
                      <label htmlFor={key}>{label}</label>
                      <Input
                        id={key}
                        value={(context as any)[key] || ""}
                        onChange={(e) =>
                          setContext({ ...context, [key]: e.target.value })
                        }
                        maxLength={4000}
                      />
                    </div>
                  ))}
                </fieldset>
                <div className="actions">
                  <Button
                    onClick={() => action(() => saveContext())}
                    disabled={busy || !dirty || !latest || !data.canEdit}
                  >
                    {busy ? "Saving…" : "Save new revision"}
                  </Button>
                  {dirty && <span className="small">Unsaved changes</span>}
                </div>
              </div>
            </TabsContent>
            <TabsContent value="data">
              <div className="panel">
                <h2>Standardized output</h2>
                <p className="small">
                  Previewing up to 100 rows. The download contains every row;
                  numbers retain decimal precision as text.
                </p>
                <div className="table-scroll">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        {p.columns.map((c: string) => (
                          <TableHead key={c}>{c}</TableHead>
                        ))}
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {p.rows.slice(0, 100).map((r: any, i: number) => (
                        <TableRow key={i}>
                          {p.columns.map((c: string) => (
                            <TableCell key={c}>
                              {r[c] === null ? "—" : String(r[c] ?? "")}
                            </TableCell>
                          ))}
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
                {!p.rows.length && (
                  <p className="empty">
                    Choose a field mapping to create standardized rows.
                  </p>
                )}
                <h2>Summary and imported settings</h2>
                <dl className="summary-list">
                  {Object.entries(p.summary).map(([k, v]) => (
                    <div key={k}>
                      <dt>{k}</dt>
                      <dd>{String(v ?? "—")}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            </TabsContent>
            <TabsContent value="mapping">
              <div className="panel">
                <h2>Versioned field mappings</h2>
                {p.kind === "rochester_toolkit_rwgs" ? (
                  <p>
                    The complete Rochester RWGS bundle uses its built-in
                    mapping. Processing choices and corrected context are
                    recorded in each revision.
                  </p>
                ) : (
                  <>
                    <label htmlFor="profile">Saved mapping</label>
                    <Choice
                      id="profile"
                      value={context.profileId || "none"}
                      onChange={(v) =>
                        setContext({
                          ...context,
                          profileId: v === "none" ? "" : v,
                        })
                      }
                      options={[
                        { id: "none", name: "Choose a mapping" },
                        ...profiles.map((p) => ({
                          id: p.id,
                          name: `${p.name} · ${p.source_version} · v${p.version}`,
                        })),
                      ]}
                    />
                    <Button
                      className="mt-4"
                      disabled={busy || !dirty || !latest || !data.canEdit}
                      onClick={() => action(() => saveContext())}
                    >
                      Apply to a new revision
                    </Button>
                    {data.canReview && latest && (
                      <>
                        <hr className="divider" />
                        <h3>Create a mapping version</h3>
                        <div className="form-grid">
                          <div>
                            <label htmlFor="map-name">Mapping name</label>
                            <Input
                              id="map-name"
                              value={mappingName}
                              onChange={(e) => setMappingName(e.target.value)}
                            />
                          </div>
                          <div>
                            <label htmlFor="source-version">
                              Instrument export / source version
                            </label>
                            <Input
                              id="source-version"
                              value={sourceVersion}
                              onChange={(e) => setSourceVersion(e.target.value)}
                            />
                          </div>
                        </div>
                        <label htmlFor="source-table">
                          Source worksheet or table
                        </label>
                        <Choice
                          id="source-table"
                          value={source || "none"}
                          onChange={(v) => {
                            setSource(v === "none" ? "" : v);
                            setFields([]);
                          }}
                          options={[
                            { id: "none", name: "Choose a source table" },
                            ...p.sourceTables.map((t: any, i: number) => ({
                              id: String(i),
                              name:
                                p.artifacts.find(
                                  (a: any) => a.id === t.artifactId,
                                ).name +
                                " · " +
                                t.sheet,
                            })),
                          ]}
                        />
                        {fields.map((f, i) => (
                          <div className="mapping-row" key={i}>
                            <div>
                              <label htmlFor={"source-" + i}>
                                Source column
                              </label>
                              <Choice
                                id={"source-" + i}
                                value={f.source}
                                onChange={(v) => field(i, { source: v })}
                                options={table.columns.map((c: string) => ({
                                  id: c,
                                  name: c,
                                }))}
                              />
                            </div>
                            <div>
                              <label htmlFor={"target-" + i}>
                                Canonical field
                              </label>
                              <Choice
                                id={"target-" + i}
                                value={f.target}
                                onChange={(v) =>
                                  field(i, {
                                    target: v,
                                    unit: canonicalFields.find(
                                      (x) => x.id === v,
                                    )!.units[0],
                                    aliasText: "{}",
                                  })
                                }
                                options={canonicalFields.map((f) => ({
                                  id: f.id,
                                  name: f.name,
                                }))}
                              />
                            </div>
                            <div>
                              <label htmlFor={"unit-" + i}>Source unit</label>
                              <Choice
                                id={"unit-" + i}
                                value={f.unit}
                                onChange={(v) => field(i, { unit: v })}
                                options={canonicalFields
                                  .find((x) => x.id === f.target)!
                                  .units.map((u) => ({ id: u, name: u }))}
                              />
                            </div>
                            <Button
                              aria-label={"Remove mapping " + (i + 1)}
                              variant="ghost"
                              onClick={() =>
                                setFields(fields.filter((_, j) => i !== j))
                              }
                            >
                              ×
                            </Button>
                            {canonicalFields.find((x) => x.id === f.target)!
                              .kind === "text" && (
                              <div className="wide">
                                <label htmlFor={"alias-" + i}>
                                  Exact name aliases (optional JSON)
                                </label>
                                <Input
                                  id={"alias-" + i}
                                  value={f.aliasText || "{}"}
                                  onChange={(e) =>
                                    field(i, { aliasText: e.target.value })
                                  }
                                  placeholder={
                                    '{"source name":"canonical name"}'
                                  }
                                />
                              </div>
                            )}
                          </div>
                        ))}
                        <div className="actions">
                          <Button
                            variant="outline"
                            disabled={source === ""}
                            onClick={() =>
                              setFields([
                                ...fields,
                                {
                                  source: table.columns[0],
                                  target: "specimen_id",
                                  unit: "text",
                                  aliasText: "{}",
                                },
                              ])
                            }
                          >
                            Add field
                          </Button>
                          <Button
                            disabled={
                              busy ||
                              !fields.length ||
                              !mappingName.trim() ||
                              !sourceVersion.trim()
                            }
                            onClick={() => action(createMapping)}
                          >
                            Save mapping and revision
                          </Button>
                        </div>
                        <p className="small">
                          Only explicit unit conversions and exact name aliases
                          are applied. Unmapped columns remain in the source and
                          require review.
                        </p>
                      </>
                    )}
                  </>
                )}
              </div>
            </TabsContent>
            <TabsContent value="history">
              <div className="panel">
                <h2>Source files</h2>
                <ul className="file-list">
                  {p.artifacts.map((a: any) => (
                    <li key={a.id}>
                      <div>
                        <a
                          className="record-link"
                          href={`/api/submissions/${id}/download?artifact=${a.id}`}
                        >
                          {a.name}
                        </a>
                        <p className="small break">SHA-256: {a.sha256}</p>
                      </div>
                    </li>
                  ))}
                </ul>
                <h2>Normalization</h2>
                <pre className="json-detail">
                  {JSON.stringify(p.normalization, null, 2)}
                </pre>
                <h2>Scientific processing</h2>
                <pre className="json-detail">
                  {JSON.stringify(p.processing, null, 2)}
                </pre>
                <h2>Revision integrity</h2>
                <p className="small break">{data.revision.digest}</p>
                <h2>Activity</h2>
                <ol className="audit-list">
                  {data.audit.map((a: any, i: number) => (
                    <li key={i}>
                      <strong>{a.action.replaceAll("_", " ")}</strong>
                      <p className="small">
                        {new Date(a.created_at).toLocaleString()} · {a.user_id}
                      </p>
                    </li>
                  ))}
                </ol>
              </div>
            </TabsContent>
          </Tabs>
        </section>
        <aside>
          <div className="panel">
            <h2>Validation & review</h2>
            <ul className="issues">
              {issues.map((i: any, n: number) => (
                <li key={n} data-severity={i.severity}>
                  <strong>
                    {i.severity === "error"
                      ? "Required"
                      : i.severity === "warning"
                        ? "Review"
                        : "Recorded"}
                    :{" "}
                  </strong>
                  {i.message}
                </li>
              ))}
            </ul>
            {data.approval ? (
              <div className="success-box">
                <strong>Revision approved</strong>
                <p>{data.approval.note}</p>
                <p className="small">
                  {new Date(data.approval.created_at).toLocaleString()}
                </p>
              </div>
            ) : (
              <>
                <label htmlFor="review-note" className="mt-5">
                  Review decision and warning resolution
                </label>
                <Textarea
                  id="review-note"
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  maxLength={4000}
                />
                <label className="check-row" htmlFor="ack">
                  <Checkbox
                    id="ack"
                    checked={ack}
                    onCheckedChange={(v) => setAck(v === true)}
                  />
                  <span>
                    I reviewed the source, scientific context, standardized
                    values, and every warning.
                  </span>
                </label>
                <Button
                  disabled={
                    busy ||
                    dirty ||
                    !latest ||
                    !data.canReview ||
                    !!errors.length ||
                    !ack ||
                    note.trim().length < 10
                  }
                  onClick={() => action(approve)}
                >
                  Approve revision {data.revision.number}
                </Button>
              </>
            )}
            <hr className="divider" />
            <h3>Publish to SciSure</h3>
            <p className="small mt-2">
              Files are attached to the partner’s verified experiment. Each
              uploaded file is downloaded again to verify its checksum.
            </p>
            <div className="actions">
              <Button
                disabled={
                  busy || dirty || !latest || !data.approval || !data.canReview
                }
                onClick={() => action(publish)}
              >
                {busy
                  ? "Working…"
                  : data.publications.some((p: any) => p.state === "unknown")
                    ? "Reconcile and continue"
                    : "Publish approved revision"}
              </Button>
            </div>
            <a className="record-link small" href="/connection">
              Review SciSure destination
            </a>
            {data.publications.map((p: any) => (
              <p className="status-tag mt-3" key={p.id}>
                {p.state} · {p.tenant}
              </p>
            ))}
            {operations.length > 0 && (
              <details className="mt-4">
                <summary>Publication steps and SciSure IDs</summary>
                <ul className="issues">
                  {operations.map((o) => (
                    <li key={o.id}>
                      {o.name}: {o.state}
                      {o.remote_id ? " · ID " + o.remote_id : ""}
                      {o.evidence && <p className="small">{o.evidence}</p>}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        </aside>
      </div>
    </main>
  );
}
