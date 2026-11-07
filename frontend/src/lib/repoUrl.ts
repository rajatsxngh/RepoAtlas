/**
 * Client-side check of a repository URL, mirroring the API's rules (PRD ING-1): only
 * `https://github.com/<owner>/<repo>` (optionally `.git`), with no other host, scheme, port,
 * credentials, query, or fragment. The API checks again; this only gives early hints.
 */

const OWNER_RE = /^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$/;
const REPO_RE = /^[A-Za-z0-9._-]{1,100}$/;
const URL_RE = /^([A-Za-z][A-Za-z0-9+.-]*):\/\/([^/?#]*)([^?#]*)(\?[^#]*)?(#.*)?$/;

export type RepoUrlCheck =
  | { ok: true; url: string; owner: string; name: string }
  | { ok: false; message: string; suggestion?: string };

function shorten(text: string): string {
  return text.length > 40 ? `${text.slice(0, 37)}...` : text;
}

function fromSegments(owner: string, rawName: string): RepoUrlCheck {
  const name = rawName.endsWith(".git") ? rawName.slice(0, -4) : rawName;
  if (!OWNER_RE.test(owner)) {
    return { ok: false, message: `'${shorten(owner)}' is not a valid GitHub owner name.` };
  }
  if (name === "." || name === ".." || !REPO_RE.test(name)) {
    return { ok: false, message: `'${shorten(name)}' is not a valid repository name.` };
  }
  return { ok: true, url: `https://github.com/${owner}/${name}`, owner, name };
}

/** `owner/repo` or `github.com/owner/repo` typed without the scheme. */
function shorthand(text: string): string | undefined {
  const match = /^(?:(?:www\.)?github\.com\/)?([^/\s?#:@]+)\/([^/\s?#:@]+?)\/?$/i.exec(text);
  if (!match) return undefined;
  const checked = fromSegments(match[1]!, match[2]!);
  return checked.ok ? checked.url : undefined;
}

export function checkRepoUrl(input: string): RepoUrlCheck {
  const text = input.trim();
  if (!text) return { ok: false, message: "Paste the URL of a public GitHub repository." };
  const parts = URL_RE.exec(text);
  if (!parts) {
    const suggestion = shorthand(text);
    return suggestion
      ? { ok: false, message: `Add https:// — did you mean ${suggestion}?`, suggestion }
      : { ok: false, message: "Expected https://github.com/<owner>/<repo>." };
  }
  const [, scheme, authority, path, query, fragment] = parts as unknown as [
    string,
    string,
    string,
    string,
    string | undefined,
    string | undefined,
  ];
  if (scheme.toLowerCase() !== "https") {
    const suggestion = shorthand(`${authority}${path}`.replace(/^www\./i, ""));
    return {
      ok: false,
      message: "Use an https:// URL, for example https://github.com/pallets/flask.",
      suggestion,
    };
  }
  if (authority.includes("@")) {
    return { ok: false, message: "URLs that contain credentials are not accepted." };
  }
  if (authority.toLowerCase() !== "github.com") {
    const suggestion =
      authority.toLowerCase() === "www.github.com" ? shorthand(path.slice(1)) : undefined;
    return {
      ok: false,
      message: "Only https://github.com/<owner>/<repo> URLs are supported.",
      suggestion,
    };
  }
  if (query !== undefined || fragment !== undefined) {
    const suggestion = shorthand(path.replace(/^\/+/, ""));
    return { ok: false, message: "Remove the query string or fragment from the URL.", suggestion };
  }
  const segments = path.replace(/^\/+|\/+$/g, "").split("/");
  if (segments.length !== 2) {
    const suggestion =
      segments.length > 2 ? fromSegments(segments[0]!, segments[1]!) : { ok: false as const };
    return {
      ok: false,
      message: "Expected https://github.com/<owner>/<repo>.",
      suggestion: suggestion.ok ? suggestion.url : undefined,
    };
  }
  return fromSegments(segments[0]!, segments[1]!);
}
