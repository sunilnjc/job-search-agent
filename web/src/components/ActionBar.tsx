import { useRunTrigger } from "../hooks/useRun";

export function ActionBar() {
  const { triggerFetch, triggerMatch, isTriggering, runStatus, clearRun } = useRunTrigger();
  const running = runStatus?.status === "running";

  return (
    <div className="action-bar">
      <div className="action-bar-buttons">
        <button disabled={isTriggering || running} onClick={() => triggerFetch(undefined)}>
          Fetch new jobs
        </button>
        <button disabled={isTriggering || running} onClick={() => triggerMatch(undefined)}>
          Match jobs
        </button>
        {runStatus && !running && (
          <button className="action-bar-clear" onClick={clearRun}>
            Clear log
          </button>
        )}
      </div>
      {runStatus && (
        <div className="run-log">
          <div className="run-log-status">
            {running ? "Running…" : runStatus.status === "error" ? "Failed" : "Done"}
          </div>
          <div className="run-log-lines">
            {runStatus.log.slice(-8).map((line, i) => (
              <div key={i} className="run-log-line">{line}</div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
