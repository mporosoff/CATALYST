import { env } from "cloudflare:workers";
import {
  actor,
  db,
  setting,
  checkOrigin,
  jsonBody,
  now,
  respond,
  fail,
  Problem,
  event,
} from "@/lib/server";
import { client, saveDestination } from "@/lib/connector";
import { SciSureError } from "@/lib/scisure-client";
import { createTestDestination } from "@/lib/test-destination";
export async function GET() {
  try {
    const user = await actor();
    if (!user.admin)
      throw new Problem(
        403,
        "Only the workspace owner manages the connection.",
      );
    return respond({
      configured: !!env.SCISURE_API_TOKEN,
      tenant: env.SCISURE_BASE_URL || "https://sandbox.elabjournal.com",
      publicationEnabled: env.SCISURE_PUBLICATION_ENABLED === "true",
      partners: (
        await db().prepare("SELECT id,name FROM partners ORDER BY name").all()
      ).results,
      destinations: (
        await db()
          .prepare(
            "SELECT key,value FROM settings WHERE key LIKE 'destination/%'",
          )
          .all()
      ).results.map((x: any) => ({
        partner: x.key.slice(12),
        ...JSON.parse(x.value),
      })),
      lastCheck: await setting("connection_check"),
    });
  } catch (e) {
    return fail(e);
  }
}
export async function POST(request: Request) {
  try {
    checkOrigin(request);
    const user = await actor();
    if (!user.admin) throw new Problem(403, "Only the owner manages SciSure.");
    const body = await jsonBody(request),
      c = client();
    if (body.action === "check") {
      const group = await c.request("/api/v1/groups/active"),
        experiments = (await c.list("/api/v1/experiments")).filter(
          (e) => e.groupID === group.groupID && !e.deleted && !e.template,
        );
      await db()
        .prepare(
          "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        )
        .bind("connection_check", now())
        .run();
      await event(user, "connection_checked", null, { groupId: group.groupID });
      return respond({
        group: {
          id: group.groupID,
          name: group.name || group.groupName || String(group.groupID),
        },
        experiments: experiments.map((e) => ({
          id: e.experimentID,
          name: e.name,
          studyId: e.studyID,
        })),
      });
    }
    if (body.action === "destination") {
      if (
        body.acknowledge !== true ||
        !(await db()
          .prepare("SELECT id FROM partners WHERE id=?")
          .bind(String(body.partner))
          .first())
      )
        throw new Problem(400, "Choose a partner and confirm the destination.");
      const d = await saveDestination(body.partner, body.experimentId);
      await event(user, "destination_verified", null, {
        partner: body.partner,
        ...d,
      });
      return respond({ destination: d });
    }
    if (body.action === "create-test") {
      if (
        body.acknowledge !== true ||
        !(await db()
          .prepare("SELECT id FROM partners WHERE id=?")
          .bind(String(body.partner))
          .first())
      )
        throw new Problem(400, "Choose a partner and confirm the test group.");
      const d = await createTestDestination(body.partner, body.groupId);
      await event(user, "test_destination_created", null, {
        partner: body.partner,
        ...d,
      });
      return respond({ destination: d });
    }
    throw new Problem(400, "Unknown connection action.");
  } catch (e) {
    return e instanceof SciSureError
      ? respond({ error: e.message }, e.status)
      : fail(e);
  }
}
