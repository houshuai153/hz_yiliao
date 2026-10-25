import { FileCheck2, FlaskConical, Languages, LayoutDashboard, LogOut, Menu, X, BookOpenCheck } from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useDrafts, useHealth } from "../api/hooks";
import { useAuth, useMe } from "../auth/AuthContext";
import { Button } from "./ui";

export function BrandMark() {
  // 色谱峰形的字标：产品自己的标志（不是“黑色方块加字母”）
  return (
    <svg className="brand__mark" viewBox="0 0 32 32" role="img" aria-label="生物分析工作台标志">
      <rect width="32" height="32" rx="7" fill="#0d1117" />
      <path d="M5 22h5l3-11 4 15 3-9 2 5h5" fill="none" stroke="#fff" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

const TITLES: [string, string][] = [["/reports", "报告审核"], ["/qa", "法规与 SOP 问答"], ["/translate", "翻译与术语核对"], ["/", "项目看板"]];

export function Shell() {
  const me = useMe();
  const { logout } = useAuth();
  const [open, setOpen] = useState(false);
  const loc = useLocation();
  const drafts = useDrafts();
  const health = useHealth();
  const waiting = drafts.data?.filter((d) => !d.legacy && d.review_status !== "reviewed").length ?? 0;
  const title = TITLES.find(([p]) => (p === "/" ? loc.pathname === "/" : loc.pathname.startsWith(p)))?.[1] ?? "";
  const llm = health.data?.llm_enabled ? `已启用大模型（${health.data.llm_model ?? health.data.llm_provider}）：仅用于翻译兜底、问答措辞、报告修订建议，发送前脱敏，输出须人工复核；判定与指标仍由规则计算。` : "规则与模板实现，非 LLM。";
  const close = () => setOpen(false);

  return (
    <div className="app">
      <div className="topbar">
        <BrandMark />
        <span className="topbar__title">{title}</span>
        <Button size="sm" aria-expanded={open} aria-controls="sidebar" aria-label={open ? "关闭导航" : "打开导航"} icon={open ? <X /> : <Menu />} onClick={() => setOpen(!open)} />
      </div>
      <aside id="sidebar" className="sidebar" data-open={open}>
        <div className="brand">
          <BrandMark />
          <div>
            <div className="brand__name">生物分析工作台</div>
            <div className="brand__scope">演示环境 · 模拟数据</div>
          </div>
        </div>
        <nav className="nav" aria-label="主导航">
          <NavLink to="/" end className="nav__item" onClick={close}><LayoutDashboard aria-hidden />项目看板</NavLink>
          <NavLink to="/reports" className="nav__item" onClick={close}>
            <FileCheck2 aria-hidden />报告审核
            {waiting > 0 && <span className="nav__count" aria-label={`${waiting} 份草稿待处理`}>{waiting}</span>}
          </NavLink>
          <div className="nav__group">知识与语言</div>
          <NavLink to="/qa" className="nav__item" onClick={close}><BookOpenCheck aria-hidden />法规与 SOP 问答</NavLink>
          <NavLink to="/translate" className="nav__item" onClick={close}><Languages aria-hidden />翻译与术语核对</NavLink>
        </nav>
        <div className="sidebar__foot">
          <p className="sidebar__notice">
            <FlaskConical size={12} aria-hidden style={{ verticalAlign: -1, marginRight: 4 }} />
            全部为模拟数据；规则阈值为演示配置（demo_unconfirmed）；{llm}输出需人工复核。
          </p>
          <div className="userrow">
            <span className="userrow__avatar" aria-hidden>{me.username.slice(0, 1).toUpperCase()}</span>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div className="userrow__name truncate">{me.username}</div>
              <div className="userrow__role">操作人记录</div>
            </div>
            <Button size="sm" variant="text" aria-label="退出登录" title="退出登录" icon={<LogOut />} onClick={() => void logout()} />
          </div>
        </div>
      </aside>
      <main className="canvas" onClick={close}><Outlet /></main>
    </div>
  );
}
