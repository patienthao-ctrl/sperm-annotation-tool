# 微流控稀有精子识别与提取：前端接口契约

> **重要说明**：当前项目是“纯前端独立运行版”。前端不会启动、依赖或调用任何 Python/FastAPI/SAM3.1 服务。
>
> 本文档的作用只是提前把**未来前端 ↔ 后端的数据格式和接口边界**定义清楚。当前页面通过 `src/api/annotationApi.ts` 的 TypeScript 接口 + Mock 实现运行。

---

## 1. 前端职责边界

```text
Vue3 页面
  ↓
src/api/annotationApi.ts
  ↓
当前：Mock API（本地，不联网）

未来：Http API Adapter
  ↓
FastAPI
  ↓
数据库 / 对象存储 / AI Worker / SAM3.1
```

前端只负责：

- 图片/视频选择与预览
- 视频第一帧提取
- 第一帧人工点标注/框标注
- 对象命名、选择、删除、重命名
- 标注结果展示
- 处理效果视频展示
- 调用接口、展示任务状态

后端负责：

- 文件持久化
- 标注 JSON 持久化
- AI 推理
- Tracking
- 任务调度
- 结果视频生成
- 数据库和对象存储

---

# 2. 数据基础类型

## 2.1 MediaType

```text
image | video
```

## 2.2 AnnotationObject

当前前端坐标统一采用 **0~100 的归一化百分比坐标**，避免前端依赖具体图片分辨率。

```json
{
  "id": "manual-001",
  "name": "rare sperm A",
  "source": "manual",
  "confidence": 0.98,
  "point": {
    "x": 48.2,
    "y": 52.1
  },
  "bbox": {
    "x": 43.0,
    "y": 48.0,
    "width": 10.5,
    "height": 8.2
  },
  "frameIndex": 0,
  "timestampMs": 0
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| id | string | 是 | 前端生成的对象 ID；后端可映射自己的 ID |
| name | string | 是 | 对象名称，来自页面对象名称输入框 |
| source | string | 是 | `manual` 或 `ai` |
| confidence | number | 否 | AI 置信度 |
| point | object | 否 | 点标注，x/y 为 0~100 |
| bbox | object | 否 | 框标注，x/y/width/height 为 0~100 |
| frameIndex | number | 视频时必填 | 当前规则固定为 `0` |
| timestampMs | number | 视频时必填 | 当前规则固定为 `0` |

---

# 3. 上传媒体

## `POST /api/annotation/media`

当前前端不调用该 HTTP 地址，仅预留契约。

### Request

`multipart/form-data`

```text
file=<图片或视频文件>
```

### Response

```json
{
  "mediaId": "media_20260905_001",
  "objectKey": "projects/demo/media/media_20260905_001.mp4"
}
```

### 建议后端职责

- 校验 MIME 类型
- 生成唯一 `mediaId`
- 保存原始文件
- 返回对象存储 `objectKey`
- 返回媒体宽高、时长、FPS 等元数据

---

# 4. 保存人工标注

## `POST /api/annotation/annotations/manual`

这是当前项目最重要的接口之一。

### 图片标注示例

```json
{
  "mediaId": "media_image_001",
  "mediaType": "image",
  "annotationVersion": "frontend-v4",
  "objects": [
    {
      "id": "manual-001",
      "name": "rare sperm A",
      "source": "manual",
      "point": {
        "x": 48.2,
        "y": 52.1
      },
      "bbox": {
        "x": 43.0,
        "y": 48.0,
        "width": 10.5,
        "height": 8.2
      }
    },
    {
      "id": "manual-002",
      "name": "rare sperm B",
      "source": "manual",
      "bbox": {
        "x": 65.0,
        "y": 31.0,
        "width": 7.0,
        "height": 6.0
      }
    }
  ]
}
```

### 视频第一帧标注示例

> **产品规则：视频只标注第一帧。**
>
> 因此任何视频人工标注 JSON 都必须使用 `frameIndex = 0`、`timestampMs = 0`。

```json
{
  "mediaId": "media_video_001",
  "mediaType": "video",
  "frameIndex": 0,
  "timestampMs": 0,
  "annotationVersion": "frontend-v4-first-frame-video",
  "objects": [
    {
      "id": "manual-video-001",
      "name": "rare sperm A",
      "source": "manual",
      "point": {
        "x": 48.2,
        "y": 52.1
      },
      "bbox": {
        "x": 43.0,
        "y": 48.0,
        "width": 10.5,
        "height": 8.2
      },
      "frameIndex": 0,
      "timestampMs": 0
    }
  ]
}
```

### Response

```json
{
  "id": "annotation_001"
}
```

---

# 5. AI 检测 / 分割

## `POST /api/annotation/ai/segment`

### 图片

```json
{
  "mediaId": "media_image_001",
  "mediaType": "image",
  "prompt": "rare sperm"
}
```

### 视频

```json
{
  "mediaId": "media_video_001",
  "mediaType": "video",
  "prompt": "rare sperm",
  "frameIndex": 0,
  "timestampMs": 0,
  "seedObjects": [
    {
      "id": "manual-video-001",
      "name": "rare sperm A",
      "source": "manual",
      "bbox": {
        "x": 43,
        "y": 48,
        "width": 10.5,
        "height": 8.2
      },
      "frameIndex": 0,
      "timestampMs": 0
    }
  ]
}
```

### Response 示例

```json
{
  "objects": [
    {
      "id": "ai-001",
      "name": "rare sperm",
      "source": "ai",
      "confidence": 0.96,
      "point": {
        "x": 51,
        "y": 46
      },
      "bbox": {
        "x": 43,
        "y": 39,
        "width": 15,
        "height": 14
      },
      "frameIndex": 0,
      "timestampMs": 0
    }
  ]
}
```

如果后端未来返回真正的 SAM3.1 mask，建议扩展：

```json
{
  "mask": {
    "format": "rle",
    "value": "..."
  }
}
```

前端之后可以在同一个 `AnnotationObject` 上增加 `mask` 字段，不改变现有 bbox/point 契约。

---

# 6. 视频 Tracking

## `POST /api/annotation/ai/track`

视频 Tracking 的**种子对象来自第一帧人工标注**。

### Request

```json
{
  "mediaId": "media_video_001",
  "prompt": "rare sperm A",
  "startFrame": 0,
  "endFrame": 180,
  "seedObject": {
    "id": "manual-video-001",
    "name": "rare sperm A",
    "source": "manual",
    "bbox": {
      "x": 43,
      "y": 48,
      "width": 10.5,
      "height": 8.2
    },
    "frameIndex": 0,
    "timestampMs": 0
  }
}
```

### Response

建议立即返回任务 ID，而不是让 HTTP 请求长时间等待：

```json
{
  "taskId": "task_track_001"
}
```

---

# 7. 查询 AI 任务状态

## `GET /api/annotation/tasks/{taskId}`

### Response

```json
{
  "taskId": "task_track_001",
  "status": "running",
  "progress": 65,
  "message": "Tracking processing frame 117 / 180"
}
```

状态：

```text
queued
running
success
failed
```

后续如果需要实时进度，可以增加：

```text
WS /api/annotation/tasks/{taskId}/events
```

但当前前端不要求 WebSocket 才能运行。

---

# 8. 标注结果列表

## `GET /api/annotation/projects/{projectId}/results`

建议后端返回：

```json
{
  "items": [
    {
      "mediaId": "media_video_001",
      "mediaName": "sample-001.mp4",
      "mediaType": "video",
      "frameIndex": 0,
      "timestampMs": 0,
      "annotationCount": 3,
      "updatedAt": "2026-09-05T09:20:00Z"
    }
  ],
  "total": 1
}
```

---

# 9. 效果列表

## `GET /api/annotation/effects`

用于“效果查看”页面左侧列表。

### Response

```json
{
  "items": [
    {
      "id": "effect_001",
      "sourceMediaId": "media_video_001",
      "sourceMediaName": "sample-001.mp4",
      "resultName": "sample-001 · 稀有精子识别效果",
      "resultVideoUrl": "/objects/results/effect_001.mp4",
      "createdAt": "2026-09-05T10:20:00Z",
      "status": "completed",
      "summary": {
        "detectedObjects": 18,
        "extractedCandidates": 4,
        "durationSeconds": 12.5
      }
    }
  ]
}
```

---

# 10. 获取单个效果

## `GET /api/annotation/effects/{effectId}`

用于点击效果列表后的详细查看。

### Response

可以返回完整效果对象，并可继续扩展：

```json
{
  "id": "effect_001",
  "sourceMediaId": "media_video_001",
  "sourceMediaName": "sample-001.mp4",
  "resultName": "sample-001 · 稀有精子识别效果",
  "resultVideoUrl": "/objects/results/effect_001.mp4",
  "createdAt": "2026-09-05T10:20:00Z",
  "status": "completed",
  "summary": {
    "detectedObjects": 18,
    "extractedCandidates": 4,
    "durationSeconds": 12.5
  }
}
```

---

# 11. 推荐的完整数据链路

```text
[前端]
上传图片/视频
      ↓
选择视频第一帧
      ↓
人工点/框标注
      ↓
POST /annotations/manual
      ↓
保存第一帧 JSON
      ↓
POST /ai/segment（可选）
      ↓
POST /ai/track（视频可选）
      ↓
得到 taskId
      ↓
GET /tasks/{taskId}
      ↓
处理完成
      ↓
GET /effects
      ↓
效果查看
```

---

# 12. 视频业务规则（必须统一）

当前前端已经固定以下规则：

1. 视频人工标注只针对第一帧。
2. 第一帧为 `frameIndex = 0`。
3. 第一帧时间为 `timestampMs = 0`。
4. 视频可以播放和预览其他帧，但不会产生逐帧人工标注数据。
5. 视频 Tracking 的 seed object 必须来自第一帧人工标注。
6. 后端可以从第一帧 seed 开始对整个视频做 Tracking。

因此后端不要把前端传来的视频人工标注理解成“逐帧标注数据”。

---

# 13. 坐标约定

当前前端传：

```text
x / y / width / height ∈ [0, 100]
```

例如：

```json
{
  "x": 25,
  "y": 40,
  "width": 10,
  "height": 8
}
```

代表：

```text
左上角：25% × 40%
宽：10%
高：8%
```

后端如果需要原始像素坐标，可根据媒体元数据转换：

```text
pixelX = x / 100 × imageWidth
pixelY = y / 100 × imageHeight
pixelW = width / 100 × imageWidth
pixelH = height / 100 × imageHeight
```

---

# 14. 前端如何接入真实后端

当前：

```ts
import { mockAnnotationApi } from './api/annotationApi'
const api = mockAnnotationApi
```

未来建议新增：

```text
src/api/httpAnnotationApi.ts
```

让它实现同一个：

```ts
AnnotationApi
```

然后页面只需要把：

```ts
const api = mockAnnotationApi
```

替换成：

```ts
const api = httpAnnotationApi
```

这样可以保证：

- UI 不关心 FastAPI
- UI 不关心 Python
- UI 不关心 SAM3.1
- UI 不关心数据库
- UI 不关心 GPU
- 未来后端接口变化时只改 API Adapter / 类型定义

---

# 15. 当前版本如何保证独立运行

项目没有：

- Python 依赖
- FastAPI 依赖
- PyTorch 依赖
- CUDA 依赖
- SAM3.1 依赖
- 后端 proxy
- 必须配置的 API URL
- 必须运行的 WebSocket

只需要：

```bash
npm install
npm run dev
```

即可启动前端。


## 前端独立运行与效果文件夹

“效果查看”的文件夹选择是纯前端能力：浏览器通过 `webkitdirectory` 选择目录，前端过滤其中的 `video/*` 文件并使用 `URL.createObjectURL` 本地播放。该操作不会请求后端。

如果未来需要后端提供效果结果，继续使用 `listEffects()` / `getEffect()` 的接口契约即可；前端不需要知道后端实现。

## 视频第一帧与帧查看规则

- 视频人工标注提交时：`frameIndex` 固定为 `0`，`timestampMs` 固定为 `0`。
- 用户输入帧号只是查看视频画面，不改变人工标注数据。
- 点击“点标注/框标注”或 AI 检测时，前端会把视频定位回第 0 帧，再产生标注。


### v7 保存规则
- 图片：导出带标注 PNG。
- 视频：前端独立模式下使用 MediaRecorder 导出 WebM；只把第一帧人工标注写入导出视频，后续帧保持原视频。
- 后端接口仍仅为契约定义，当前不会发起 HTTP 请求。

## 前端 `annotations.json` 导出格式

人工标注页面提供“生成 JSON”功能。该文件用于后续交给 SAM3 后端，不保存前端百分比坐标作为最终 bbox。

前端内部坐标为百分比：

```text
bbox = { x, y, width, height } // 0~100
```

导出时根据素材实际像素尺寸转换为：

```text
[x1, y1, x2, y2]
```

示例：

```json
{
  "version": "1.0",
  "format": "sam3-annotations",
  "media": {
    "id": "video-demo-001",
    "name": "microfluidic-demo.mp4",
    "type": "video",
    "width": 1920,
    "height": 1080
  },
  "frame": {
    "frameIndex": 0,
    "timestampMs": 0
  },
  "coordinateSystem": {
    "source": "frontend-percent",
    "target": "pixel",
    "bbox": "[x1, y1, x2, y2]"
  },
  "annotations": [
    {
      "id": "manual-001",
      "name": "rare sperm",
      "source": "manual",
      "bbox": [768, 324, 864, 410],
      "frameIndex": 0,
      "timestampMs": 0
    }
  ]
}
```

对于图片，使用图片的 `naturalWidth / naturalHeight`；对于视频，使用视频的 `videoWidth / videoHeight`。视频始终导出第一帧标注。

