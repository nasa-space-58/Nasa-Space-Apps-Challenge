import type { Preset } from "../types";
export default function PolarMap({
  lat,
  lon,
  sites,
  onSelect,
  onPreset,
}: {
  lat: number;
  lon: number;
  sites: Preset[];
  onSelect: (lat: number, lon: number) => void;
  onPreset: (site: Preset) => void;
}) {
  const point = (latitude: number, longitude: number) => {
    const r = ((90 + latitude) / 10) * 130,
      angle = (longitude * Math.PI) / 180;
    return [160 + r * Math.sin(angle), 150 - r * Math.cos(angle)];
  };
  const selected = point(lat, lon);
  return (
    <svg
      className="polar-map"
      viewBox="0 0 320 300"
      role="img"
      aria-label="South pole coordinate selector. Use the latitude and longitude fields for keyboard input."
      onClick={(event) => {
        const box = event.currentTarget.getBoundingClientRect();
        const x = ((event.clientX - box.left) / box.width) * 320 - 160;
        const y = ((event.clientY - box.top) / box.height) * 300 - 150;
        const r = Math.hypot(x, y);
        if (r > 130) return;
        onSelect(
          Number((-90 + (r / 130) * 10).toFixed(5)),
          Number(
            (((Math.atan2(x, -y) * 180) / Math.PI + 360) % 360).toFixed(5),
          ),
        );
      }}
    >
      <defs>
        <radialGradient id="map-shade">
          <stop offset="0" stopColor="#344148" />
          <stop offset="1" stopColor="#131d23" />
        </radialGradient>
      </defs>
      <circle
        cx="160"
        cy="150"
        r="130"
        fill="url(#map-shade)"
        stroke="#40505a"
      />
      {[26, 52, 78, 104, 130].map((r) => (
        <circle
          key={r}
          cx="160"
          cy="150"
          r={r}
          fill="none"
          stroke="#42515a"
          strokeDasharray="2 5"
          opacity=".55"
        />
      ))}
      {[0, 45, 90, 135].map((angle) => (
        <line
          key={angle}
          x1="30"
          x2="290"
          y1="150"
          y2="150"
          transform={`rotate(${angle} 160 150)`}
          stroke="#42515a"
          opacity=".5"
        />
      ))}
      <text x="160" y="12" textAnchor="middle">
        0° E
      </text>
      <text x="160" y="294" textAnchor="middle">
        180° E
      </text>
      <text x="164" y="166">
        90° S
      </text>
      {sites.map((site) => {
        const [x, y] = point(site.latitude_deg, site.longitude_deg_east);
        return (
          <g
            key={site.site_id}
            onClick={(e) => {
              e.stopPropagation();
              onPreset(site);
            }}
          >
            <circle cx={x} cy={y} r="11" fill="transparent" />
            <circle cx={x} cy={y} r="3" fill="#c1cbd1" />
            <text x={x + 7} y={y - 7}>
              {site.site_id.startsWith("malapert") ? "R23" : "MOUTON"}
            </text>
          </g>
        );
      })}
      {Number.isFinite(lat) && Number.isFinite(lon) && (
        <g>
          <circle
            cx={selected[0]}
            cy={selected[1]}
            r="9"
            fill="none"
            stroke="#edbb66"
          />
          <circle cx={selected[0]} cy={selected[1]} r="3" fill="#edbb66" />
          <line
            x1={selected[0] - 15}
            x2={selected[0] + 15}
            y1={selected[1]}
            y2={selected[1]}
            stroke="#edbb66"
            opacity=".5"
          />
        </g>
      )}
    </svg>
  );
}
