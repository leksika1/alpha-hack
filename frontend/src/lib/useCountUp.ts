import { useEffect, useRef, useState } from 'react';

// Плавная «прокрутка» числа к новому значению (счётчики на главной и над лентой).
export function useCountUp(target: number | undefined, duration = 700): number | undefined {
  const [value, setValue] = useState(target);
  const from = useRef(0);

  useEffect(() => {
    if (target === undefined) return;
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
      setValue(target);
      return;
    }
    const start = performance.now();
    const begin = from.current;
    let frame = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      setValue(Math.round(begin + (target - begin) * eased));
      if (t < 1) frame = requestAnimationFrame(tick);
      else from.current = target;
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [target, duration]);

  return value;
}
