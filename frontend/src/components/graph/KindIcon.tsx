import {
  Box,
  FileCode2,
  FlaskConical,
  Folder,
  FolderOpen,
  Globe,
  Parentheses,
  SquareFunction,
  type LucideIcon,
} from "lucide-react";
import type { NodeKind } from "@/lib/api/types";

const ICONS: Record<NodeKind, LucideIcon> = {
  dir: Folder,
  file: FileCode2,
  class: Box,
  function: SquareFunction,
  method: Parentheses,
  route: Globe,
  test: FlaskConical,
};

export const KIND_LABELS: Record<NodeKind, string> = {
  dir: "Folder",
  file: "File",
  class: "Class",
  function: "Function",
  method: "Method",
  route: "Route",
  test: "Test",
};

/** Text color utility for each kind's accent. */
export const KIND_TEXT: Record<NodeKind, string> = {
  dir: "text-kind-dir",
  file: "text-kind-file",
  class: "text-kind-class",
  function: "text-kind-function",
  method: "text-kind-method",
  route: "text-kind-route",
  test: "text-kind-test",
};

export function KindIcon({
  kind,
  open = false,
  className = "h-4 w-4",
}: {
  kind: NodeKind;
  open?: boolean;
  className?: string;
}) {
  const Icon = kind === "dir" && open ? FolderOpen : ICONS[kind];
  return <Icon className={`${KIND_TEXT[kind]} ${className}`} aria-hidden="true" strokeWidth={2} />;
}
