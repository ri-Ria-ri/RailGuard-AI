import { useEffect, useState, useRef } from "react";
import "./styles.css";
import Heatmap from "./Heatmap";

const WS_ALERTS = import.meta.env.VITE_WS_URL || "ws://localhost:8000/ws/alerts";
const WS_AI = import.meta.env.VITE_WS_AI_RISK_URL || "ws://localhost:8000/ws/ai-risk";
const WS_HEAP = import.meta.env.VITE_WS_HEAP_URL || "ws://localhost:8000/ws/heap";
const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

function useWs(url, onMessage) {
  useEffect(() => {
    let ws = new WebSocket(url);
    ws.onmessage = (ev) => onMessage(JSON.parse(ev.data));
    ws.onclose = () => {
      setTimeout(() => {
        ws = new WebSocket(url);
      }, 2000);
    };
    return () => ws.close();
  }, [url]);
}

function RiskPanel() {
  const [risk, setRisk] = useState({});
  useWs(WS_AI, (msg) => setRisk((prev) => ({ ...prev, [msg.zoneId]: msg })));

  useEffect(() => {
    const id = setInterval(async () => {
      const res = await fetch(`${API_BASE}/ai/risk/latest`);
      const data = await res.json();
      const map = {};
      data.forEach((r) => (map[r.zoneId] = r));
      setRisk(map);
    }, 10000);
    return () => clearInterval(id);
  }, []);

  const rows = Object.values(risk);

  return (
    <div className="card">
      <h2>AI Risk (Live)</h2>
      {rows.length === 0 && <div className="muted">No risk events yet.</div>}
      {rows.map((r) => (
        <div key={r.zoneId} className={`risk-row level-${r.riskLevel.toLowerCase()}`}>
          <div className="row-head">
            <span className="zone">{r.zoneId}</span>
            <span className="score">{(r.riskScore * 100).toFixed(0)}%</span>
            <span className="level">{r.riskLevel}</span>
            <span className="conf">conf {Math.round(r.confidence * 100)}%</span>
          </div>
          <div className="factors">
            {r.topFactors?.map((f, i) => (
              <span key={i} className="factor">{`${f.factor}: ${(f.contribution * 100).toFixed(0)}%`}</span>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function AlertsPanel() {
  const [alerts, setAlerts] = useState([]);
  useWs(WS_ALERTS, (msg) => setAlerts((prev) => [msg, ...prev].slice(0, 50)));
  return (
    <div className="card">
      <h2>Alerts</h2>
      {alerts.map((a, idx) => (
        <div key={idx} className="alert-row">
          <span>{a.severity || "LOW"}</span>
          <span>{a.message || a.type || "event"}</span>
          <span className="muted">{a.zoneId || a.stationId || "n/a"}</span>
        </div>
      ))}
    </div>
  );
}

function HeapPanel() {
  const [heapData, setHeapData] = useState(null);

  // Subscribe to heap WebSocket
  useWs(WS_HEAP, (msg) => setHeapData(msg));

  return (
    <div className="card">
      <h2>Heap Usage Heatmap</h2>
      <Heatmap data={heapData} />
    </div>
  );
}

export default function App() {
  return (
    <div className="layout">
      <RiskPanel />
      <AlertsPanel />
      <HeapPanel />
    </div>
  );
}

import { BrowserRouter as Router, Route, Routes } from "react-router-dom";
import Home from "./pages/Home";
import Risk from "./pages/Risk";
import Camera from "./pages/Camera";
import Delay from "./pages/Delay";
import Crowd from "./pages/Crowd";
import Alerts from "./pages/Alerts";
import OpenCV from "./pages/OpenCV";

function App() {
  return (
    <Router>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/risk" element={<Risk />} />
        <Route path="/camera" element={<Camera />} />
        <Route path="/delay" element={<Delay />} />
        <Route path="/crowd" element={<Crowd />} />
        <Route path="/alerts" element={<Alerts />} />
        <Route path="/opencv" element={<OpenCV />} />
      </Routes>
    </Router>
  );
}

export default App;

import { BrowserRouter as Router, Routes, Route } from "react-router-dom";

function App() {
  return (
    <Router>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/risk" element={<Risk />} />
        <Route path="/camera" element={<Camera />} />
        <Route path="/delay" element={<Delay />} />
        <Route path="/crowd" element={<Crowd />} />
        <Route path="/alerts" element={<Alerts />} />
        <Route path="/opencv" element={<OpenCV />} />
      </Routes>
    </Router>
  );
}

export default App;