"use client";

export default function ErrorPage({ reset }: { reset: () => void }) {
  return (
    <div className="page-content">
      <section className="error-state" role="alert">
        <p className="eyebrow">WORKSPACE UNAVAILABLE</p>
        <h1>We couldn’t load your records.</h1>
        <p>
          The local API or database may still be starting. Check the setup
          instructions, then try again.
        </p>
        <button className="secondary-button" onClick={reset}>
          Try again
        </button>
      </section>
    </div>
  );
}
