/** Original product illustrations. Colors are illustrative, not device identity. */
export interface DeviceArtwork {
  src: string;
  alt: string;
  kind: "controller" | "receiver" | "accessory";
}

const image = (content: string, viewBox = "0 0 360 250") =>
  `data:image/svg+xml;charset=utf-8,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="${viewBox}">${content}</svg>`)}`;

function controller(variant: "ultimate2" | "bluetooth" | "24g"): string {
  const modern = variant === "ultimate2";
  const white = variant === "bluetooth";
  const top = white ? "#f7f7f2" : modern ? "#636778" : "#c8bde3";
  const bottom = white ? "#c9ccd1" : modern ? "#282c39" : "#8b7ca6";
  const trim = white ? "#acb1bb" : "#242735";
  const ink = white ? "#636b7b" : "#e1e3ed";
  const labels = white ? ["X", "A", "B", "Y"] : ["Y", "B", "A", "X"];
  const buttons = [[244, 63], [263, 82], [244, 101], [225, 82]].map(([x,y], i) =>
    `<circle cx="${x}" cy="${y}" r="11" fill="#262b36" stroke="#171b25" stroke-width="2"/><text x="${x}" y="${y+4}" fill="#e9edf5" font-size="10" text-anchor="middle" font-family="sans-serif">${labels[i]}</text>`).join("");
  const stick = (x: number, y: number) => `<circle cx="${x}" cy="${y}" r="29" fill="${trim}"/>
    ${modern ? `<circle cx="${x}" cy="${y}" r="26" fill="none" stroke="#91e3cd" stroke-width="2.5"/>` : ""}
    <circle cx="${x}" cy="${y}" r="22" fill="url(#stick)" stroke="#171c26" stroke-width="2"/>
    <circle cx="${x}" cy="${y}" r="16" fill="none" stroke="#626875" stroke-width="1.5"/>`;
  return image(`<defs>
    <linearGradient id="body" x2=".2" y2="1"><stop stop-color="${top}"/><stop offset="1" stop-color="${bottom}"/></linearGradient>
    <radialGradient id="stick" cx=".35" cy=".25"><stop stop-color="#5d6470"/><stop offset="1" stop-color="#292e3a"/></radialGradient>
  </defs>
  <ellipse cx="180" cy="224" rx="122" ry="12" fill="#080e19" opacity=".19"/>
  <path d="M73 45 Q81 26 119 27 L135 41 M225 41 L241 27 Q279 27 287 45" fill="#323747" stroke="#1c2230" stroke-width="5"/>
  <path d="M77 43 Q101 35 136 43 Q180 54 224 43 Q259 35 283 43 Q303 55 309 92 L328 181 Q333 214 309 219 Q286 222 266 189 L243 151 Q180 167 117 151 L94 189 Q74 222 51 219 Q27 214 32 181 L51 92 Q57 55 77 43Z" fill="url(#body)" stroke="${trim}" stroke-width="2.5"/>
  <path d="M49 171 L66 102 M311 171 L294 102" stroke="${white ? "#fff" : "#a5a6b5"}" opacity=".25" stroke-width="3" stroke-linecap="round"/>
  ${stick(103,82)}${stick(216,144)}${buttons}
  <path d="M106 122 h16 v16 h16 v16 h-16 v16 h-16 v-16 H90 v-16 h16Z" fill="#313743" stroke="#1e2430" stroke-width="2" stroke-linejoin="round"/>
  <circle cx="114" cy="146" r="5" fill="#3d4451"/>
  <circle cx="159" cy="92" r="7" fill="${trim}"/><circle cx="194" cy="92" r="7" fill="${trim}"/>
  <path d="M156 92 h6 M191 92 h6 M194 89 v6" stroke="${ink}" stroke-width="1.4"/>
  <circle cx="177" cy="119" r="8" fill="${trim}"/><circle cx="177" cy="119" r="3" fill="none" stroke="${ink}" stroke-width="1.2"/>
  <text x="179" y="73" text-anchor="middle" font-family="sans-serif" font-size="8" font-weight="700" letter-spacing="1.1" fill="${ink}">${modern ? "ULTIMATE 2" : white ? "ULTIMATE" : "ULTIMATE 2.4G"}</text>
  <rect x="171" y="141" width="13" height="3" rx="1.5" fill="#b3efd5"/>
  ${modern ? '<path d="M67 52 Q90 43 120 47 M240 47 Q270 43 293 52" stroke="#9ca4b9" stroke-width="2" fill="none"/>' : ""}`);
}

function receiver(modern: boolean, light: boolean): string {
  return image(`<defs><linearGradient id="case" x2="1" y2="1"><stop stop-color="${light ? "#f4f5f5" : "#666c7c"}"/><stop offset="1" stop-color="${light ? "#bbc1cb" : "#2b3040"}"/></linearGradient><linearGradient id="metal" x2="1"><stop stop-color="#a1adbf"/><stop offset=".5" stop-color="#edf1f6"/><stop offset="1" stop-color="#8793a6"/></linearGradient></defs>
  <ellipse cx="180" cy="222" rx="52" ry="10" fill="#080e19" opacity=".19"/>
  <g transform="rotate(12 180 130)"><path d="M151 37 h58 v57 h-58Z" fill="url(#metal)" stroke="#7e8c9f" stroke-width="2"/>
  <rect x="158" y="45" width="44" height="18" rx="2" fill="#4b5667"/><path d="M166 49 v10 M194 49 v10" stroke="#c9d2de" stroke-width="4"/>
  <rect x="144" y="86" width="72" height="126" rx="${modern ? 22 : 14}" fill="url(#case)" stroke="${light ? "#a8afbc" : "#252b39"}" stroke-width="2"/>
  <path d="M152 116 v65" stroke="#fff" opacity=".18" stroke-width="2" stroke-linecap="round"/>
  <circle cx="180" cy="106" r="3" fill="${modern ? "#8ce6cb" : "#dfe8fb"}"/>
  <text x="180" y="150" font-family="sans-serif" font-size="10" font-weight="700" text-anchor="middle" fill="${light ? "#586274" : "#dfe4ee"}">8BitDo</text>
  <text x="180" y="171" font-family="sans-serif" font-size="7" letter-spacing="1" text-anchor="middle" fill="${light ? "#6f7889" : "#aeb8cb"}">${modern ? "ULTIMATE 2" : "RECEIVER"}</text></g>`);
}

const artworks: Record<string, DeviceArtwork> = {
  "ultimate2-controller": {src: controller("ultimate2"), alt: "Illustration of an Ultimate 2 Wireless controller", kind: "controller"},
  "ultimate2-receiver": {src: receiver(true, false), alt: "Illustration of an Ultimate 2 Wireless USB receiver", kind: "receiver"},
  "ultimate-bluetooth-controller": {src: controller("bluetooth"), alt: "Illustration of an Ultimate Bluetooth controller with Nintendo button labels", kind: "controller"},
  "ultimate-bluetooth-receiver": {src: receiver(false, true), alt: "Illustration of an Ultimate Bluetooth USB receiver", kind: "receiver"},
  "ultimate-24g-controller": {src: controller("24g"), alt: "Illustration of an Ultimate 2.4G controller with Xbox button labels", kind: "controller"},
  "ultimate-24g-receiver": {src: receiver(false, false), alt: "Illustration of an Ultimate 2.4G USB receiver", kind: "receiver"},
};

export function deviceArtwork(modelId: string): DeviceArtwork | undefined {
  return artworks[modelId];
}
