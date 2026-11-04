import { connectHost } from "./host.ts";
import { el, empty } from "./elements.ts";
import "./styles.css";

const root = document.getElementById("root");
if (root) {
  root.append(el("main", "main", empty("Opening Lighthouse…", "Connecting your evaluation workspace to Codex.")));
  void connectHost(root).catch((error: unknown) => {
    const message = error instanceof Error ? error.message : "The desktop host did not respond.";
    const warning = empty("Could not connect to Codex", message);
    warning.setAttribute("role", "alert");
    root.replaceChildren(el("main", "main", warning));
  });
}
