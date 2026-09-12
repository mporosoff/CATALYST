import {
  sqliteTable,
  text,
  integer,
  index,
  primaryKey,
  uniqueIndex,
} from "drizzle-orm/sqlite-core";
export const settings = sqliteTable("settings", {
  key: text("key").primaryKey(),
  value: text("value").notNull(),
});
export const partners = sqliteTable("partners", {
  id: text("id").primaryKey(),
  name: text("name").notNull(),
  createdAt: text("created_at").notNull(),
});
export const members = sqliteTable(
  "members",
  {
    userId: text("user_id").notNull(),
    partnerId: text("partner_id").notNull(),
    role: text("role").notNull(),
  },
  (t) => [primaryKey({ columns: [t.userId, t.partnerId] })],
);
export const submissions = sqliteTable(
  "submissions",
  {
    id: text("id").primaryKey(),
    partnerId: text("partner_id").notNull(),
    modality: text("modality").notNull(),
    title: text("title").notNull(),
    createdBy: text("created_by").notNull(),
    createdAt: text("created_at").notNull(),
    latestRevision: text("latest_revision"),
  },
  (t) => [index("submissions_partner_date").on(t.partnerId, t.createdAt)],
);
export const artifacts = sqliteTable(
  "artifacts",
  {
    id: text("id").primaryKey(),
    submissionId: text("submission_id").notNull(),
    name: text("name").notNull(),
    sha256: text("sha256").notNull(),
    size: integer("size").notNull(),
    objectKey: text("object_key").notNull(),
    format: text("format").notNull(),
  },
  (t) => [index("artifacts_submission").on(t.submissionId)],
);
export const profiles = sqliteTable(
  "profiles",
  {
    id: text("id").primaryKey(),
    sourceVersion: text("source_version").notNull().default("legacy"),
    partnerId: text("partner_id").notNull(),
    modality: text("modality").notNull(),
    format: text("format").notNull(),
    version: integer("version").notNull(),
    name: text("name").notNull(),
    content: text("content").notNull(),
    digest: text("digest").notNull(),
    createdBy: text("created_by").notNull(),
    createdAt: text("created_at").notNull(),
  },
  (t) => [
    uniqueIndex("profile_versions").on(
      t.partnerId,
      t.modality,
      t.format,
      t.sourceVersion,
      t.name,
      t.version,
    ),
  ],
);
export const revisions = sqliteTable(
  "revisions",
  {
    id: text("id").primaryKey(),
    submissionId: text("submission_id").notNull(),
    parentId: text("parent_id"),
    number: integer("number").notNull(),
    payloadKey: text("payload_key").notNull(),
    digest: text("digest").notNull(),
    context: text("context").notNull(),
    createdBy: text("created_by").notNull(),
    createdAt: text("created_at").notNull(),
  },
  (t) => [
    uniqueIndex("submission_revision_number").on(t.submissionId, t.number),
  ],
);
export const approvals = sqliteTable("approvals", {
  revisionId: text("revision_id").primaryKey(),
  digest: text("digest").notNull(),
  reviewer: text("reviewer").notNull(),
  note: text("note").notNull(),
  createdAt: text("created_at").notNull(),
});
export const publications = sqliteTable(
  "publications",
  {
    id: text("id").primaryKey(),
    revisionId: text("revision_id").notNull(),
    tenant: text("tenant").notNull(),
    destination: text("destination").notNull(),
    state: text("state").notNull(),
    createdAt: text("created_at").notNull(),
    updatedAt: text("updated_at").notNull(),
  },
  (t) => [
    uniqueIndex("publication_unique").on(t.revisionId, t.tenant, t.destination),
  ],
);
export const operations = sqliteTable(
  "operations",
  {
    id: text("id").primaryKey(),
    publicationId: text("publication_id").notNull(),
    name: text("name").notNull(),
    state: text("state").notNull(),
    remoteId: text("remote_id"),
    evidence: text("evidence"),
    updatedAt: text("updated_at").notNull(),
  },
  (t) => [uniqueIndex("operation_unique").on(t.publicationId, t.name)],
);
export const audit = sqliteTable(
  "audit",
  {
    id: text("id").primaryKey(),
    userId: text("user_id").notNull(),
    submissionId: text("submission_id"),
    action: text("action").notNull(),
    detail: text("detail").notNull(),
    createdAt: text("created_at").notNull(),
  },
  (t) => [index("audit_submission").on(t.submissionId, t.createdAt)],
);
