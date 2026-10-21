import Link from "next/link";

export default function NotFound() {
  return (
    <div className="page-content">
      <section className="error-state">
        <p className="eyebrow">404</p>
        <h1>This page isn’t in your workspace.</h1>
        <Link className="text-link" href="/">
          Return to overview →
        </Link>
      </section>
    </div>
  );
}
