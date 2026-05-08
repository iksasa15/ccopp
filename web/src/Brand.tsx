type PillKind = "ok" | "warn" | "danger";

export function BrandLogo({ size = 56 }: { size?: number }) {
  return (
    <svg
      className="brand-logo-svg"
      width={size}
      height={size}
      viewBox="0 0 64 64"
      aria-hidden
    >
      <defs>
        <linearGradient id="brandGrad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#6ee7b7" />
          <stop offset="55%" stopColor="#34d399" />
          <stop offset="100%" stopColor="#059669" />
        </linearGradient>
      </defs>
      <circle cx="32" cy="32" r="28" fill="none" stroke="url(#brandGrad)" strokeWidth="1" opacity="0.35" />
      <circle cx="32" cy="32" r="22" fill="none" stroke="url(#brandGrad)" strokeWidth="1" opacity="0.55" />
      <path
        fill="url(#brandGrad)"
        d="M32 6L56 20v24L32 58 8 44V20L32 6z"
        opacity="0.95"
      />
      <path
        fill="#05110e"
        d="M32 16l16 9.2v18.4L32 52 16 43.6V25.2L32 16z"
      />
      <text
        x="32"
        y="38"
        textAnchor="middle"
        fill="#6ee7b7"
        fontSize="13"
        fontFamily="JetBrains Mono, monospace"
        fontWeight="700"
      >
        {"</>"}
      </text>
    </svg>
  );
}

export function StatusPill({ kind, label }: { kind: PillKind; label: string }) {
  return <span className={`status-pill status-pill--${kind}`}>{label}</span>;
}
