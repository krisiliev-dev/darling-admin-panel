// Register only the Chart.js pieces we use, and a helper to read theme colors at runtime.
import {
  Chart as ChartJS,
  ArcElement, BarElement, LineElement, PointElement, CategoryScale, LinearScale, Tooltip, Legend,
} from 'chart.js'

ChartJS.register(ArcElement, BarElement, LineElement, PointElement, CategoryScale, LinearScale, Tooltip, Legend)

// Chart.js drives its intro animation via requestAnimationFrame, which is PAUSED in
// hidden/backgrounded tabs — the canvas would stay blank until first paint. Disable the
// animation so charts render synchronously (also respects users who dislike motion).
ChartJS.defaults.animation = false

export function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}
