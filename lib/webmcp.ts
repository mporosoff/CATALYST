type Registry = {
  registerTool: (tool: any, options: { signal: AbortSignal }) => unknown;
};
export function registerReviewTools(
  read: () => any,
  save: (context: any) => Promise<any>,
) {
  const registry = (document as Document & { modelContext?: Registry })
    .modelContext;
  if (!registry?.registerTool) return;
  const lifetime = new AbortController();
  const tools = [
    {
      name: "read_catalyst_review",
      description:
        "Read the current saved revision, validation findings, and approval status.",
      inputSchema: {
        type: "object",
        properties: {},
        additionalProperties: false,
      },
      annotations: { readOnlyHint: true, untrustedContentHint: true },
      execute(input: any) {
        if (!input || typeof input !== "object" || Object.keys(input).length)
          throw new Error("Pass an empty object.");
        const d = read();
        if (!d) throw new Error("The review is loading.");
        return {
          revision: d.revision.id,
          context: d.preview.context,
          issues: d.preview.validation.issues,
          approved: !!d.approval,
        };
      },
    },
    {
      name: "save_catalyst_context_revision",
      description:
        "Save confirmed scientific context as a new revision and update the visible review. This does not approve or publish.",
      inputSchema: {
        type: "object",
        properties: {
          context: { type: "object", additionalProperties: { type: "string" } },
        },
        required: ["context"],
        additionalProperties: false,
      },
      annotations: { readOnlyHint: false, untrustedContentHint: true },
      async execute(input: any) {
        if (
          !input ||
          Object.keys(input).some((k) => k !== "context") ||
          !input.context ||
          Array.isArray(input.context) ||
          typeof input.context !== "object" ||
          Object.values(input.context).some((x) => typeof x !== "string")
        )
          throw new Error("Provide a context object with text values.");
        return save(input.context);
      },
    },
  ];
  for (const tool of tools)
    try {
      Promise.resolve(
        registry.registerTool(tool, { signal: lifetime.signal }),
      ).catch(() => {});
    } catch {
      /* Experimental registry support is optional. */
    }
  return () => lifetime.abort();
}
