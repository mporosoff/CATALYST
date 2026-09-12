import { getChatGPTUser, chatGPTSignInPath } from "./chatgpt-auth";
import Workspace from "./workspace";
export const dynamic = "force-dynamic";
export default async function Page() {
  const user = await getChatGPTUser();
  if (!user)
    return (
      <main className="signin">
        <div className="wordmark">
          CATALYST<span>DATA WORKSPACE</span>
        </div>
        <h1>Your research, with its full history.</h1>
        <p>
          Upload catalysis data, review standardized results, and publish
          approved revisions to SciSure.
        </p>
        <a
          className="signin-button"
          href={chatGPTSignInPath("/")}
          target="_top"
        >
          Sign in with ChatGPT
        </a>
        <p className="muted">
          Private access. Your SciSure token stays on the server.
        </p>
      </main>
    );
  return <Workspace name={user.displayName} />;
}
