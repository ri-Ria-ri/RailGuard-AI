import { useEffect, useMemo, useRef, useState } from "react";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
const WS_URL = import.meta.env.VITE_WS_URL || "ws://localhost:8000/ws/alerts";

const severityOrder = { HIGH: 3, MEDIUM: 2, LOW: 1 };

function App() {
  const [alerts, setAlerts] = useState([]);
  const [severityFilter, setSeverityFilter] = useState("ALL");
  const [connectionState, setConnectionState] = useState("CONNECTING");
  const reconnectTimerRef = useRef(null);

  useEffect(() => {
    fetch(`${API_BASE_URL}/alerts?limit=50`)
      .then((res) => res.json())
      .then((data) => {
        const sorted = [...data].sort((a, b) => {
          const scoreDiff = (severityOrder[b.severity] || 0) - (severityOrder[a.severity] || 0);
          if (scoreDiff !== 0) return scoreDiff;
          return new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime();
        });
        setAlerts(sorted);
      })
      .catch(() => {
        // Keep UI running even if initial fetch fails.
      });
  }, []);

  useEffect(() => {
    let ws;

    const connect = () => {
      setConnectionState("CONNECTING");
      ws = new WebSocket(WS_URL);

      ws.onopen = () => {
        setConnectionState("LIVE");
      };

      ws.onmessage = (event) => {
        try {
          const alert = JSON.parse(event.data);
          setAlerts((prev) => {
            const merged = [alert, ...prev.filter((item) => item.id !== alert.id)];
            return merged
              .sort((a, b) => {
                const scoreDiff = (severityOrder[b.severity] || 0) - (severityOrder[a.severity] || 0);
                if (scoreDiff !== 0) return scoreDiff;
                return new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime();
              })
              .slice(0, 100);
          });
        } catch {
          // Ignore malformed event payloads.
        }
      };

      ws.onclose = () => {
        setConnectionState("RECONNECTING");
        reconnectTimerRef.current = setTimeout(connect, 2000);
      };

      ws.onerror = () => {
        ws.close();
      };
    };

    connect();

    return () => {
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
      }
      if (ws && ws.readyState <= 1) {
        ws.close();
      }
    };
  }, []);

  const filteredAlerts = useMemo(() => {
    if (severityFilter === "ALL") {
      return alerts;
    }
    return alerts.filter((a) => a.severity === severityFilter);
  }, [alerts, severityFilter]);

  return (
    <main className="page">
      <header className="header">
        <div>
          <h1>RailGuard AI SOC</h1>
          <p>Live safety alert queue</p>
        </div>
        <div className={`badge badge-${connectionState.toLowerCase()}`}>{connectionState}</div>
      </header>

      <section className="controls">
        <label htmlFor="severity">Severity filter:</label>
        <select id="severity" value={severityFilter} onChange={(e) => setSeverityFilter(e.target.value)}>
          <option value="ALL">All</option>
          <option value="HIGH">High</option>
          <option value="MEDIUM">Medium</option>
          <option value="LOW">Low</option>
        </select>
      </section>

      <section className="alerts">
        {filteredAlerts.length === 0 ? (
          <div className="empty">No alerts yet.</div>
        ) : (
          filteredAlerts.map((alert) => (
            <article key={alert.id} className={`alert alert-${alert.severity.toLowerCase()}`}>
              <div className="alert-top">
                <strong>{alert.severity}</strong>
                <span>{new Date(alert.timestamp).toLocaleString()}</span>
              </div>
              <div className="alert-middle">
                <span>Zone: {alert.zoneId}</span>
                <span>Source: {alert.source}</span>
                <span>Risk Score: {Number(alert.riskScore).toFixed(3)}</span>
              </div>
              <code className="alert-id">{alert.id}</code>
            </article>
          ))
        )}
      </section>
    </main>
  );
}

export default App;
