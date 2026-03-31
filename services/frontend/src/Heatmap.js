import React, { useEffect, useRef } from "react";
import h337 from "heatmap.js";

const Heatmap = ({ data }) => {
  const heatmapRef = useRef(null);
  const heatmapInstance = useRef(null);

  useEffect(() => {
    if (!heatmapInstance.current) {
      heatmapInstance.current = h337.create({
        container: heatmapRef.current,
        radius: 40,
      });
    }
    if (data) {
      heatmapInstance.current.setData(data);
    }
  }, [data]);

  return (
    <div
      ref={heatmapRef}
      style={{
        width: "800px",
        height: "600px",
        border: "1px solid #ccc",
        margin: "20px auto",
      }}
    />
  );
};

export default Heatmap;