import { afterEach } from "vitest";
import { clearResourceCache } from "../crm/useResource";

// Pages remember their last answer per signed-in operator (`useResource`). Tests render many
// pages as the same invented operator, so each test starts with that memory empty.
afterEach(() => clearResourceCache());
