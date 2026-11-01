import { type ReactNode, useEffect, useState } from "react";
import { fitStage, STAGE_HEIGHT, STAGE_WIDTH } from "./lib/stage";

const currentFit = () => fitStage(window.innerWidth, window.innerHeight);

/** The fixed 1920x1080 "10-foot" canvas, scaled to whatever window it is in. */
export function Stage({ children }: { children: ReactNode }) {
  const [fit, setFit] = useState(currentFit);

  useEffect(() => {
    const onResize = () => {
      setFit(currentFit());
    };
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
    };
  }, []);

  return (
    <div
      className="stage"
      data-testid="stage"
      style={{
        width: STAGE_WIDTH,
        height: STAGE_HEIGHT,
        transform: `translate(${fit.left}px, ${fit.top}px) scale(${fit.scale})`,
      }}
    >
      {children}
    </div>
  );
}
