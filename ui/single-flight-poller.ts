/**
 * Single-flight poller for plugin widgets (attention/overview badges).
 *
 * Guarantees, regardless of how slow the backend is:
 * - at most ONE request in flight: a tick that fires while the previous load
 *   is still pending is skipped, so slow endpoints cannot pile up requests;
 * - stop() aborts the pending request (AbortController) so unmounted widgets
 *   release their server-side slot instead of leaving it to run to completion;
 * - results/errors that arrive after stop() or after an abort are dropped.
 *
 * Framework-free on purpose: React effects own start/stop, tests drive tick().
 */
export type SingleFlightPollerOptions<T> = {
  intervalMs: number;
  load: (signal: AbortSignal) => Promise<T>;
  onResult: (value: T) => void;
  onError?: (error: unknown) => void;
  /** Injectable for tests; default window/global setInterval. */
  schedule?: (callback: () => void, ms: number) => unknown;
  /** Injectable for tests; default clearInterval. */
  cancel?: (handle: unknown) => void;
};

export type SingleFlightPoller = {
  start: () => void;
  stop: () => void;
  /** Run one poll now unless one is already in flight. Resolves when it settles. */
  tick: () => Promise<void>;
  readonly inFlight: boolean;
};

function isAbortError(error: unknown): boolean {
  return typeof error === 'object' && error !== null && (error as { name?: unknown }).name === 'AbortError';
}

export function createSingleFlightPoller<T>(options: SingleFlightPollerOptions<T>): SingleFlightPoller {
  const schedule = options.schedule ?? ((callback: () => void, ms: number) => setInterval(callback, ms));
  const cancel = options.cancel ?? ((handle: unknown) => clearInterval(handle as ReturnType<typeof setInterval>));
  let controller: AbortController | null = null;
  let timer: unknown = null;
  let stopped = false;

  const tick = async (): Promise<void> => {
    if (stopped || controller) return;
    const current = new AbortController();
    controller = current;
    try {
      const value = await options.load(current.signal);
      if (!stopped && !current.signal.aborted) options.onResult(value);
    } catch (error) {
      if (!stopped && !current.signal.aborted && !isAbortError(error)) options.onError?.(error);
    } finally {
      if (controller === current) controller = null;
    }
  };

  return {
    start() {
      if (timer !== null || stopped) return;
      void tick();
      timer = schedule(() => void tick(), options.intervalMs);
    },
    stop() {
      stopped = true;
      if (timer !== null) cancel(timer);
      timer = null;
      controller?.abort();
      controller = null;
    },
    tick,
    get inFlight() {
      return controller !== null;
    },
  };
}
