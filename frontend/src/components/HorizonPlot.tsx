import { useId } from "react";
import type { Frame, Result } from "../types";
export default function HorizonPlot({
  result,
  frame,
  scaleResults,
}: {
  result: Result;
  frame: Frame;
  scaleResults?: Result[];
}) {
  const clipId = useId();
  const x = (az: number) => 48 + (az / 360) * 820;
  // Display bounds include every returned sample so scrubbing does not rescale.
  const elevations = [
    ...(scaleResults || [result]).flatMap((view) =>
      view.horizon.directions.map((point) => point.elevation_deg),
    ),
    ...(scaleResults || [result])
      .flatMap((view) => view.frames)
      .flatMap((sample) => [
        sample.sun.elevation_deg,
        sample.earth.elevation_deg,
      ]),
  ].filter((value): value is number => value !== null);
  const lower = Math.min(-5, Math.floor(Math.min(...elevations) / 5) * 5);
  const upper = Math.max(10, Math.ceil(Math.max(...elevations) / 5) * 5);
  const y = (el: number) => 220 - ((el - lower) / (upper - lower)) * 195;
  const ticks = Array.from(
    { length: 5 },
    (_, i) => lower + ((upper - lower) * i) / 4,
  );
  const points = result.horizon.directions;
  let path = "";
  let fill = "";
  let open = false;
  let runStart = 0;
  let lastX = 0;
  const closeRun = () => {
    if (open) fill += `L${lastX},225 L${runStart},225 Z `;
  };
  for (const point of [...points, { ...points[0], azimuth_deg: 360 }]) {
    if (point.elevation_deg === null) {
      closeRun();
      open = false;
      continue;
    }
    const px = x(point.azimuth_deg),
      py = y(point.elevation_deg);
    path += `${open ? "L" : "M"}${px},${py} `;
    fill += open ? `L${px},${py} ` : `M${px},225 L${px},${py} `;
    if (!open) runStart = px;
    lastX = px;
    open = true;
  }
  closeRun();
  return (
    <svg
      className="horizon-plot"
      viewBox="-60 0 1030 270"
      role="img"
      aria-label={`Terrain horizon. Sun ${frame.sun.state}, Earth ${frame.earth.state}. Elevation in degrees.`}
    >
      <defs>
        <clipPath id={clipId}>
          <rect x="48" y="15" width="820" height="220" />
        </clipPath>
      </defs>
      {ticks.map((el) => (
        <g key={el}>
          <line
            x1="48"
            x2="868"
            y1={y(el)}
            y2={y(el)}
            className={el === 0 ? "zero-line" : "grid-line"}
          />
          <text x="32" y={y(el) + 4} textAnchor="end">
            {Number(el.toFixed(1))}°
          </text>
        </g>
      ))}
      {[0, 90, 180, 270, 360].map((az, i) => (
        <g key={az}>
          <line x1={x(az)} x2={x(az)} y1="20" y2="225" className="grid-line" />
          <text x={x(az)} y="252" textAnchor="middle">
            {["N · 0°", "E · 90°", "S · 180°", "W · 270°", "N · 360°"][i]}
          </text>
        </g>
      ))}
      <text x="-52" y="12" className="axis-title">
        ELEVATION ABOVE LEVEL ↑
      </text>
      <text x="868" y="268" textAnchor="end" className="axis-title">
        COMPASS DIRECTION (AZIMUTH) →
      </text>
      <g clipPath={`url(#${clipId})`}>
        <path d={fill} className="terrain-fill" />
        <path d={path} fill="none" stroke="#9aa5a9" strokeWidth="2" />
        {(["sun", "earth"] as const).map((target) => {
          const item = frame[target];
          if (item.azimuth_deg === null || item.elevation_deg === null)
            return null;
          return (
            <g key={target}>
              <line
                x1={x(item.azimuth_deg)}
                x2={x(item.azimuth_deg)}
                y1={y(item.elevation_deg)}
                y2="225"
                stroke={target === "sun" ? "#edbb66" : "#9ac9e5"}
                opacity=".25"
                strokeDasharray="3 5"
              />
              <circle
                cx={x(item.azimuth_deg)}
                cy={y(item.elevation_deg)}
                r={target === "sun" ? 7 : 5}
                fill={target === "sun" ? "#edbb66" : "#9ac9e5"}
              />
              <text
                x={x(item.azimuth_deg) + 12}
                y={y(item.elevation_deg) - 10}
                className={target}
              >
                {target === "sun" ? "SUN" : "EARTH"}
              </text>
            </g>
          );
        })}
      </g>
    </svg>
  );
}
