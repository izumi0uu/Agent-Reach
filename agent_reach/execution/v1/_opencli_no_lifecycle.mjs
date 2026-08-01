import childProcess from "node:child_process";
import { syncBuiltinESMExports } from "node:module";
import path from "node:path";
import { pathToFileURL } from "node:url";

const entrypoint = process.argv[1];
if (typeof entrypoint !== "string" || !path.isAbsolute(entrypoint)) {
  throw new Error("OpenCLI entrypoint is not absolute");
}

const sourceRoot = path.dirname(entrypoint);
const expectedEntrypoint = path.join(sourceRoot, "main.js");
if (path.resolve(entrypoint) !== expectedEntrypoint) {
  throw new Error("OpenCLI entrypoint is incompatible");
}

const lifecycleUrl = pathToFileURL(
  path.join(sourceRoot, "browser", "daemon-lifecycle.js"),
).href;
const errorsUrl = pathToFileURL(path.join(sourceRoot, "errors.js")).href;
const [{ daemonLifecycleHooks }, { BrowserConnectError }] = await Promise.all([
  import(lifecycleUrl),
  import(errorsUrl),
]);

if (
  daemonLifecycleHooks === null ||
  typeof daemonLifecycleHooks !== "object" ||
  typeof BrowserConnectError !== "function"
) {
  throw new Error("OpenCLI lifecycle contract is incompatible");
}

const denyLifecycleMutation = () => {
  throw new BrowserConnectError(
    "OpenCLI daemon lifecycle is disabled for managed execution",
    "Start the reviewed daemon explicitly on the trusted Connector device.",
    "daemon-not-running",
  );
};

for (const name of [
  "exec",
  "execFile",
  "execFileSync",
  "execSync",
  "fork",
  "spawn",
  "spawnSync",
]) {
  Object.defineProperty(childProcess, name, {
    value: denyLifecycleMutation,
    configurable: false,
    enumerable: true,
    writable: false,
  });
}
syncBuiltinESMExports();

const nativeFetch = globalThis.fetch;
if (typeof nativeFetch !== "function") {
  throw new Error("OpenCLI fetch contract is incompatible");
}
const guardFetch = (delegate) => (input, init) => {
  const target = new URL(input instanceof Request ? input.url : String(input));
  const method = String(
    init?.method ?? (input instanceof Request ? input.method : "GET"),
  ).toUpperCase();
  if (
    target.origin === "http://127.0.0.1:19825" &&
    target.pathname === "/shutdown" &&
    method === "POST"
  ) {
    denyLifecycleMutation();
  }
  return delegate(input, init);
};

let guardedFetch = guardFetch(nativeFetch);
let fetchReplacementInstalled = false;
Object.defineProperty(globalThis, "fetch", {
  get: () => guardedFetch,
  set: (replacement) => {
    if (fetchReplacementInstalled || typeof replacement !== "function") {
      denyLifecycleMutation();
    }
    guardedFetch = guardFetch(replacement);
    fetchReplacementInstalled = true;
  },
  configurable: false,
  enumerable: true,
});

const nativeKill = process.kill.bind(process);
Object.defineProperty(process, "kill", {
  value: (pid, signal) => {
    if (signal !== 0) {
      denyLifecycleMutation();
    }
    return nativeKill(pid, signal);
  },
  configurable: false,
  enumerable: true,
  writable: false,
});

Object.defineProperties(daemonLifecycleHooks, {
  requestDaemonShutdown: {
    value: denyLifecycleMutation,
    configurable: false,
    enumerable: true,
    writable: false,
  },
  spawnDaemonProcess: {
    value: denyLifecycleMutation,
    configurable: false,
    enumerable: true,
    writable: false,
  },
  waitForDaemonStop: {
    value: denyLifecycleMutation,
    configurable: false,
    enumerable: true,
    writable: false,
  },
});
Object.freeze(daemonLifecycleHooks);
