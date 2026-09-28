import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AgentSteps, Badge, Button, Card, CardTitle, Composer, Input, ToolCall } from "../src";

describe("组件渲染", () => {
  it("Button 使用 token 类名并在加载时禁用", () => {
    render(<Button loading>保存</Button>);
    const btn = screen.getByRole("button", { name: "保存" });
    expect(btn).toHaveProperty("disabled", true);
    expect(btn.className).toContain("bg-accent");
    expect(btn.className).toContain("rounded-control");
  });

  it("Badge、Card、Input 正常渲染", () => {
    render(
      <Card>
        <CardTitle>订阅</CardTitle>
        <Badge tone="success">已更新</Badge>
        <Input aria-label="名称" defaultValue="繁花" />
      </Card>,
    );
    expect(screen.getByText("已更新").className).toContain("text-success-fg");
    expect(screen.getByLabelText("名称")).toHaveProperty("value", "繁花");
  });

  it("AgentSteps 渲染每一步", () => {
    render(
      <AgentSteps
        steps={[
          { id: "a", title: "搜索资源", status: "done", meta: "1.2s" },
          { id: "b", title: "校验链接", status: "running", detail: "正在检查 3 个分享链接" },
          { id: "c", title: "转存到网盘", status: "pending" },
        ]}
      />,
    );
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
    expect(screen.getByText("正在检查 3 个分享链接")).toBeTruthy();
  });

  it("ToolCall 与 Composer 的无障碍属性", () => {
    render(
      <>
        <ToolCall name="search" status="done" input={{ q: "繁花" }} />
        <Composer value="" onChange={() => {}} onSubmit={() => {}} />
      </>,
    );
    expect(screen.getByRole("button", { expanded: false })).toBeTruthy();
    expect(screen.getByRole("button", { name: "发送" })).toHaveProperty("disabled", true);
  });
});
