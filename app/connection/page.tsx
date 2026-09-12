import { requireChatGPTUser } from "@/app/chatgpt-auth";
import Connection from "./settings";
export default async function Page() {
  await requireChatGPTUser("/connection");
  return <Connection />;
}
