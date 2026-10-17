import assert from "node:assert/strict";
import test from "node:test";

import { createSingleFlightPoller } from "./ui/single-flight-poller.ts";

function deferred() {
  let resolve, reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function manualClock() {
  const callbacks = [];
  return {
    schedule: (callback) => { callbacks.push(callback); return callbacks.length; },
    cancel: () => { callbacks.length = 0; },
    fire: () => { for (const callback of [...callbacks]) callback(); },
    get active() { return callbacks.length; },
  };
}

test("a tick while a load is pending is skipped (no request pile-up)", async () => {
  const pending = deferred();
  let loads = 0;
  const clock = manualClock();
  const poller = createSingleFlightPoller({
    intervalMs: 30_000,
    load: () => { loads += 1; return pending.promise; },
    onResult: () => {},
    schedule: clock.schedule,
    cancel: clock.cancel,
  });

  poller.start();
  clock.fire();
  clock.fire();
  clock.fire();
  assert.equal(loads, 1);
  assert.equal(poller.inFlight, true);

  pending.resolve(1);
  await pending.promise;
  await Promise.resolve();
  assert.equal(poller.inFlight, false);

  clock.fire();
  assert.equal(loads, 2);
  poller.stop();
});

test("results are delivered in order and errors go to onError", async () => {
  const results = [];
  const errors = [];
  let n = 0;
  const poller = createSingleFlightPoller({
    intervalMs: 1,
    load: async () => { n += 1; if (n === 2) throw new Error("boom"); return n; },
    onResult: (value) => results.push(value),
    onError: (error) => errors.push(error.message),
    schedule: () => 1,
    cancel: () => {},
  });

  await poller.tick();
  await poller.tick();
  await poller.tick();

  assert.deepEqual(results, [1, 3]);
  assert.deepEqual(errors, ["boom"]);
  poller.stop();
});

test("stop() aborts the in-flight request and drops its late result", async () => {
  const pending = deferred();
  let signal;
  const results = [];
  const errors = [];
  const clock = manualClock();
  const poller = createSingleFlightPoller({
    intervalMs: 30_000,
    load: (s) => { signal = s; return pending.promise; },
    onResult: (value) => results.push(value),
    onError: (error) => errors.push(error),
    schedule: clock.schedule,
    cancel: clock.cancel,
  });

  poller.start();
  poller.stop();

  assert.equal(signal.aborted, true);
  assert.equal(clock.active, 0);
  pending.resolve("late");
  await pending.promise;
  await Promise.resolve();
  assert.deepEqual(results, []);
  assert.deepEqual(errors, []);
});

test("AbortError from fetch is not reported as an error", async () => {
  const errors = [];
  const poller = createSingleFlightPoller({
    intervalMs: 1,
    load: (signal) => new Promise((_, reject) => {
      signal.addEventListener("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" })));
    }),
    onResult: () => {},
    onError: (error) => errors.push(error),
    schedule: () => 1,
    cancel: () => {},
  });

  const running = poller.tick();
  poller.stop();
  await running;

  assert.deepEqual(errors, []);
});

test("start() is idempotent and a stopped poller never restarts", async () => {
  let loads = 0;
  const clock = manualClock();
  const poller = createSingleFlightPoller({
    intervalMs: 30_000,
    load: async () => { loads += 1; },
    onResult: () => {},
    schedule: clock.schedule,
    cancel: clock.cancel,
  });

  poller.start();
  poller.start();
  assert.equal(clock.active, 1);
  await Promise.resolve();

  poller.stop();
  poller.start();
  await poller.tick();
  assert.equal(loads, 1);
  assert.equal(clock.active, 0);
});
