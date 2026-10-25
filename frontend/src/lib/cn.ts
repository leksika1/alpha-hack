// Склейка классов Tailwind с условиями: cn('a', ok && 'b').
export function cn(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(' ');
}
