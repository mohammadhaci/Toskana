// Shared test setup: clean up React Testing Library between tests.
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});
