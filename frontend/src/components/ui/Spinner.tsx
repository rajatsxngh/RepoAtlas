import { LoaderCircle } from "lucide-react";

export function Spinner({ className = "h-4 w-4", label }: { className?: string; label?: string }) {
  return (
    <LoaderCircle
      className={`animate-spin ${className}`}
      aria-hidden={label ? undefined : true}
      aria-label={label}
      role={label ? "img" : undefined}
    />
  );
}
