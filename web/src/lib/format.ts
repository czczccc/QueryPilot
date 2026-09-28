export const RES_RANK: Record<string, number> = { SD: 1, "720p": 2, "1080p": 3, "2160p": 4 };
export const RES_LABEL: Record<string, string> = { SD: "标清", "720p": "720p", "1080p": "1080p", "2160p": "4K" };

export function formatSize(bytes?: number | null): string {
  if (!bytes) return "";
  const gb = bytes / 1024 ** 3;
  return gb >= 1 ? gb.toFixed(1) + "GB" : Math.round(bytes / 1024 ** 2) + "MB";
}

/** 秒级时间戳 → 「9/28 15:37」 */
export function formatTime(ts: number): string {
  const d = new Date(ts * 1000);
  return d.getMonth() + 1 + "/" + d.getDate() + " " + String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
}

/** 秒级时间戳 → 「3 分钟前」 */
export function relTime(ts: number): string {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "刚刚";
  if (s < 3600) return Math.floor(s / 60) + " 分钟前";
  if (s < 86400) return Math.floor(s / 3600) + " 小时前";
  if (s < 86400 * 7) return Math.floor(s / 86400) + " 天前";
  return formatTime(ts);
}

/** 把集号压缩成「3、5–7、10」 */
export function episodeRanges(list: number[]): string {
  const out: [number, number][] = [];
  list
    .slice()
    .sort((a, b) => a - b)
    .forEach((n) => {
      const last = out[out.length - 1];
      if (last && n === last[1] + 1) last[1] = n;
      else out.push([n, n]);
    });
  return out.map(([a, b]) => (a === b ? String(a) : a + "–" + b)).join("、");
}

export function shareUrl(share: string, pwd?: string | null): string {
  return "https://pan.quark.cn/s/" + share + (pwd ? "?pwd=" + pwd : "");
}

export function isMobileDevice(): boolean {
  const ua = navigator.userAgent || "";
  return /Android|iPhone|iPad|iPod|HarmonyOS|Mobile/i.test(ua) || (navigator.maxTouchPoints > 1 && /Macintosh/.test(ua));
}
