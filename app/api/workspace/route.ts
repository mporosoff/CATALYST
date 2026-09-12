import { actor, db, fail, respond } from "@/lib/server";
import { env } from "cloudflare:workers";
export const dynamic = "force-dynamic";
export async function GET() {
  try {
    const user = await actor();
    const partners = (
      await db()
        .prepare("SELECT id,name FROM partners ORDER BY name")
        .all<{ id: string; name: string }>()
    ).results.filter(
      (p) => user.admin || user.memberships.some((m) => m.partner_id === p.id),
    );
    const ids = partners.map((p) => p.id);
    const all = ids.length
      ? (
          await db()
            .prepare(
              "SELECT s.*, a.reviewer AS approved_by FROM submissions s LEFT JOIN approvals a ON a.revision_id=s.latest_revision WHERE s.partner_id IN (" +
                ids.map(() => "?").join(",") +
                ") ORDER BY s.created_at DESC LIMIT 200",
            )
            .bind(...ids)
            .all<any>()
        ).results
      : [];
    return respond({
      partners,
      submissions: all,
      admin: user.admin,
      connection: {
        configured: !!env.SCISURE_API_TOKEN,
        tenant: env.SCISURE_BASE_URL || "https://sandbox.elabjournal.com",
        publicationEnabled: env.SCISURE_PUBLICATION_ENABLED === "true",
      },
    });
  } catch (e) {
    return fail(e);
  }
}
