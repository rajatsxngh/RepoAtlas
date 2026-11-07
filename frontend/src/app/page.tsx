export default function HomePage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col justify-center gap-4 px-6">
      <h1 className="text-4xl font-semibold tracking-tight text-fg">RepoAtlas</h1>
      <p className="text-lg text-fg-muted">
        Turn a public GitHub repository into an interactive map of its code.
      </p>
    </main>
  );
}
