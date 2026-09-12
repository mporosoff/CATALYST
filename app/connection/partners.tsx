"use client";
import { useEffect, useState } from "react";
import { call, Choice } from "@/app/workspace";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
export default function Partners() {
  const [data, setData] = useState<any>(null),
    [name, setName] = useState(""),
    [userId, setUserId] = useState(""),
    [partner, setPartner] = useState("university-of-rochester"),
    [role, setRole] = useState("contributor"),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  async function refresh() {
    setData(await call("partners"));
  }
  useEffect(() => {
    refresh().catch((e) => setError(e.message));
  }, []);
  async function save(body: any) {
    setBusy(true);
    setError("");
    try {
      await call("partners", { method: "POST", body: JSON.stringify(body) });
      await refresh();
      setName("");
      setUserId("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel">
      <h2>Partners and access</h2>
      <p className="small">
        Add the contributing organizations here. Each account’s assigned role
        controls access to that partner’s data. Site sharing must also allow the
        account to sign in.
      </p>
      {error && (
        <p className="error-box" role="alert">
          {error}
        </p>
      )}
      <label htmlFor="new-partner" className="mt-4">
        New partner name
      </label>
      <Input
        id="new-partner"
        value={name}
        onChange={(e) => setName(e.target.value)}
        maxLength={120}
      />
      <Button
        className="mt-3"
        variant="outline"
        disabled={busy || !name.trim()}
        onClick={() => save({ action: "partner", name })}
      >
        Add partner
      </Button>
      {data && (
        <>
          <hr className="divider" />
          <h3>Assign an account</h3>
          <div className="form-grid">
            <div>
              <label htmlFor="member-partner">Partner</label>
              <Choice
                id="member-partner"
                value={partner}
                onChange={setPartner}
                options={data.partners}
              />
            </div>
            <div>
              <label htmlFor="member-role">Role</label>
              <Choice
                id="member-role"
                value={role}
                onChange={setRole}
                options={[
                  { id: "viewer", name: "Viewer" },
                  { id: "contributor", name: "Contributor" },
                  { id: "reviewer", name: "Reviewer" },
                  { id: "remove", name: "Remove access" },
                ]}
              />
            </div>
          </div>
          <label htmlFor="member-id">Verified sign-in account ID</label>
          <Input
            id="member-id"
            value={userId}
            onChange={(e) => setUserId(e.target.value)}
            maxLength={200}
          />
          <Button
            className="mt-3"
            disabled={busy || !userId.trim()}
            onClick={() => save({ action: "member", partner, userId, role })}
          >
            Save account access
          </Button>
          <ul className="issues">
            {data.members.map((m: any) => (
              <li key={m.user_id + m.partner_id}>
                {m.user_id} ·{" "}
                {data.partners.find((p: any) => p.id === m.partner_id)?.name} ·{" "}
                {m.role}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
