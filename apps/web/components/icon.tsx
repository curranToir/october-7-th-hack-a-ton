import type { CSSProperties } from "react";

const paths = {
  brain:
    "M12 5a3 3 0 0 0-5-2 3 3 0 0 0-3 4 4 4 0 0 0-1 7 4 4 0 0 0 4 6 3 3 0 0 0 5-1V5Zm0 0a3 3 0 0 1 5-2 3 3 0 0 1 3 4 4 4 0 0 1 1 7 4 4 0 0 1-4 6 3 3 0 0 1-5-1M7 3v4l2 2m-5-2 2 2m-3 5h4l2-2m-2 8v-4m10-13v4l-2 2m5-2-2 2m3 5h-4l-2-2m2 8v-4",
  tasks:
    "M9 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-8M8 11l4 4L22 3",
  plus: "M12 5v14M5 12h14",
  search: "M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
  chat: "M21 11a8 8 0 0 1-8 8H7l-5 3V5a3 3 0 0 1 3-3h13a3 3 0 0 1 3 3v6Z",
  settings:
    "M9 3h6l1 3 3 1 2 5-2 3-1 4-5 2-3-2-4-1-2-5 2-3 1-4 2-1Zm6 9a3 3 0 1 0-6 0 3 3 0 0 0 6 0",
  help: "M9 9a3 3 0 0 1 6 0c0 2-3 2-3 4m0 4h.01M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0",
  chevron: "m9 5 7 7-7 7",
  down: "m6 9 6 6 6-6",
  arrow: "M5 12h14m-6-6 6 6-6 6",
  send: "M12 19V5m-6 6 6-6 6 6",
  check: "m5 12 4 4L19 6",
  close: "m6 6 12 12M6 18 18 6",
  info: "M12 11v6m0-10h.01M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0",
  link: "M14 3h7v7m0-7L10 14M10 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-5",
  more: "M5 12h.01M12 12h.01M19 12h.01",
  panel:
    "M8 3v18M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2",
  clock: "M12 6v6l4 2M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0",
  shield: "m12 2 9 4v6c0 5-9 10-9 10S3 17 3 12V6l9-4Zm-4 9 3 3 5-5",
  sun: "M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0",
  moon: "M21 13A9 9 0 0 1 11 3a9 9 0 1 0 10 10Z",
  monitor:
    "M9 21h6m-3-4v4M4 3h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2",
  trash: "M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7",
  edit: "m16 3 5 5-12 12-6 1 1-6L16 3Zm-2 2 5 5",
  download: "M12 3v12m-5-5 5 5 5-5M3 16v5h18v-5",
  logout: "M9 3H3v18h6m5-14 5 5-5 5M8 12h13",
} as const;
export type IconName = keyof typeof paths;
export function Icon({
  name,
  size = 20,
  style,
  className = "",
}: {
  name: IconName;
  size?: number;
  style?: CSSProperties;
  className?: string;
}) {
  return (
    <svg
      aria-hidden="true"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={name === "more" ? 3.5 : 1.65}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      style={style}
    >
      <path d={paths[name]} />
    </svg>
  );
}
