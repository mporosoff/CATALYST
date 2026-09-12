import {
  actor,
  db,
  checkOrigin,
  jsonBody,
  uid,
  now,
  respond,
  fail,
  Problem,
  event,
} from "@/lib/server";
export async function GET() {
  try {
    const user = await actor();
    if (!user.admin) throw new Problem(403, "Owner access is required.");
    return respond({
      partners: (
        await db().prepare("SELECT * FROM partners ORDER BY name").all()
      ).results,
      members: (
        await db().prepare("SELECT user_id,partner_id,role FROM members").all()
      ).results,
    });
  } catch (e) {
    return fail(e);
  }
}
export async function POST(request: Request) {
  try {
    checkOrigin(request);
    const user = await actor();
    if (!user.admin) throw new Problem(403, "Owner access is required.");
    const body = await jsonBody(request);
    if (body.action === "partner") {
      if (
        typeof body.name !== "string" ||
        !body.name.trim() ||
        body.name.length > 120
      )
        throw new Problem(400, "Enter a partner name.");
      const id = uid();
      await db()
        .prepare("INSERT INTO partners(id,name,created_at) VALUES(?,?,?)")
        .bind(id, body.name.trim(), now())
        .run();
      await event(user, "partner_created", null, {
        id,
        name: body.name.trim(),
      });
      return respond({ id }, 201);
    }
    if (body.action === "member") {
      if (
        typeof body.userId !== "string" ||
        !body.userId.trim() ||
        body.userId.length > 200 ||
        !["viewer", "contributor", "reviewer", "remove"].includes(body.role)
      )
        throw new Problem(
          400,
          "Enter the verified account ID and a supported role.",
        );
      if (
        !(await db()
          .prepare("SELECT id FROM partners WHERE id=?")
          .bind(String(body.partner))
          .first())
      )
        throw new Problem(400, "Partner not found.");
      if (body.role === "remove")
        await db()
          .prepare("DELETE FROM members WHERE user_id=? AND partner_id=?")
          .bind(body.userId.trim(), body.partner)
          .run();
      else
        await db()
          .prepare(
            "INSERT INTO members(user_id,partner_id,role) VALUES(?,?,?) ON CONFLICT(user_id,partner_id) DO UPDATE SET role=excluded.role",
          )
          .bind(body.userId.trim(), body.partner, body.role)
          .run();
      await event(user, "member_access_changed", null, {
        userId: body.userId.trim(),
        partner: body.partner,
        role: body.role,
      });
      return respond({ saved: true });
    }
    throw new Problem(400, "Unknown partner action.");
  } catch (e) {
    return fail(e);
  }
}
