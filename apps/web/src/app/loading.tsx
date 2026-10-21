export default function Loading() {
  return (
    <div className="page-content" role="status">
      <div className="loading-state">
        <span className="loading-spinner" />
        <h1>Loading your workspace</h1>
        <p>Fetching records from the local API…</p>
      </div>
    </div>
  );
}
