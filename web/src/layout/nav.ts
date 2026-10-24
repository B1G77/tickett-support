import type { ComponentType } from "react";
import { IconAuto, IconChart, IconDrafts, IconPulse } from "./icons";

export type NavItem = { path: string; label: string; short: string; Icon: ComponentType; admin?: boolean };

// `short` is the label in the phone's bottom bar
export const NAV: NavItem[] = [
  { path: "/", label: "Por responder", short: "Por resp.", Icon: IconDrafts },
  { path: "/painel", label: "Painel", short: "Painel", Icon: IconChart, admin: true },
  { path: "/estado", label: "Estado", short: "Estado", Icon: IconPulse, admin: true },
  { path: "/envio-automatico", label: "Envio automático", short: "Envio auto.", Icon: IconAuto, admin: true },
];

export function titleFor(pathname: string): string {
  return NAV.find((n) => n.path === pathname)?.label ?? "Apoio Tickett";
}
