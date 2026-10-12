// Icons from the approved mockups: 24×24 strokes, drawn with currentColor.

type Props = { size?: number };

function Stroke({ size = 18, width = 2, children }: Props & {
  width?: number;
  children: React.ReactNode;
}) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth={width} strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      {children}
    </svg>
  );
}

export const ChevronRight = (p: Props) => (
  <Stroke {...p}><path d="M9 6l6 6-6 6" /></Stroke>
);

export const ChevronDown = (p: Props) => (
  <Stroke {...p} width={2.2}><path d="M6 9l6 6 6-6" /></Stroke>
);

export const ArrowRight = (p: Props) => (
  <Stroke {...p}><path d="M5 12h14M13 6l6 6-6 6" /></Stroke>
);

export const Server = (p: Props) => (
  <Stroke {...p}>
    <rect x="3" y="4" width="18" height="7" rx="2" />
    <rect x="3" y="13" width="18" height="7" rx="2" />
    <path d="M7 7.5h.01M7 16.5h.01" />
  </Stroke>
);

export const Computer = (p: Props) => (
  <Stroke {...p}>
    <rect x="4" y="5" width="16" height="11" rx="2" />
    <path d="M2 19h20" />
  </Stroke>
);

export const Pin = (p: Props) => (
  <Stroke {...p}>
    <path d="M12 17v5" />
    <path d="M9 3h6l-1 6 3 3v2H7v-2l3-3z" />
  </Stroke>
);

export const Close = (p: Props) => (
  <Stroke {...p}><path d="M6 6l12 12M18 6L6 18" /></Stroke>
);

export const Plus = (p: Props) => (
  <Stroke {...p} width={2.4}><path d="M12 5v14M5 12h14" /></Stroke>
);

export const Copy = (p: Props) => (
  <Stroke {...p}>
    <rect x="9" y="9" width="11" height="11" rx="2" />
    <path d="M5 15V6a2 2 0 0 1 2-2h9" />
  </Stroke>
);

export const TerminalIcon = (p: Props) => (
  <Stroke {...p}>
    <rect x="3" y="4" width="18" height="16" rx="2" />
    <path d="M7 10l3 2-3 2M12 15h5" />
  </Stroke>
);

export const Check = (p: Props) => (
  <Stroke {...p} width={2.4}><path d="M5 12l5 5 9-10" /></Stroke>
);

export const Sun = (p: Props) => (
  <Stroke {...p}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
  </Stroke>
);

export const Moon = (p: Props) => (
  <Stroke {...p}><path d="M20.5 14.5A8.5 8.5 0 0 1 9.5 3.5a8.5 8.5 0 1 0 11 11z" /></Stroke>
);

export function Dots({ size = 16 }: Props) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor"
      aria-hidden="true">
      <circle cx="5" cy="12" r="1.8" />
      <circle cx="12" cy="12" r="1.8" />
      <circle cx="19" cy="12" r="1.8" />
    </svg>
  );
}

/** The tTerm mark. Its colours are fixed: it is the same in both themes. */
export function Logo({ size = 40 }: Props) {
  return (
    <svg width={size} height={size} viewBox="0 0 512 512" role="img"
      aria-label="tTerm">
      <rect width="512" height="512" rx="116" fill="#131417" />
      <path d="M118 168 L206 256 L118 344" fill="none" stroke="#E9A33F"
        strokeWidth="56" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M330 140 L330 316 C330 344 344 356 372 350" fill="none"
        stroke="#EDEFF3" strokeWidth="56" strokeLinecap="round" />
      <path d="M272 212 L392 212" fill="none" stroke="#EDEFF3"
        strokeWidth="56" strokeLinecap="round" />
    </svg>
  );
}
