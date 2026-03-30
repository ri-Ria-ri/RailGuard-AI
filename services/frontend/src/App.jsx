import { useEffect, useMemo, useRef, useState } from "react";
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
const WS_URL = import.meta.env.VITE_WS_URL || "ws://localhost:8000/ws/alerts";
const WS_CROWD_URL = import.meta.env.VITE_WS_CROWD_URL || "ws://localhost:8000/ws/crowd";
const WS_TRAINS_URL = import.meta.env.VITE_WS_TRAINS_URL || "ws://localhost:8000/ws/trains";

const severityOrder = { HIGH: 3, MEDIUM: 2, LOW: 1 };
const crowdOrder = ["PF-1", "PF-2", "PF-3", "ENTRY-A", "ENTRY-B"];

function App() {
  const [alerts, setAlerts] = useState([]);
  const [crowdByZone, setCrowdByZone] = useState({});
  const [trains, setTrains] = useState([]);
  const [severityFilter, setSeverityFilter] = useState("ALL");
  const [alertsState, setAlertsState] = useState("CONNECTING");
  const [crowdState, setCrowdState] = useState("CONNECTING");
  const [trainsState, setTrainsState] = useState("CONNECTING");
  const reconnectTimerRef = useRef(null);
  const crowdReconnectTimerRef = useRef(null);
  const trainsReconnectTimerRef = useRef(null);

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

    fetch(`${API_BASE_URL}/crowd/latest`)
      .then((res) => res.json())
      .then((data) => {
        const byZone = {};
        for (const item of data) {
          byZone[item.zoneId] = item;
        }
        setCrowdByZone(byZone);
      })
      .catch(() => {
        // Keep UI running even if initial fetch fails.
      });

    fetch(`${API_BASE_URL}/trains/latest`)
      .then((res) => res.json())
      .then((data) => {
        setTrains(data);
      })
      .catch(() => {
        // Keep UI running even if initial fetch fails.
      });
  }, []);

  useEffect(() => {
    let ws;

    const connect = () => {
      setAlertsState("CONNECTING");
      ws = new WebSocket(WS_URL);

      ws.onopen = () => {
        setAlertsState("LIVE");
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
        setAlertsState("RECONNECTING");
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

  useEffect(() => {
    let ws;

    const connect = () => {
      setCrowdState("CONNECTING");
      ws = new WebSocket(WS_CROWD_URL);

      ws.onopen = () => {
        setCrowdState("LIVE");
      };

      ws.onmessage = (event) => {
        try {
          const crowd = JSON.parse(event.data);
          setCrowdByZone((prev) => ({
            ...prev,
            [crowd.zoneId]: crowd,
          }));
        } catch {
          // Ignore malformed event payloads.
        }
      };

      ws.onclose = () => {
        setCrowdState("RECONNECTING");
        crowdReconnectTimerRef.current = setTimeout(connect, 2000);
      };

      ws.onerror = () => {
        ws.close();
      };
    };

    connect();

    return () => {
      if (crowdReconnectTimerRef.current) {
        clearTimeout(crowdReconnectTimerRef.current);
      }
      if (ws && ws.readyState <= 1) {
        ws.close();
      }
    };
  }, []);

  useEffect(() => {
    let ws;

    const connect = () => {
      setTrainsState("CONNECTING");
      ws = new WebSocket(WS_TRAINS_URL);

      ws.onopen = () => {
        setTrainsState("LIVE");
      };

      ws.onmessage = (event) => {
        try {
          const train = JSON.parse(event.data);
          setTrains((prev) => {
            const merged = [train, ...prev.filter((item) => item.trainNumber !== train.trainNumber)];
            return merged.sort((a, b) => a.trainNumber.localeCompare(b.trainNumber));
          });
        } catch {
          // Ignore malformed event payloads.
        }
      };

      ws.onclose = () => {
        setTrainsState("RECONNECTING");
        trainsReconnectTimerRef.current = setTimeout(connect, 2000);
      };

      ws.onerror = () => {
        ws.close();
      };
    };

    connect();

    return () => {
      if (trainsReconnectTimerRef.current) {
        clearTimeout(trainsReconnectTimerRef.current);
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

  const crowdRows = useMemo(
    () =>
      crowdOrder
        .filter((zone) => crowdByZone[zone])
        .map((zone) => ({
          zoneId: zone,
          ...crowdByZone[zone],
        })),
    [crowdByZone]
  );

  const sortedTrains = useMemo(
    () => [...trains].sort((a, b) => a.trainNumber.localeCompare(b.trainNumber)),
    [trains]
  );

  const crowdClass = (density) => {
    if (density >= 75) return "density-critical";
    if (density >= 50) return "density-crowded";
    return "density-normal";
  };

  return (
    <main className="page">
      <header className="header">
        <div>
          <h1>RailGuard AI SOC</h1>
          <p>Live alerts, crowd density, and train status</p>
        </div>
        <div className="status-strip">
          <div className={`badge badge-${alertsState.toLowerCase()}`}>Alerts: {alertsState}</div>
          <div className={`badge badge-${crowdState.toLowerCase()}`}>Crowd: {crowdState}</div>
          <div className={`badge badge-${trainsState.toLowerCase()}`}>Trains: {trainsState}</div>
        </div>
      </header>

      <section className="dashboard-grid">
        <section className="panel">
          <div className="panel-header">
            <h2>Alert Queue</h2>
            <div className="controls">
              <label htmlFor="severity">Severity:</label>
              <select id="severity" value={severityFilter} onChange={(e) => setSeverityFilter(e.target.value)}>
                <option value="ALL">All</option>
                <option value="HIGH">High</option>
                <option value="MEDIUM">Medium</option>
                <option value="LOW">Low</option>
              </select>
            </div>
          </div>
          <div className="alerts">
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
          </div>
        </section>

        <section className="panel">
          <div className="panel-header">
            <h2>Crowd Density</h2>
          </div>
          {crowdRows.length === 0 ? (
            <div className="empty">No crowd data yet.</div>
          ) : (
            <div className="crowd-list">
              {crowdRows.map((row) => (
                <div key={row.zoneId} className={`crowd-item ${crowdClass(row.densityPercent)}`}>
                  <span>{row.zoneId}</span>
                  <strong>{row.densityPercent}%</strong>
                  <span>{row.status || "NORMAL"}</span>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="panel panel-wide">
          <div className="panel-header">
            <h2>Train Status</h2>
          </div>
          {sortedTrains.length === 0 ? (
            <div className="empty">No train updates yet.</div>
          ) : (
            <div className="table-wrap">
              <table className="train-table">
                <thead>
                  <tr>
                    <th>Train</th>
                    <th>Route</th>
                    <th>Current</th>
                    <th>Next</th>
                    <th>ETA</th>
                    <th>Delay</th>
                    <th>Kavach</th>
                  </tr>
                </thead>
                <tbody>
                  {sortedTrains.map((train) => (
                    <tr key={train.trainNumber}>
                      <td>{train.trainNumber} - {train.trainName}</td>
                      <td>{train.route}</td>
                      <td>{train.currentStation}</td>
                      <td>{train.nextStation}</td>
                      <td>{train.etaMinutes}m</td>
                      <td>{train.delayMinutes}m</td>
                      <td>{train.kavachStatus}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </section>
    </main>
  );
}

export default App;
