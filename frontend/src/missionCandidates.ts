/** API assessments; trajectory lines are illustrative, never solver telemetry. */
export type MissionCandidate = {
  id: string; label: string; lat: number; lon: number;
  status: "accepted" | "rejected" | "failed" | "unknown";
  score: number | null; explanation: string; source: string;
  metrics: { label: string; value: string }[];
  altitudeKm?: number;
};
export const candidateColor = (c: MissionCandidate) =>
  ({ accepted: "#65ddbd", rejected: "#ff8a78", failed: "#db9afa", unknown: "#e5c77c" })[c.status];
