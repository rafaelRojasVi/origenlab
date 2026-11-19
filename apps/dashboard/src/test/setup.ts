import { afterEach } from "vitest";
import { resetSegUi } from "../crm/segStore";
import { clearResourceCache } from "../crm/useResource";

// Pages remember their last answer per signed-in operator (`useResource`). Tests render many
// pages as the same invented operator, so each test starts with that memory empty — and so does
// «Seguimiento»'s screen memory (open forms, drafts: `segStore`).
afterEach(() => {
  clearResourceCache();
  resetSegUi();
});
