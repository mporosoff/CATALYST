"use client";
import { useEffect, useState } from "react";
import {
  UploadCloud,
  FlaskConical,
  FileCheck2,
  Link2,
  ChevronRight,
  Files,
  Clock3,
  Plus,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { modalities } from "@/lib/contracts";
export async function call(path: string, options: RequestInit = {}) {
  const response = await fetch("/api/" + path, {
    ...options,
    headers:
      options.body instanceof FormData
        ? options.headers
        : { "Content-Type": "application/json", ...options.headers },
  });
  const data: any = await response.json();
  if (!response.ok)
    throw new Error(data.error || "The request could not be completed.");
  return data;
}
export function Choice({
  id,
  value,
  onChange,
  options,
}: {
  id: string;
  value: string;
  onChange: (v: string) => void;
  options: { id: string; name: string }[];
}) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger id={id} className="field-select">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {options.map((o) => (
          <SelectItem key={o.id} value={o.id}>
            {o.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
export default function Workspace({ name }: { name: string }) {
  const [workspace, setWorkspace] = useState<any>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [partner, setPartner] = useState("university-of-rochester"),
    [modality, setModality] = useState("reactor"),
    [files, setFiles] = useState<File[]>([]),
    [title, setTitle] = useState(""),
    [tab, setTab] = useState("upload");
  async function refresh() {
    try {
      setWorkspace(await call("workspace"));
    } catch (e) {
      setError((e as Error).message);
    }
  }
  useEffect(() => {
    refresh();
  }, []);
  async function upload() {
    setBusy(true);
    setError("");
    try {
      const form = new FormData();
      form.set("partner", partner);
      form.set("modality", modality);
      form.set("title", title);
      files.forEach((f) => form.append("files", f));
      const data = await call("submissions", { method: "POST", body: form });
      window.location.assign("/submissions/" + data.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="shell">
      <header className="topbar">
        <a className="wordmark" href="/">
          CATALYST<span>DATA WORKSPACE</span>
        </a>
        <div className="account">
          <span>{name}</span>
          <a href="/signout-with-chatgpt?return_to=/" target="_top">
            Sign out
          </a>
        </div>
      </header>
      <div className="workspace-head">
        <div>
          <p className="eyebrow">RESEARCH DATA</p>
          <h1>Upload. Review. Keep the history.</h1>
          <p className="muted">
            Bring source files and processed results into one traceable record.
          </p>
        </div>
        <a className="connection-pill" href="/connection">
          <Link2 size={17} />
          {workspace?.connection.configured
            ? "SciSure connection"
            : "Connect SciSure"}
          <ChevronRight size={15} />
        </a>
      </div>
      <main className="main-grid">
        <section className="work-panel">
          <Tabs value={tab} onValueChange={setTab}>
            <TabsList className="work-tabs" variant="line">
              <TabsTrigger value="upload">
                <UploadCloud />
                New upload
              </TabsTrigger>
              <TabsTrigger value="history">
                <Clock3 />
                Submissions {workspace?.submissions?.length || ""}
              </TabsTrigger>
            </TabsList>
            <TabsContent value="upload">
              <div className="section-heading">
                <span className="step-number">01</span>
                <div>
                  <h2>Choose the source</h2>
                  <p>
                    Files remain associated with the partner who supplied them.
                  </p>
                </div>
              </div>
              <div className="form-grid">
                <div>
                  <label htmlFor="partner">Partner</label>
                  <Choice
                    id="partner"
                    value={partner}
                    onChange={setPartner}
                    options={
                      workspace?.partners || [
                        {
                          id: "university-of-rochester",
                          name: "University of Rochester",
                        },
                      ]
                    }
                  />
                </div>
                <div>
                  <label htmlFor="modality">Data type</label>
                  <Choice
                    id="modality"
                    value={modality}
                    onChange={setModality}
                    options={modalities}
                  />
                </div>
              </div>
              <label htmlFor="title">Run or dataset name</label>
              <Input
                id="title"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="e.g. Catalyst screening — run 01"
                maxLength={160}
              />
              <div className="section-heading">
                <span className="step-number">02</span>
                <div>
                  <h2>Add your files</h2>
                  <p>
                    For toolkit GC results, include the original report,
                    analysis workbook, summary CSV, and flows CSV.
                  </p>
                </div>
              </div>
              <label
                className="drop-zone"
                htmlFor="files"
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                  e.preventDefault();
                  setFiles(Array.from(e.dataTransfer.files));
                }}
              >
                <UploadCloud size={32} />
                <strong>Choose files or drop them here</strong>
                <span>CSV, XLSX, or JSON · up to 6 files · 8 MB combined</span>
                <input
                  id="files"
                  type="file"
                  multiple
                  accept=".csv,.xlsx,.json"
                  onChange={(e) => setFiles(Array.from(e.target.files || []))}
                />
              </label>
              {files.length > 0 && (
                <ul className="file-list">
                  {files.map((f, i) => (
                    <li key={i}>
                      <Files size={17} />
                      <span>{f.name}</span>
                      <small>{(f.size / 1024).toFixed(1)} KB</small>
                    </li>
                  ))}
                </ul>
              )}
              <div className="form-footer">
                <span>Original files are preserved unchanged.</span>
                <Button
                  onClick={upload}
                  disabled={
                    busy || !files.length || !title.trim() || !workspace
                  }
                >
                  {busy ? "Saving files…" : "Upload and review"}
                  <ChevronRight />
                </Button>
              </div>
            </TabsContent>
            <TabsContent value="history">
              <h2 className="mt-6">Your submissions</h2>
              {workspace?.submissions?.length ? (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Dataset</TableHead>
                      <TableHead>Type</TableHead>
                      <TableHead>Review</TableHead>
                      <TableHead>Received</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {workspace.submissions.map((s: any) => (
                      <TableRow key={s.id}>
                        <TableCell>
                          <a
                            className="record-link"
                            href={"/submissions/" + s.id}
                          >
                            {s.title}
                          </a>
                        </TableCell>
                        <TableCell>{s.modality}</TableCell>
                        <TableCell>
                          {s.approved_by ? "Approved" : "Awaiting review"}
                        </TableCell>
                        <TableCell>
                          {new Date(s.created_at).toLocaleDateString()}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              ) : (
                <div className="empty">
                  <Files />
                  <h3>No submissions yet</h3>
                  <p>Your uploaded datasets and revisions will appear here.</p>
                  <Button variant="outline" onClick={() => setTab("upload")}>
                    <Plus />
                    New upload
                  </Button>
                </div>
              )}
            </TabsContent>
          </Tabs>
          {error && (
            <p className="error-box" role="alert">
              {error}
            </p>
          )}
        </section>
        <aside>
          <div className="guide-card">
            <p className="eyebrow">EVERY REVISION HAS A RECORD</p>
            <ol>
              <li>
                <UploadCloud />
                <div>
                  <strong>Preserve the source</strong>
                  <p>Raw files, source labels, and checksums stay together.</p>
                </div>
              </li>
              <li>
                <FlaskConical />
                <div>
                  <strong>Review the science</strong>
                  <p>
                    Inspect mapped fields, units, context, and processing
                    choices.
                  </p>
                </div>
              </li>
              <li>
                <FileCheck2 />
                <div>
                  <strong>Approve a revision</strong>
                  <p>Changes create a new revision with its own review.</p>
                </div>
              </li>
              <li>
                <Link2 />
                <div>
                  <strong>Publish to SciSure</strong>
                  <p>
                    Approved data is attached to the selected laboratory record.
                  </p>
                </div>
              </li>
            </ol>
          </div>
          <div className="aside-note">
            <h3>Rochester GC workflow</h3>
            <p>
              CATALYST recognizes the catalysis toolkit’s complete RWGS result
              bundle. Other formats can use explicit field mappings.
            </p>
          </div>
        </aside>
      </main>
      <footer className="site-footer">
        CATALYST{" "}
        <span>Source · Normalization · Processing · Review · Publication</span>
      </footer>
    </div>
  );
}
