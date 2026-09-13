# 页面模块化与登录架构说明

本次重构的目标是：把原来 `RareSpermAnnotation.vue` 中混在一起的三个业务页面拆开，并把认证边界放到业务页面之外。

## 页面结构

- `src/pages/LoginPage.vue`：登录页
- `src/pages/AnnotatePage.vue`：人工标注页
- `src/pages/ResultsPage.vue`：标注结果页
- `src/pages/EffectsPage.vue`：效果查看页
- `src/layouts/AppLayout.vue`：登录后的统一壳层、顶部导航、退出登录
- `src/router/index.ts`：轻量路由与登录保护
- `src/stores/auth.ts`：认证状态；当前是前端 Mock，后续替换这里即可接真实 API
- `src/stores/workspace.ts`：原标注/结果/效果业务状态与方法，页面只负责展示和事件绑定

## 为什么这样拆

以后接入真实登录系统时，主要改动集中在：

1. `src/stores/auth.ts`：登录接口、token、刷新 token、用户信息。
2. `src/router/index.ts`：如果后续需要更复杂的权限控制，可直接替换成 Vue Router。
3. `App.vue`：只负责认证后的页面入口和路由映射。

人工标注、结果、效果三个业务页面不需要因为“是否登录”而增加登录判断，也不需要把登录逻辑塞进原来的业务代码。

## 当前登录行为

这是为了方便前端联调的 Mock 登录：账号和密码均非空即可登录，并写入 `localStorage`。后端接口确定后，只需修改 `auth.ts`。

## 验证

已通过：

```bash
vue-tsc --noEmit
```

如果本机首次安装依赖，请执行：

```bash
npm install
npm run dev
```
