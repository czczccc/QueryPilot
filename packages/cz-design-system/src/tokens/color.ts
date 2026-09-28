/**
 * 颜色系统
 *
 * 分两层：
 * 1. 原始色阶（primitive）：用 OKLCH 定义，明度按感知均匀分布，同一档位跨色相亮度一致。
 *    组件永远不直接引用原始色阶。
 * 2. 语义色（semantic）：按用途命名（画布、表面、正文、边框、强调、状态），
 *    明暗两套主题各自映射到原始色阶。组件只使用语义色。
 */

export type Oklch = { l: number; c: number; h: number; alpha?: number };

export const oklch = ({ l, c, h, alpha }: Oklch) =>
  alpha === undefined || alpha === 1
    ? `oklch(${round(l)} ${round(c)} ${round(h)})`
    : `oklch(${round(l)} ${round(c)} ${round(h)} / ${round(alpha)})`;

const round = (n: number) => Math.round(n * 10000) / 10000;

type Ramp = Record<string, Oklch>;

/** OKLCH 是否落在 sRGB 色域内（OKLab → 线性 sRGB，标准矩阵）。 */
function inSrgb(l: number, c: number, h: number) {
  const a = c * Math.cos((h * Math.PI) / 180);
  const b = c * Math.sin((h * Math.PI) / 180);
  const l_ = (l + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m_ = (l - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s_ = (l - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const rgb = [
    4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
    -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
    -0.0041960863 * l_ - 0.7034186147 * m_ + 1.707614701 * s_,
  ];
  return rgb.every((v) => v >= 0 && v <= 1);
}

/** 彩度超出 sRGB 时向内收，保证每一档在普通屏幕上都能准确显示。 */
function clampChroma(l: number, c: number, h: number) {
  let chroma = Math.round(c * 1000) / 1000;
  while (chroma > 0 && !inSrgb(l, chroma, h)) chroma = Math.round((chroma - 0.001) * 1000) / 1000;
  return chroma;
}

function ramp(h: number, steps: Record<string, [l: number, c: number]>): Ramp {
  return Object.fromEntries(
    Object.entries(steps).map(([step, [l, c]]) => [step, { l, c: clampChroma(l, c, h), h }]),
  );
}

/**
 * 石墨（Graphite）：略带青绿偏色的中性色，比纯灰更沉静，和松石强调色同源。
 * 档位比常见色阶更密，两端用于明暗主题的多层表面。
 */
export const graphite = ramp(170, {
  "0": [0.997, 0.001],
  "25": [0.986, 0.0025],
  "50": [0.974, 0.003],
  "100": [0.957, 0.004],
  "150": [0.937, 0.0045],
  "200": [0.906, 0.005],
  "300": [0.842, 0.006],
  "400": [0.72, 0.007],
  "500": [0.6, 0.008],
  "600": [0.5, 0.008],
  "700": [0.41, 0.008],
  "800": [0.33, 0.007],
  "850": [0.285, 0.0065],
  "900": [0.25, 0.006],
  "925": [0.226, 0.0055],
  "950": [0.205, 0.005],
  "975": [0.185, 0.0045],
  "1000": [0.165, 0.004],
});

/** 通用色阶的明度与彩度曲线：中段彩度最高，两端收敛，保证浅色底和深色字都不刺眼。 */
const chromatic = (peak: number): Record<string, [number, number]> => ({
  "50": [0.972, peak * 0.16],
  "100": [0.943, peak * 0.3],
  "200": [0.885, peak * 0.55],
  "300": [0.8, peak * 0.8],
  "400": [0.71, peak * 0.95],
  "500": [0.61, peak],
  "600": [0.525, peak * 0.95],
  "700": [0.45, peak * 0.85],
  "800": [0.38, peak * 0.72],
  "900": [0.31, peak * 0.58],
  "950": [0.24, peak * 0.44],
});

/** 松石（Pine）：品牌强调色。偏青的深绿，传达“可靠、专注”，与常见的蓝紫色 AI 品牌拉开距离。 */
export const pine = ramp(182, chromatic(0.11));
/** 苔绿：成功。色相比松石更偏黄，避免和品牌色混淆。 */
export const moss = ramp(146, chromatic(0.14));
/** 琥珀：警告。 */
export const amber = ramp(72, chromatic(0.15));
/** 赭红：危险/错误。 */
export const rust = ramp(27, chromatic(0.17));
/** 雾蓝：信息。 */
export const mist = ramp(248, chromatic(0.12));

export const primitives = { graphite, pine, moss, amber, rust, mist } as const;
export type PrimitiveName = keyof typeof primitives;

/** 引用原始色阶中的某一档，可选透明度。 */
export type Ref = readonly [PrimitiveName, string] | readonly [PrimitiveName, string, number];

export const resolve = (ref: Ref): Oklch => {
  const [name, step, alpha] = ref;
  const value = primitives[name][step];
  if (!value) throw new Error(`未知色阶：${name}.${step}`);
  return alpha === undefined ? value : { ...value, alpha };
};

/**
 * 语义色。键名就是 CSS 变量名（--cz-color-*）和 Tailwind 颜色名。
 * 例：canvas → bg-canvas，fg-muted → text-fg-muted，line → border-line。
 */
export const semantic = {
  light: {
    // 背景层级：画布 < 表面 < 浮起；凹陷用于输入框和代码块
    canvas: ["graphite", "25"],
    surface: ["graphite", "0"],
    raised: ["graphite", "0"],
    sunken: ["graphite", "50"],
    overlay: ["graphite", "1000", 0.32],
    hover: ["graphite", "900", 0.045],
    pressed: ["graphite", "900", 0.08],
    selected: ["pine", "500", 0.1],
    inverse: ["graphite", "950"],

    // 文字
    fg: ["graphite", "950"],
    "fg-muted": ["graphite", "600"],
    "fg-subtle": ["graphite", "500"],
    "fg-disabled": ["graphite", "400"],
    "fg-inverse": ["graphite", "25"],

    // 边框
    "line-subtle": ["graphite", "150"],
    line: ["graphite", "200"],
    "line-strong": ["graphite", "300"],
    focus: ["pine", "500"],

    // 强调
    accent: ["pine", "600"],
    "accent-hover": ["pine", "700"],
    "accent-fg": ["pine", "700"],
    "accent-soft": ["pine", "50"],
    "accent-soft-hover": ["pine", "100"],
    "accent-line": ["pine", "200"],
    "on-accent": ["graphite", "0"],
    // 开关滑块等“实体小部件”的表面
    knob: ["graphite", "0"],

    // 状态：solid 用于实心徽标和图标，fg 用于文字，soft/line 用于提示条底色和描边
    success: ["moss", "600"],
    "success-fg": ["moss", "700"],
    "success-soft": ["moss", "50"],
    "success-line": ["moss", "200"],
    warning: ["amber", "500"],
    "warning-fg": ["amber", "700"],
    "warning-soft": ["amber", "50"],
    "warning-line": ["amber", "200"],
    danger: ["rust", "600"],
    "danger-fg": ["rust", "600"],
    "danger-soft": ["rust", "50"],
    "danger-line": ["rust", "200"],
    info: ["mist", "600"],
    "info-fg": ["mist", "600"],
    "info-soft": ["mist", "50"],
    "info-line": ["mist", "200"],
  },
  dark: {
    canvas: ["graphite", "1000"],
    surface: ["graphite", "975"],
    raised: ["graphite", "925"],
    sunken: ["graphite", "1000"],
    overlay: ["graphite", "1000", 0.6],
    hover: ["graphite", "0", 0.05],
    pressed: ["graphite", "0", 0.085],
    selected: ["pine", "400", 0.14],
    inverse: ["graphite", "50"],

    fg: ["graphite", "50"],
    "fg-muted": ["graphite", "400"],
    "fg-subtle": ["graphite", "500"],
    "fg-disabled": ["graphite", "700"],
    "fg-inverse": ["graphite", "950"],

    "line-subtle": ["graphite", "900"],
    line: ["graphite", "850"],
    "line-strong": ["graphite", "700"],
    focus: ["pine", "400"],

    accent: ["pine", "400"],
    "accent-hover": ["pine", "300"],
    "accent-fg": ["pine", "300"],
    "accent-soft": ["pine", "500", 0.14],
    "accent-soft-hover": ["pine", "500", 0.22],
    "accent-line": ["pine", "400", 0.32],
    "on-accent": ["graphite", "1000"],
    knob: ["graphite", "150"],

    success: ["moss", "400"],
    "success-fg": ["moss", "300"],
    "success-soft": ["moss", "500", 0.14],
    "success-line": ["moss", "400", 0.32],
    warning: ["amber", "400"],
    "warning-fg": ["amber", "300"],
    "warning-soft": ["amber", "500", 0.14],
    "warning-line": ["amber", "400", 0.32],
    danger: ["rust", "400"],
    "danger-fg": ["rust", "300"],
    "danger-soft": ["rust", "500", 0.16],
    "danger-line": ["rust", "400", 0.34],
    info: ["mist", "400"],
    "info-fg": ["mist", "300"],
    "info-soft": ["mist", "500", 0.16],
    "info-line": ["mist", "400", 0.34],
  },
} as const satisfies Record<"light" | "dark", Record<string, Ref>>;

export type SemanticColor = keyof typeof semantic.light;
export type Theme = keyof typeof semantic;
