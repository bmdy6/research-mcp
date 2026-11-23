// Calls pinned upstream code; deliberately no CLI/daemon/browser entrypoint.
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";

let input = "";
for await (const chunk of process.stdin) {
  input += chunk;
  if (input.length > 64_000) throw new Error("Input budget exceeded");
}
const { query, engine, limit } = JSON.parse(input);
const engines = ["baidu", "sogou", "bing", "duckduckgo", "csdn"];
if (!engines.includes(engine) || typeof query !== "string" || !Number.isInteger(limit) || limit < 1 || limit > 40) {
  throw new Error("Invalid search arguments");
}
const { createOpenWebSearchRuntime } = await import(pathToFileURL(resolve(process.argv[2], "build/runtime/createRuntime.js")));
const runtime = createOpenWebSearchRuntime();
const result = await runtime.services.search.execute({ query, engines: [engine], limit, searchMode: "request" });
console.log(JSON.stringify({ status: "ok", data: result, error: null }));
