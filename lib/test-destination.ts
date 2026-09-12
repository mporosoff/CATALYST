import "server-only";
import { db } from "./server";
import { client, saveDestination } from "./connector";
import { createTestStructure } from "./scisure-test-setup";

export async function createTestDestination(
  partner: string,
  expectedGroup: unknown,
) {
  const { experimentId } = await createTestStructure(
    client(),
    db(),
    expectedGroup,
  );
  return saveDestination(partner, experimentId);
}
