// 订阅弹窗（由订阅模块实现）。约定的接口，搜索页的「订阅」按钮会用它：
//   <SubscribeDialog open target={...} autoSave onDone={(sub) => ...} />
// onDone(sub)：订阅成功传回新订阅对象；取消或失败传 null。
export type SubscribeTarget = { query: string; resource: string; empty: boolean };

export function SubscribeDialog(_props: { open: boolean; target: SubscribeTarget; autoSave: boolean; onDone: (sub: any | null) => void }) {
  return null;
}
