export const theme = {
  background: "#101719",
  surface: "#192326",
  raised: "#202d30",
  border: "#344649",
  text: "#f1f6f4",
  muted: "#a5b9b7",
  accent: "#b5f0cb",
  warning: "#f1cd8b",
  danger: "#ffb4ab",
  radius: 18,
};

export const jobIsActive = (state: string) =>
  ["queued", "downloading", "validating", "installing", "verifying"].includes(
    state,
  );
export const progressValue = (value: number) =>
  Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : 0;
