import tokens from "../../../design/tokens.json";

export const STAGE_WIDTH = tokens.screen.width;
export const STAGE_HEIGHT = tokens.screen.height;

export interface StageFit {
  scale: number;
  left: number;
  top: number;
}

/** Scale the fixed 1920x1080 stage to fit the window, centered, keeping 16:9. */
export function fitStage(windowWidth: number, windowHeight: number): StageFit {
  const scale = Math.min(windowWidth / STAGE_WIDTH, windowHeight / STAGE_HEIGHT);
  return {
    scale,
    left: (windowWidth - STAGE_WIDTH * scale) / 2,
    top: (windowHeight - STAGE_HEIGHT * scale) / 2,
  };
}
