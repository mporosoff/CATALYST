declare namespace Cloudflare {
  interface Env {
    DB?: D1Database;
    BUCKET?: R2Bucket;
    SCISURE_API_TOKEN?: string;
    SCISURE_BASE_URL?: string;
    SCISURE_PUBLICATION_ENABLED?: string;
    SITE_ORIGIN?: string;
    ALLOW_OWNER_BOOTSTRAP?: string;
  }
}
