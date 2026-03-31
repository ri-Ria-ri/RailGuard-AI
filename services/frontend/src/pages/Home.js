import { Link } from "react-router-dom";

export default function Home() {
  const modules = [
    { name: "AI Risk Detection", path: "/risk" },
    { name: "Camera Alerts", path: "/camera" },
    { name: "Train Delay", path: "/delay" },
    { name: "Crowd Status", path: "/crowd" },
    { name: "Alerts", path: "/alerts" },
    { name: "OpenCV Tools", path: "/opencv" },
  ];

  return (
    <div className="flex flex-col items-center justify-center h-screen bg-gray-100">
      <h1 className="text-4xl font-bold mb-6">Welcome to RailGuard</h1>
      <p className="mb-8 text-lg text-gray-600">
        AI-powered Railway Safety Monitoring
      </p>
      <div className="grid grid-cols-2 gap-6">
        {modules.map((m) => (
          <Link
            key={m.path}
            to={m.path}
            className="p-6 bg-white shadow rounded hover:bg-blue-100 text-center"
          >
            {m.name}
          </Link>
        ))}
      </div>
    </div>
  );
}
