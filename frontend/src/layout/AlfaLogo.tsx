export function AlfaLogo({ size = 32 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="8" fill="#EF3124" />
      <path d="M16.9 7h-1.8L10 21.2h2.6l1.1-3.2h4.6l1.1 3.2H22L16.9 7zm-2.5 8.8 1.6-4.9 1.6 4.9h-3.2zM10 23h12v2H10z" fill="#fff" />
    </svg>
  );
}
