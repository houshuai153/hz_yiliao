import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => { const p = join(dir, n); return statSync(p).isDirectory() ? files(p) : [p]; });
}
const SRC = join(__dirname, "..");
const code = files(SRC).filter((p) => /\.(ts|tsx)$/.test(p) && !p.includes(`${join(SRC, "test")}`));
// 去掉注释再检查：注释里允许解释“为什么不用 localStorage”，真正的代码不允许使用
const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");

describe("前端不接触静态令牌，也不把凭据放进可读存储", () => {
  it.each(["API_TOKEN", "localStorage", "sessionStorage", "Authorization", "Bearer "])("源码中没有 %s", (needle) => {
    const hits = code.filter((p) => strip(readFileSync(p, "utf8")).includes(needle));
    expect(hits).toEqual([]);
  });
  it("没有 VITE_ 环境变量被用来注入令牌", () => {
    expect(code.filter((p) => /import\.meta\.env\.VITE_.*TOKEN/i.test(strip(readFileSync(p, "utf8"))))).toEqual([]);
  });
});
