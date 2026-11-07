/** Prism language for a file path (the languages RepoAtlas parses). */

export type CodeLanguage = "python" | "typescript" | "tsx" | "javascript" | "jsx" | "text";

export function languageOf(path: string): CodeLanguage {
  const dot = path.lastIndexOf(".");
  const extension = dot < 0 ? "" : path.slice(dot + 1).toLowerCase();
  switch (extension) {
    case "py":
    case "pyi":
      return "python";
    case "ts":
    case "mts":
    case "cts":
      return "typescript";
    case "tsx":
      return "tsx";
    case "js":
    case "mjs":
    case "cjs":
      return "javascript";
    case "jsx":
      return "jsx";
    default:
      return "text";
  }
}
