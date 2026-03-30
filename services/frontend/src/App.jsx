import { memo, useCallback, useEffect, useMemo, useState } from "react";
import "./styles.css";

const WS_ALERTS = import.meta.env.VITE_WS_URL || "ws://localhost:8000/ws/alerts";
const WS_AI = import.meta.env.VITE_WS_AI_RISK_URL || "ws://localhost:8000/ws/ai-risk";
const WS_TRAINS = import.meta.env.VITE_WS_TRAINS_URL || "ws://localhost:8000/ws/trains";
const WS_CROWD = import.meta.env.VITE_WS_CROWD_URL || "ws://localhost:8000/ws/crowd";
const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
const WS_FLUSH_MS = 100;
const MAX_ALERTS = 100;
const MAX_CAMERA_ALERTS = 80;
const MAX_TRAINS = 100;
const MAX_CROWD_ZONES = 100;
const MAX_RISK_ZONES = 100;
const ALERT_ROW_HEIGHT = 76;
const ALERT_VIEWPORT_HEIGHT = 360;
const ALERT_OVERSCAN_ROWS = 4;

function useBufferedWs(url, onBatch, flushMs = WS_FLUSH_MS) {
  useEffect(() => {
    let ws;
    let closed = false;
    let timer = null;
    const queue = [];

    const flush = () => {
      timer = null;
      if (!queue.length) {
        return;
      }
      const batch = queue.splice(0, queue.length);
      onBatch(batch);
    };

    const scheduleFlush = () => {
      if (timer) {
        return;
      }
      timer = setTimeout(flush, flushMs);
    };

    const connect = () => {
      ws = new WebSocket(url);
      ws.onmessage = (ev) => {
        try {
          queue.push(JSON.parse(ev.data));
          scheduleFlush();
        } catch {
          // Ignore malformed payloads.
        }
      };
      ws.onclose = () => {
        flush();
        if (!closed) {
          setTimeout(connect, 1500);
        }
      };
    };

    connect();
    return () => {
      closed = true;
      if (timer) {
        clearTimeout(timer);
      }
      flush();
      if (ws) {
        ws.close();
      }
    };
  }, [url, onBatch, flushMs]);
}

/* ---------- AI RISK (requires location, no conf display) ---------- */
const RiskPanel = memo(function RiskPanel() {
  const [risk, setRisk] = useState({});
  const [dropped, setDropped] = useState(0);

  const handleRiskBatch = useCallback((batch) => {
    let rejected = 0;
    setRisk((prev) => {
      const next = { ...prev };
      for (const r of batch) {
        const id = r.zoneId || r.stationId || r.platformId || r.location || r.name;
        if (!id) {
          rejected += 1;
          continue;
        }
        next[id] = { ...r, _id: id };
      }
      const trimmed = Object.values(next)
        .sort((a, b) => (b.timestamp ?? 0) - (a.timestamp ?? 0))
        .slice(0, MAX_RISK_ZONES)
        .reduce((acc, item) => {
          acc[item._id] = item;
          return acc;
        }, {});
      return trimmed;
    });
    if (rejected) {
      setDropped((n) => n + rejected);
    }
  }, []);

  useBufferedWs(WS_AI, handleRiskBatch);

  useEffect(() => {
    const id = setInterval(async () => {
      const res = await fetch(`${API_BASE}/ai/risk/latest?limit=${MAX_RISK_ZONES}`);
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

  const rows = useMemo(
    () => Object.values(risk).sort((a, b) => (b.riskScore ?? 0) - (a.riskScore ?? 0)),
    [risk],
  );

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
});

/* ---------- ALERTS (with category/subType tags) ---------- */
const AlertsPanel = memo(function AlertsPanel() {
  const [alerts, setAlerts] = useState([]);
  const [scrollTop, setScrollTop] = useState(0);

  const handleAlertBatch = useCallback((batch) => {
    setAlerts((prev) => {
      const incoming = batch.filter((a) => a.zoneId || a.stationId);
      return [...incoming.reverse(), ...prev].slice(0, MAX_ALERTS);
    });
  }, []);

  useBufferedWs(WS_ALERTS, handleAlertBatch);

  const totalRows = alerts.length;
  const startRow = Math.max(0, Math.floor(scrollTop / ALERT_ROW_HEIGHT) - ALERT_OVERSCAN_ROWS);
  const visibleRows = Math.ceil(ALERT_VIEWPORT_HEIGHT / ALERT_ROW_HEIGHT) + ALERT_OVERSCAN_ROWS * 2;
  const endRow = Math.min(totalRows, startRow + visibleRows);
  const visibleAlerts = alerts.slice(startRow, endRow);
  const topSpacer = startRow * ALERT_ROW_HEIGHT;
  const bottomSpacer = (totalRows - endRow) * ALERT_ROW_HEIGHT;

  return (
    <div className="card">
      <div className="card-title">Alerts</div>
      {totalRows === 0 && <div className="muted">Waiting for alerts…</div>}
      {totalRows > 0 && (
        <div
          className="alerts-viewport"
          onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
          style={{ maxHeight: ALERT_VIEWPORT_HEIGHT }}
        >
          {topSpacer > 0 && <div className="alert-spacer" style={{ height: topSpacer }} />}
          {visibleAlerts.map((a, idx) => (
            <div key={`${a.id || a.timestamp || "alert"}-${startRow + idx}`} className="alert-row">
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
          {bottomSpacer > 0 && <div className="alert-spacer" style={{ height: bottomSpacer }} />}
        </div>
      )}
    </div>
  );
});

/* ---------- CAMERA ALERTS (dedicated operational panel) ---------- */
const CameraAlertsPanel = memo(function CameraAlertsPanel() {
  const [cameraAlerts, setCameraAlerts] = useState([]);

  const isCameraAlert = useCallback((a) => {
    if (!a) return false;
    return (
      a.category === "CAMERA" ||
      a.source === "camera_watchdog" ||
      (typeof a.subType === "string" && a.subType.startsWith("camera_"))
    );
  }, []);

  const handleAlertBatch = useCallback(
    (batch) => {
      setCameraAlerts((prev) => {
        const incoming = batch.filter(isCameraAlert).filter((a) => a.zoneId || a.stationId);
        return [...incoming.reverse(), ...prev].slice(0, MAX_CAMERA_ALERTS);
      });
    },
    [isCameraAlert],
  );

  useBufferedWs(WS_ALERTS, handleAlertBatch);

  useEffect(() => {
    const id = setInterval(async () => {
      const res = await fetch(`${API_BASE}/alerts/latest?limit=${MAX_ALERTS}`);
      const data = await res.json();
      setCameraAlerts(
        (Array.isArray(data) ? data : [])
          .filter(isCameraAlert)
          .filter((a) => a.zoneId || a.stationId)
          .slice(0, MAX_CAMERA_ALERTS),
      );
    }, 10000);
    return () => clearInterval(id);
  }, [isCameraAlert]);

  return (
    <div className="card card-camera">
      <div className="card-title-row">
        <div className="card-title">Camera Alerts</div>
        <span className="camera-count">{cameraAlerts.length}</span>
      </div>
      {cameraAlerts.length === 0 && <div className="muted">No camera alerts yet.</div>}
      {cameraAlerts.length > 0 && (
        <div className="camera-alerts-viewport">
          {cameraAlerts.map((a, idx) => (
            <div key={`${a.id || a.timestamp || "camera"}-${idx}`} className="camera-alert-row">
              <span className={`pill sev-${(a.severity || "low").toLowerCase()}`}>{a.severity || "LOW"}</span>
              <div className="alert-body">
                <div className="alert-line">
                  <span>{a.message || "Camera event"}</span>
                  <span className="tag tag-camera">CAMERA</span>
                  {a.subType && <span className="tag tag-sub">{a.subType}</span>}
                </div>
                <div className="muted">
                  {(a.zoneId || a.stationId) ?? "unknown"}
                  {a.cameraId ? ` • ${a.cameraId}` : ""}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
});

/* ---------- TRAINS (drops items without destination) ---------- */
const TrainsPanel = memo(function TrainsPanel() {
  const [trains, setTrains] = useState({});

  const handleTrainBatch = useCallback((batch) => {
    setTrains((prev) => {
      const next = { ...prev };
      for (const msg of batch) {
        const id =
          msg.trainId ||
          msg.trainName ||
          msg.name ||
          `${msg.route || "route"}-${msg.destination || msg.zoneId || msg.stationId || "x"}`;
        next[id] = { ...msg, _receivedAt: Date.now() };
      }
      const trimmed = Object.values(next)
        .sort((a, b) => (b._receivedAt || 0) - (a._receivedAt || 0))
        .slice(0, MAX_TRAINS)
        .reduce((acc, item) => {
          const id =
            item.trainId ||
            item.trainName ||
            item.name ||
            `${item.route || "route"}-${item.destination || item.zoneId || item.stationId || "x"}`;
          acc[id] = item;
          return acc;
        }, {});
      return trimmed;
    });
  }, []);

  useBufferedWs(WS_TRAINS, handleTrainBatch);

  const rows = useMemo(
    () =>
      Object.values(trains)
        .filter((t) => t.destination || t.dest || t.terminal || t.headsign || t.nextStop || t.zoneId || t.stationId)
        .sort((a, b) => (b._receivedAt || 0) - (a._receivedAt || 0))
        .slice(0, MAX_TRAINS),
    [trains],
  );

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
});

/* ---------- CROWD (requires location) ---------- */
const CrowdPanel = memo(function CrowdPanel() {
  const [crowd, setCrowd] = useState({});

  const handleCrowdBatch = useCallback((batch) => {
    setCrowd((prev) => {
      const next = { ...prev };
      for (const msg of batch) {
        const zone = msg.zoneId || msg.stationId || msg.platformId || msg.location;
        if (!zone) {
          continue;
        }
        next[zone] = msg;
      }
      const trimmed = Object.values(next)
        .sort((a, b) => (b.timestamp ?? 0) - (a.timestamp ?? 0))
        .slice(0, MAX_CROWD_ZONES)
        .reduce((acc, item) => {
          const zone = item.zoneId || item.stationId || item.platformId || item.location;
          if (zone) {
            acc[zone] = item;
          }
          return acc;
        }, {});
      return trimmed;
    });
  }, []);

  useBufferedWs(WS_CROWD, handleCrowdBatch);
  const rows = useMemo(() => Object.values(crowd).slice(0, MAX_CROWD_ZONES), [crowd]);

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
});

/* ---------- APP ---------- */
export default function App() {
  return (
    <div className="layout">
      <RiskPanel />
      <CameraAlertsPanel />
      <div className="three-col">
        <TrainsPanel />
        <CrowdPanel />
        <AlertsPanel />
      </div>
    </div>
  );
}