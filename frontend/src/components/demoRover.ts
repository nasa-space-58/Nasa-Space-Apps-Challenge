import { terrainHeight } from "./LunarSurface";
import type { SurfaceScene } from "./surfaceScene";
import type { RouteResult } from "./SurfaceOps";

/** A* on the same crater field rendered by the scene. Sampling is DEMO, not QPU. */
export function demoRover(seed: number): RouteResult {
  const n = 49, cell = 100;
  const random = (i: number) => { const x = Math.sin(i * 127.1 + seed * 91.7) * 43758.5453; return x - Math.floor(x); };
  const heights = Array.from({ length: n }, (_, r) => Array.from({ length: n }, (_, c) => terrainHeight(c - 24, r - 24) * cell));
  const passable = heights.map((row, r) => row.map((h, c) => {
    const dx = ((heights[r][Math.min(n - 1, c + 1)] - heights[r][Math.max(0, c - 1)]) / (2 * cell));
    const dz = ((heights[Math.min(n - 1, r + 1)][c] - heights[Math.max(0, r - 1)][c]) / (2 * cell));
    return Number.isFinite(h) && Math.atan(Math.hypot(dx, dz)) * 180 / Math.PI <= 24;
  }));
  // Pick a reachable goal in the outer column, so every seed can deploy a rover.
  const start = 24 * n + 24;
  const connected = new Set([start]), queue = [start];
  for (let q = 0; q < queue.length; q++) {
    const id = queue[q], r = Math.floor(id / n), c = id % n;
    for (const [dr, dc] of [[1,0],[-1,0],[0,1],[0,-1]]) {
      const rr = r + dr, cc = c + dc, next = rr * n + cc;
      if (passable[rr]?.[cc] && !connected.has(next)) { connected.add(next); queue.push(next); }
    }
  }
  const targets = Array.from({length:25},(_,i)=>i+12).filter(r=>connected.has(r*n+46));
  const goalRow = targets[Math.floor(random(7) * targets.length)];
  const goal = goalRow * n + 46;
  const scene: SurfaceScene = { pixel_m: cell, slope_limit_deg: 24, rows: n, cols: n,
    window: {row0:0,col0:0,row1:n,col1:n}, site_cell:[24,24], heights_m:heights, passable,
    local_from_cell: {east:[cell,0,-24.5*cell],north:[0,-cell,24.5*cell]}, explored:{}, explored_truncated:{}, path_cells:{} };
  const routes = ["Demo · A*", "Demo · sampled cost"].map((target_id, mode) => {
    const costs = new Map<number,number>([[start,0]]), parents = new Map<number,number>(), open = new Set([start]), closed = new Set<number>();
    const explored: [number,number][] = [];
    const heuristic = (id:number) => Math.hypot(Math.floor(id/n)-goalRow,id%n-46);
    while(open.size) {
      let current = -1, best = Infinity;
      for(const id of open) { const f=costs.get(id)!+heuristic(id); if(f<best){best=f;current=id;} }
      open.delete(current); closed.add(current);
      const r=Math.floor(current/n), c=current%n; explored.push([r,c]);
      if(current===goal) break;
      for(const [dr,dc] of [[1,0],[-1,0],[0,1],[0,-1],[1,1],[1,-1],[-1,1],[-1,-1]]) {
        const rr=r+dr,cc=c+dc,id=rr*n+cc;
        if(!passable[rr]?.[cc] || closed.has(id) || (dr&&dc&&(!passable[r][cc]||!passable[rr][c]))) continue;
        const slope=Math.abs(heights[rr][cc]-heights[r][c])/cell;
        const score=costs.get(current)!+Math.hypot(dr,dc)*(1+slope*5+(mode?random(id)*5:0));
        if(score<(costs.get(id)??Infinity)){costs.set(id,score);parents.set(id,current);open.add(id);}
      }
    }
    const cells: [number,number][]=[];
    if(closed.has(goal)){let id=goal;while(id!==start){cells.push([Math.floor(id/n),id%n]);id=parents.get(id)!;}cells.push([24,24]);cells.reverse();}
    scene.explored[target_id]=explored;scene.explored_truncated[target_id]=false;scene.path_cells[target_id]=cells;
    const path=cells.map(([r,c])=>({east_m:(c-24)*cell,north_m:(24-r)*cell}));
    let length=0,gain=0,slope=0;
    for(let i=1;i<cells.length;i++){const [r,c]=cells[i],[pr,pc]=cells[i-1];const run=Math.hypot(r-pr,c-pc)*cell,dh=heights[r][c]-heights[pr][pc];length+=Math.hypot(run,dh);gain+=Math.max(0,dh);slope=Math.max(slope,Math.atan2(Math.abs(dh),run)*180/Math.PI);}
    return {target_id,reachable:path.length>0,length_m:length,duration_s:length/0.1,max_slope_deg:slope,elevation_gain_m:gain,detour_ratio:length/(Math.hypot(goalRow-24,22)*cell),path,waypoints:[]};
  });
  return {sky_evaluated:false,routes,scene};
}
