// Starts the Command Tower in mock mode (in-browser sample data; no API, IdP or database needed).
// Cross-platform: works the same from PowerShell, cmd and bash, so there's no shell-specific env syntax.
import { spawn } from "node:child_process";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const nextBin = require.resolve("next/dist/bin/next");
const port = process.env.PORT || "3000";

const child = spawn(process.execPath, [nextBin, "dev", "-p", port], {
  stdio: "inherit",
  env: { ...process.env, NEXT_PUBLIC_DQ_MOCK: "1" },
});
child.on("exit", (code) => process.exit(code ?? 0));
