import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

/**
 * 前端开发服务器。所有 /api 请求统一代理到唯一 FastAPI 后端 :3000。
 */
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    // ↓ 新增：所有 /api 请求转发到后端，前端不用写死地址，也没有跨域问题
    //   改完必须重启 dev server 才生效
    proxy: {
      '/api': {
        target: 'http://localhost:3000',
        changeOrigin: true,
      },
    },
  },
})
