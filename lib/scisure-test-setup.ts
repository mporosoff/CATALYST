import { Problem } from "./problem";
import { runStep } from "./ledger";
import { remoteId, SciSureError, SciSureClient } from "./scisure-client";
export async function createTestStructure(
  c: SciSureClient,
  store: D1Database,
  expectedGroup: unknown,
) {
  const groupId = remoteId(expectedGroup),
    ledger = "sandbox-setup/" + groupId;
  const before = async () => {
    const group = await c.request("/api/v1/groups/active");
    if (group.groupID !== groupId)
      throw new Problem(
        409,
        "The active SciSure group changed. Check the connection again.",
      );
  };
  await before();
  async function ensure(
    kind: string,
    path: string,
    name: string,
    key: string,
    match: (x: any) => boolean,
    body: any,
  ) {
    const find = async () => {
      const rows = (await c.list(path)).filter(
        (x) => x.name === name && match(x),
      );
      if (rows.length > 1)
        throw new Problem(
          409,
          "Several test records have the same name. Choose an existing destination manually.",
        );
      return rows.length ? remoteId(rows[0][key]) : null;
    };
    return runStep(store, ledger, {
      name: kind,
      before,
      find,
      write: async () => {
        const response = await c.request(path, {
          method: "POST",
          json: { ...body, name },
        });
        const result =
          typeof response === "number" ? response : response?.[key];
        if (result) return remoteId(result);
        const found = await find();
        if (!found)
          throw new SciSureError(
            502,
            "The test record’s creation could not be confirmed.",
            true,
          );
        return found;
      },
    });
  }
  const projectId = await ensure(
    "project",
    "/api/v1/projects",
    "CATALYST Testing",
    "projectID",
    (x) => x.groupID === groupId && x.active !== false,
    {
      description: "Dedicated CATALYST connector test project.",
      // SciSure validates notes even though its schema omits required flags.
      notes: "Sandbox records for testing CATALYST uploads and publication.",
    },
  );
  const studyId = await ensure(
    "study/" + projectId,
    "/api/v1/studies",
    "Connector Tests",
    "studyID",
    (x) => x.groupID === groupId && x.projectID === projectId && !x.deleted,
    {
      projectID: projectId,
      description: "CATALYST upload and publication tests.",
    },
  );
  const experimentId = await ensure(
    "experiment/" + studyId,
    "/api/v1/experiments",
    "GC Upload Test",
    "experimentID",
    (x) =>
      x.groupID === groupId &&
      x.studyID === studyId &&
      !x.deleted &&
      !x.template,
    { studyID: studyId, status: "PENDING", autoCollaborate: false },
  );
  return { projectId, studyId, experimentId };
}
