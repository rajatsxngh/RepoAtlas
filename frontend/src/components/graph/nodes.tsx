"use client";

import { Handle, Position, type NodeProps } from "@xyflow/react";
import { ChevronRight, ChevronsDownUp, Ellipsis } from "lucide-react";
import { memo, type KeyboardEvent } from "react";
import { Spinner } from "@/components/ui/Spinner";
import { useCanvasActions } from "./actions";
import type { ContainerFlowNode, EntityFlowNode, MoreFlowNode } from "./flow";
import { KIND_LABELS, KindIcon } from "./KindIcon";

/** Invisible anchors: edges float between node borders, but React Flow wants handles. */
function Anchors() {
  return (
    <>
      <Handle type="target" position={Position.Top} isConnectable={false} className="ra-anchor" />
      <Handle
        type="source"
        position={Position.Bottom}
        isConnectable={false}
        className="ra-anchor"
      />
    </>
  );
}

function stop(event: { stopPropagation: () => void }) {
  event.stopPropagation();
}

export const EntityNode = memo(function EntityNode({
  id,
  data,
  selected,
}: NodeProps<EntityFlowNode>) {
  const actions = useCanvasActions();
  const { node, label, meta, expandable, loading } = data;
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Enter") actions.select(id);
    else if ((event.key === " " || event.key === "ArrowRight") && expandable) {
      event.preventDefault();
      actions.toggle(id);
    } else if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) {
      event.preventDefault();
      const box = event.currentTarget.getBoundingClientRect();
      actions.openMenu(id, { x: box.left + 12, y: box.bottom + 4 });
    }
  };
  return (
    <div
      className={`ra-node group flex h-full w-full items-center gap-2.5 rounded-lg border bg-surface pr-1.5 pl-2.5 shadow-card transition-[border-color,box-shadow] ${
        selected ? "border-focus ring-2 ring-focus/30" : "border-line hover:border-line-strong"
      } ${node.kind === "test" ? "ra-node-test" : ""}`}
      title={node.kind === "dir" || node.kind === "file" ? node.path : node.qualified_name}
      onKeyDown={onKeyDown}
      onDoubleClick={() => expandable && actions.toggle(id)}
      onContextMenu={(event) => {
        event.preventDefault();
        actions.openMenu(id, { x: event.clientX, y: event.clientY });
      }}
    >
      <Anchors />
      <span
        className="ra-kind-tile flex h-6 w-6 shrink-0 items-center justify-center rounded-md"
        data-kind={node.kind}
      >
        <KindIcon kind={node.kind} className="h-3.5 w-3.5" />
      </span>
      <span className="min-w-0 flex-1 leading-tight">
        <span className="block truncate text-[13px] font-medium text-fg">{label}</span>
        <span className="block truncate text-[11px] text-fg-faint">
          <span className="sr-only">{KIND_LABELS[node.kind]}, </span>
          {meta}
        </span>
      </span>
      {expandable ? (
        <button
          type="button"
          className="nodrag flex h-6 w-6 shrink-0 cursor-pointer items-center justify-center rounded-md text-fg-faint hover:bg-surface-2 hover:text-fg"
          aria-label={`Open ${label}`}
          title="Open"
          onClick={(event) => {
            stop(event);
            actions.toggle(id);
          }}
          onDoubleClick={stop}
        >
          {loading ? (
            <Spinner className="h-3.5 w-3.5" />
          ) : (
            <ChevronRight className="h-4 w-4" aria-hidden="true" />
          )}
        </button>
      ) : null}
    </div>
  );
});

export const ContainerNode = memo(function ContainerNode({
  id,
  data,
  selected,
}: NodeProps<ContainerFlowNode>) {
  const actions = useCanvasActions();
  const { node, label, shown, total, loading } = data;
  return (
    <div
      className={`ra-container h-full w-full rounded-xl border ${selected ? "border-focus ring-2 ring-focus/25" : "border-line-strong"}`}
      data-kind={node.kind}
      onContextMenu={(event) => {
        event.preventDefault();
        actions.openMenu(id, { x: event.clientX, y: event.clientY });
      }}
    >
      <Anchors />
      <div
        className="ra-container-header flex h-10 cursor-pointer items-center gap-2 rounded-t-xl border-b border-line px-3"
        onDoubleClick={() => actions.toggle(id)}
        title={node.kind === "dir" || node.kind === "file" ? node.path : node.qualified_name}
      >
        <KindIcon kind={node.kind} open className="h-4 w-4 shrink-0" />
        <span className="min-w-0 truncate text-[13px] font-semibold text-fg">{label}</span>
        <span className="shrink-0 rounded-full bg-surface-3 px-1.5 text-[11px] text-fg-muted tabular-nums">
          {shown === total ? total : `${shown} of ${total}`}
        </span>
        <span className="flex-1" />
        {loading ? <Spinner className="h-3.5 w-3.5 text-fg-faint" /> : null}
        <button
          type="button"
          className="nodrag flex h-6 w-6 shrink-0 cursor-pointer items-center justify-center rounded-md text-fg-faint hover:bg-surface-2 hover:text-fg"
          aria-label={`Close ${label}`}
          title="Close"
          onClick={(event) => {
            stop(event);
            actions.toggle(id);
          }}
          onDoubleClick={stop}
        >
          <ChevronsDownUp className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
    </div>
  );
});

export const MoreNode = memo(function MoreNode({ data }: NodeProps<MoreFlowNode>) {
  const actions = useCanvasActions();
  return (
    <div className="flex h-full w-full items-center justify-center">
      <Anchors />
      <button
        type="button"
        className="nodrag flex h-full w-full cursor-pointer items-center justify-center gap-1.5 rounded-full border border-dashed border-line-strong bg-surface-2 text-[12px] font-medium text-fg-muted transition-colors hover:border-focus hover:text-fg"
        onClick={(event) => {
          stop(event);
          actions.showMore(data.container);
        }}
        aria-label={`Show ${data.hidden} more`}
      >
        <Ellipsis className="h-4 w-4" aria-hidden="true" />+{data.hidden.toLocaleString("en-US")}{" "}
        more
      </button>
    </div>
  );
});

export const NODE_TYPES = { entity: EntityNode, container: ContainerNode, more: MoreNode };
