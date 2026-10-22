// Line icons (24 px grid, currentColor), drawn inline so nothing loads from outside.
import type { ReactNode } from "react";

function Svg({ children }: { children: ReactNode }) {
  return (
    <svg className="icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor"
      strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {children}
    </svg>
  );
}

export const IconDrafts = () => (
  <Svg><path d="M4 5h16v11H8l-4 4z" /><path d="M8 9h8M8 12h5" /></Svg>
);
export const IconWaiting = () => (
  <Svg><circle cx="12" cy="12" r="8" /><path d="M12 8v4l3 2" /></Svg>
);
export const IconChart = () => (
  <Svg><path d="M4 20V4M4 20h16" /><path d="M8 16v-5M12 16V8M16 16v-3" /></Svg>
);
export const IconPulse = () => (
  <Svg><path d="M3 12h4l2-5 4 10 2-5h6" /></Svg>
);
export const IconAuto = () => (
  <Svg><path d="M4 12a8 8 0 0 1 14-5.3M20 12a8 8 0 0 1-14 5.3" /><path d="M18 3v4h-4M6 21v-4h4" /></Svg>
);
export const IconSun = () => (
  <Svg><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></Svg>
);
export const IconMoon = () => (
  <Svg><path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z" /></Svg>
);
export const IconLogout = () => (
  <Svg><path d="M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10" /></Svg>
);
export const IconInbox = () => (
  <Svg><path d="M4 13l2.5-7h11L20 13v6H4z" /><path d="M4 13h4.5l1 2h5l1-2H20" /></Svg>
);
export const IconReply = () => (
  <Svg><path d="M10 8 5 12l5 4" /><path d="M5 12h9a5 5 0 0 1 5 5v1" /></Svg>
);
export const IconPerson = () => (
  <Svg><circle cx="12" cy="8" r="4" /><path d="M4 20a8 8 0 0 1 16 0" /></Svg>
);
