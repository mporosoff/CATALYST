// Numeric tokens remain decimal text so uploaded precision is never rounded by JSON.parse.
export function parseScientificJSON(source: string): unknown {
  let pos = 0,
    nodes = 0;
  const fail = () => {
    throw new Error("Invalid, duplicate-key, or excessively nested JSON.");
  };
  const ws = () => {
    while (/[\t\r\n ]/.test(source[pos] || "!")) pos++;
  };
  function string(): string {
    const start = pos++;
    while (pos < source.length) {
      if (source[pos] === "\\") {
        pos += 2;
        continue;
      }
      if (source[pos++] === '"') {
        try {
          return JSON.parse(source.slice(start, pos));
        } catch {
          fail();
        }
      }
    }
    return fail();
  }
  function value(depth = 0): unknown {
    ws();
    if (depth > 8 || ++nodes > 60000) fail();
    const c = source[pos];
    if (c === '"') return string();
    if (c === "[") {
      pos++;
      ws();
      const result: unknown[] = [];
      if (source[pos] === "]") {
        pos++;
        return result;
      }
      for (;;) {
        result.push(value(depth + 1));
        ws();
        if (source[pos] === "]") {
          pos++;
          return result;
        }
        if (source[pos++] !== ",") fail();
      }
    }
    if (c === "{") {
      pos++;
      ws();
      const result: Record<string, unknown> = Object.create(null);
      if (source[pos] === "}") {
        pos++;
        return result;
      }
      for (;;) {
        ws();
        if (source[pos] !== '"') fail();
        const key = string();
        if (Object.hasOwn(result, key)) fail();
        ws();
        if (source[pos++] !== ":") fail();
        result[key] = value(depth + 1);
        ws();
        if (source[pos] === "}") {
          pos++;
          return result;
        }
        if (source[pos++] !== ",") fail();
      }
    }
    for (const [literal, parsed] of [
      ["true", true],
      ["false", false],
      ["null", null],
    ] as const)
      if (source.startsWith(literal, pos)) {
        pos += literal.length;
        return parsed;
      }
    const match = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(
      source.slice(pos),
    );
    if (match) {
      pos += match[0].length;
      return match[0];
    }
    return fail();
  }
  const result = value();
  ws();
  if (pos !== source.length) fail();
  return result;
}
