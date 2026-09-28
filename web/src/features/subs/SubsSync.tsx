import { useEffect } from "react";
import { useAppState } from "../../lib/app-state";
import { useMe } from "../../lib/me";
import { loadSubs, useSubsData } from "./store";

/** 常驻：每 5 分钟拉一次订阅和提醒，同步导航上的订阅入口和未读数 */
export function SubsSync() {
  const { setSubsEnabled, setUnread, subsVersion } = useAppState();
  const { me } = useMe();
  const data = useSubsData();

  useEffect(() => {
    loadSubs();
  }, [subsVersion, me.logged_in]);

  useEffect(() => {
    const t = setInterval(loadSubs, 5 * 60 * 1000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    setSubsEnabled(data.enabled);
    setUnread(data.notes.filter((n) => !n.read).length);
  }, [data.enabled, data.notes, setSubsEnabled, setUnread]);

  return null;
}
