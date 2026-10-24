import { useEffect, useRef, useState } from "react";

/** A click that only counts the second time: the first arms it for `ms`, a second click within that runs
 * `fn`. One stray click never sends an email. The arming belongs to `key` (the draft's version): when the
 * key changes, the next click arms again instead of running `fn` on something the user has not seen.
 * `onArmed` hears each arm and disarm (the page holds its poll meanwhile). */
export function useConfirm(fn: () => void, key = "", onArmed?: (armed: boolean) => void, ms = 5000):
  { armed: boolean; click: () => void; disarm: () => void } {
  const [armedFor, setArmedFor] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const armed = armedFor === key;

  useEffect(() => () => clearTimeout(timer.current), []);

  function click() {
    clearTimeout(timer.current);
    if (armed) {
      setArmedFor(null);
      onArmed?.(false);
      fn();
      return;
    }
    setArmedFor(key);
    onArmed?.(true);
    timer.current = setTimeout(() => { setArmedFor(null); onArmed?.(false); }, ms);
  }

  /** Drop a pending arm (another button on the same card was armed instead). */
  function disarm() {
    if (!armed) return;
    clearTimeout(timer.current);
    setArmedFor(null);
    onArmed?.(false);
  }

  return { armed, click, disarm };
}
