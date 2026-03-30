import { useEffect, useState } from "react";
import "./styles.css";

const WS_ALERTS = import.meta.env.VITE_WS_URL || "ws://localhost:8000/ws/alerts";
const WS_AI = import.meta.env.VITE_WS_AI_RISK_URL || "ws://localhost:8000/ws/ai-risk";
const WS_TRAINS = import.meta.env.VITE_WS_TRAINS_URL || "ws://localhost:8000/ws/trains";
const WS_CROWD = import.meta.env.VITE_WS_CROWD_URL || "ws://localhost:8000/ws/crowd";
const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

function useWs(url, onMessage) {
  useEffect(() => {
    let ws = new WebSocket(url);
    ws.onmessage = (ev) => onMessage(JSON.parse(ev.data));
    ws.onclose = () => setTimeout(() => (ws = new WebSocket(url)), 1500);
    return () => ws.close();
  }, [url, onMessage]);
}

/* ---------- AI RISK (requires location, no conf display) ---------- */
function RiskPanel() {
  const [risk, setRisk] = useState({});
  const [dropped, setDropped] = useState(0);

  const upsert = (r) => {
    const id = r.zoneId || r.stationId || r.platformId || r.location || r.name;
    if (!id) { setDropped((n) => n + 1); return; }
    setRisk((prev) => ({ ...prev, [id]: { ...r, _id: id } }));
  };

  useWs(WS_AI, upsert);

  useEffect(() => {
    const id = setInterval(async () => {
      const res = await fetch(`${API_BASE}/ai/risk/latest`);
      const data = await res.json();
      const map = {};
      let rejected = 0;
      data.forEach((r) => {
        const z = r.zoneId || r.stationId || r.platformId || r.location || r.name;
        if (z) map[z] = { ...r, _id: z };
        else rejected += 1;
      });
      setRisk(map);
      if (rejected) setDropped((n) => n + rejected);
    }, 10000);
    return () => clearInterval(id);
  }, []);

  const rows = Object.values(risk).sort((a, b) => (b.riskScore ?? 0) - (a.riskScore ?? 0));

  return (
    <div className="card card-wide">
      <div className="card-title">AI RISK</div>
      {dropped > 0 && (
        <div className="warning-line">
          {dropped} risk event(s) ignored: missing zone/station/platform.
        </div>
      )}
      {rows.length === 0 && <div className="muted">No risk events yet.</div>}
      {rows.map((r) => (
        <div key={r._id} className="risk-row">
          <div className="row-head">
            <span className="zone">{r._id}</span>
            <span className="score">{(r.riskScore * 100).toFixed(0)}%</span>
            <span className={`level level-${r.riskLevel?.toLowerCase()}`}>{r.riskLevel}</span>
          </div>
          <div className="factors">
            {r.topFactors?.map((f, i) => (
              <span key={i} className="factor">
                {f.factor}: {(f.contribution * 100).toFixed(0)}%
              </span>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

/* ---------- ALERTS (with category/subType tags) ---------- */
function AlertsPanel() {
  const [alerts, setAlerts] = useState([]);
  useWs(WS_ALERTS, (msg) => setAlerts((prev) => [msg, ...prev].slice(0, 50)));
  const filtered = alerts.filter((a) => a.zoneId || a.stationId);
  return (
    <div className="card">
      <div className="card-title">Alerts</div>
      {filtered.length === 0 && <div className="muted">Waiting for alerts…</div>}
      {filtered.map((a, idx) => (
        <div key={idx} className="alert-row">
          <span className={`pill sev-${(a.severity || "low").toLowerCase()}`}>{a.severity || "LOW"}</span>
          <div className="alert-body">
            <div className="alert-line">
              <span>{a.message || a.type || "event"}</span>
              {a.category && <span className="tag">{a.category}</span>}
              {a.subType && <span className="tag tag-sub">{a.subType}</span>}
            </div>
            <div className="muted">{a.zoneId || a.stationId}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

/* ---------- TRAINS (drops items without destination) ---------- */
function TrainsPanel() {
  const [trains, setTrains] = useState({});
  useWs(WS_TRAINS, (msg) => {
    const id =
      msg.trainId ||
      msg.trainName ||
      msg.name ||
      `${msg.route || "route"}-${msg.destination || msg.zoneId || msg.stationId || "x"}`;
    setTrains((prev) => ({ ...prev, [id]: { ...msg, _receivedAt: Date.now() } }));
  });

  const rows = Object.values(trains)
    .filter((t) => t.destination || t.dest || t.terminal || t.headsign || t.nextStop || t.zoneId || t.stationId)
    .sort((a, b) => (b._receivedAt || 0) - (a._receivedAt || 0));

  return (
    <div className="card">
      <div className="card-title">Train Delay</div>
      {rows.length === 0 && <div className="muted">No train data yet.</div>}
      <div className="train-list">
        {rows.map((t, i) => {
          const delay = t.delayMinutes ?? 0;
          const name = t.trainName || t.name || t.trainId || "Train";
          const destination =
            t.destination ||
            t.dest ||
            t.terminal ||
            t.headsign ||
            t.nextStop ||
            t.zoneId ||
            t.stationId;
          const route = t.route || t.line || t.service || "";
          const zone = t.zoneId || t.stationId || "";
          return (
            <div key={name + i} className="train-row">
              <div className="row-head">
                <span className="zone">{name}</span>
                {route && <span className="muted">{route}</span>}
                <span className="muted">→ {destination}</span>
                <span className={`pill delay-${delay >= 10 ? "high" : delay >= 5 ? "med" : "low"}`}>
                  {delay} min delay
                </span>
              </div>
              <div className="muted">
                {zone && `Zone: ${zone}`}
                {t.status ? ` • Status: ${t.status}` : ""}
                {t.nextArrival ? ` • Next arrival: ${t.nextArrival}` : ""}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ---------- CROWD (requires location) ---------- */
function CrowdPanel() {
  const [crowd, setCrowd] = useState({});
  useWs(WS_CROWD, (msg) => {
    const zone = msg.zoneId || msg.stationId || msg.platformId || msg.location;
    if (!zone) return;
    setCrowd((prev) => ({ ...prev, [zone]: msg }));
  });
  const rows = Object.values(crowd);
  return (
    <div className="card">
      <div className="card-title">Crowd Status</div>
      {rows.length === 0 && <div className="muted">No crowd data yet.</div>}
      {rows.map((c, i) => (
        <div key={i} className="crowd-row">
          <div className="row-head">
            <span className="zone">{c.zoneId || c.stationId || c.platformId || c.location}</span>
            <span className={`pill crowd-${(c.crowdClass || "normal").toLowerCase()}`}>
              {c.crowdClass || "NORMAL"}
            </span>
          </div>
          <div className="muted">
            Density: {c.densityPercent ?? "?"}% • Count: {c.personCount ?? "?"}
          </div>
        </div>
      ))}
    </div>
  );
}

/* ---------- APP ---------- */
export default function App() {
  return (
    <div className="layout">
      <RiskPanel />
      <div className="three-col">
        <TrainsPanel />
        <CrowdPanel />
        <AlertsPanel />
      </div>
    </div>
  );
}