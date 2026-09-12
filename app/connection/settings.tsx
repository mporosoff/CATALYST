"use client";
import { useEffect, useState } from "react";
import { call, Choice } from "@/app/workspace";
import { Button } from "@/components/ui/button";
import Partners from "./partners";
import { Checkbox } from "@/components/ui/checkbox";
export default function Connection() {
  const [data, setData] = useState<any>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [catalog, setCatalog] = useState<any>(null),
    [partner, setPartner] = useState("university-of-rochester"),
    [experiment, setExperiment] = useState(""),
    [ack, setAck] = useState(false),
    [notice, setNotice] = useState("");
  async function refresh() {
    setData(await call("connection"));
  }
  useEffect(() => {
    refresh().catch((e) => setError(e.message));
  }, []);
  async function action(fn: () => Promise<void>) {
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
  return (
    <main className="detail connection-layout">
      <a href="/">← CATALYST workspace</a>
      <h1>SciSure connection</h1>
      <p className="muted">
        Connect the private processing service to your laboratory workspace.
      </p>
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
      {!data ? (
        <p role="status">Loading connection settings…</p>
      ) : (
        <>
          <section className="panel">
            <h2>Private backend</h2>
            <p>
              Your browser uploads files to CATALYST. The server preserves the
              files, records revisions, and makes authenticated SciSure
              requests.
            </p>
            <dl className="summary-list mt-5">
              <div>
                <dt>SciSure instance</dt>
                <dd>{data.tenant}</dd>
              </div>
              <div>
                <dt>API token</dt>
                <dd>
                  {data.configured
                    ? "Stored in deployment secrets"
                    : "Not configured"}
                </dd>
              </div>
              <div>
                <dt>Publication switch</dt>
                <dd>
                  {data.publicationEnabled
                    ? "Enabled for approved revisions"
                    : "Disabled"}
                </dd>
              </div>
            </dl>
            {!data.configured && (
              <p className="warning-box">
                Add SCISURE_API_TOKEN through the deployment platform’s secret
                mechanism. The application has no token input or token display.
              </p>
            )}
            <Button
              className="mt-5"
              disabled={busy || !data.configured}
              onClick={() =>
                action(async () => {
                  setCatalog(
                    await call("connection", {
                      method: "POST",
                      body: JSON.stringify({ action: "check" }),
                    }),
                  );
                  setNotice(
                    "SciSure responded. Review the active group and choose the test experiment.",
                  );
                  await refresh();
                })
              }
            >
              {busy ? "Checking…" : "Check connection and find experiments"}
            </Button>
          </section>
          <section className="panel">
            <h2>Test destination</h2>
            <p>
              A SciSure group is the laboratory workspace controlling access.
              Inside it, a project contains studies, and studies contain
              experiments.
            </p>
            <p className="small mt-3">
              Suggested structure: CATALYST Testing → Connector Tests → GC
              Upload Test. Use an unsigned experiment dedicated to connector
              testing.
            </p>
            {catalog && (
              <>
                <p className="success-box">
                  Active group: <strong>{catalog.group.name}</strong> · ID{" "}
                  {catalog.group.id}
                </p>
                <div className="form-grid">
                  <div>
                    <label htmlFor="destination-partner">Partner</label>
                    <Choice
                      id="destination-partner"
                      value={partner}
                      onChange={(v) => {
                        setPartner(v);
                        setAck(false);
                      }}
                      options={data.partners}
                    />
                  </div>
                  <div>
                    <label htmlFor="destination-experiment">
                      Test experiment
                    </label>
                    <Choice
                      id="destination-experiment"
                      value={experiment || "none"}
                      onChange={(v) => {
                        setExperiment(v === "none" ? "" : v);
                        setAck(false);
                      }}
                      options={[
                        { id: "none", name: "Choose an existing experiment" },
                        ...catalog.experiments.map((e: any) => ({
                          id: String(e.id),
                          name: e.name + " · ID " + e.id,
                        })),
                      ]}
                    />
                  </div>
                </div>
                {!catalog.experiments.length && (
                  <p className="warning-box">
                    No experiments are visible. Use the button below to create
                    the suggested test destination.
                  </p>
                )}
                <label className="check-row" htmlFor="destination-ack">
                  <Checkbox
                    id="destination-ack"
                    checked={ack}
                    onCheckedChange={(v) => setAck(v === true)}
                  />
                  <span>
                    This group is appropriate for this partner’s test
                    publications. Use the chosen experiment, or create the
                    suggested test destination.
                  </span>
                </label>
                <Button
                  disabled={busy || !ack || !experiment}
                  onClick={() =>
                    action(async () => {
                      await call("connection", {
                        method: "POST",
                        body: JSON.stringify({
                          action: "destination",
                          partner,
                          experimentId: Number(experiment),
                          acknowledge: ack,
                        }),
                      });
                      await refresh();
                      setNotice("The test destination is verified and saved.");
                    })
                  }
                >
                  Verify and save destination
                </Button>
                <Button
                  className="mt-4"
                  variant="outline"
                  disabled={busy || !ack}
                  onClick={() =>
                    action(async () => {
                      await call("connection", {
                        method: "POST",
                        body: JSON.stringify({
                          action: "create-test",
                          partner,
                          groupId: catalog.group.id,
                          acknowledge: ack,
                        }),
                      });
                      await refresh();
                      setNotice(
                        "The CATALYST test project, study, and experiment are ready.",
                      );
                    })
                  }
                >
                  Create the suggested test destination
                </Button>
              </>
            )}
            {data.destinations.map((d: any) => (
              <div className="success-box" key={d.partner}>
                <strong>
                  {data.partners.find((p: any) => p.id === d.partner)?.name ||
                    d.partner}
                </strong>
                <p>
                  {d.groupName} → {d.studyName} → {d.experimentName}
                </p>
                <p className="small">
                  Experiment ID {d.experimentId} · verified{" "}
                  {new Date(d.verifiedAt).toLocaleString()}
                </p>
              </div>
            ))}
          </section>
          <Partners />
          <p className="small">
            The connector checks the active group and experiment signature
            before writes. It adds revision-specific sections and verifies every
            uploaded file by checksum.
          </p>
        </>
      )}
    </main>
  );
}
