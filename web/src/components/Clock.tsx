import { formatClock, formatLongDate } from "../lib/time";

interface ClockProps {
  now: number;
  timeZone: string;
  className?: string;
}

export function Clock({ now, timeZone, className = "clock" }: ClockProps) {
  return (
    <div className={className} data-testid="clock">
      <div className="clock-time">{formatClock(now, timeZone)}</div>
      <div className="clock-date">{formatLongDate(now, timeZone)}</div>
    </div>
  );
}
