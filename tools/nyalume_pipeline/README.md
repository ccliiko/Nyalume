# Nyalume 桌宠表情帧流水线

把“同一个人设生成多个表情帧”做成可复现的本地 ComfyUI 管线。
生成的工作流与提示词都在本目录（tracked）；生成的图片素材只写进
gitignore 的 `user_pets/`，不进仓库。

## 文件

- `prompts/q_BA_prompt.txt`：v5-1 定稿提示词（站姿立绘、银白侧马尾、
  橘子发饰、粉白哥特裙、纯白丝袜、左腿腿环、深蓝平涂底，所有权重 ≤1.5）
- `workflows/q_BA_workflow_api.json`：double_lora_BA API 工作流
  （StarSea checkpoint → z3画风 0.4 → 鬼针草-Illustrious NoobAI 0.8 →
  cfg8/dpmpp_2m/karras → 脸/手 FaceDetailer → SaveImage）
- `make_moods.py`：按表情词生成 6 份表情工作流
  （distant/grumpy/neutral/happy/love/working）
- `comfy_submit.py`：把 API 工作流 POST 到 127.0.0.1:8188 队列尾
- `comfy_wait.py`：轮询 /history 直到出图，打印输出文件名
- `pet_skin_cut.py`：把纯色底 PNG 抠成透明皮肤帧（边框连通抠除，
  支持 --adaptive 处理渐变背景，--out 指定输出目录）

## 前置条件

1. 本机 ComfyUI（aki 整合包）正在运行，API 端口 8188；
2. 模型就位：
   - checkpoint：`绘星海_清新风格 StarSea_pastel _v5.safetensors`
   - LoRA：`z3画风_illv0.1.safetensors`、`鬼针草-Illustriou_NoobAI_1.0.safetensors`
   - ultralytics：`bbox/face_yolov8m.pt`、`bbox/hand_yolov8s.pt`
3. Python 依赖（项目 .venv）：
   `pip install pillow numpy scipy`

## 用法

```bash
# 1) 生成 6 个表情工作流（可加 happy love 只生成指定表情）
python tools/nyalume_pipeline/make_moods.py

# 2) 逐个排队出图
python tools/nyalume_pipeline/comfy_submit.py user_pets/nyalume/art/moods/happy_workflow_api.json
python tools/nyalume_pipeline/comfy_submit.py user_pets/nyalume/art/moods/love_workflow_api.json

# 3) 等出图
python tools/nyalume_pipeline/comfy_wait.py <prompt_id...>

# 4) 把 ComfyUI output 里的 nyalume_mood_<key>_*.png 拷到
#    user_pets/nyalume/moods_raw/<key>.png，然后抠成 300x300 透明帧：
python tools/nyalume_pipeline/pet_skin_cut.py \
  user_pets/nyalume/moods_raw/happy.png --prefix mood_happy_ --out user_pets/nyalume

# 5) 把 mood_<key>_1.png 改名成 user_pets/nyalume/mood_<key>.png，
#    并在 user_pets/nyalume/manifest.json 的 frames 里填上对应文件
```

## 提示词维护约定

- 所有 `(tag:weight)` 权重不得超过 1.5；
- 背景必须是和“银白头发+粉白衣物”色差大的深蓝平涂，否则抠图会吃发丝；
- 表情词只在 `looking at viewer, ` 之后插入，姿势/服装/头发标签不动，
  保证 6 帧共用同一构图种子（默认 2026097002）。

## 版权说明

角色与提示词均为本项目的原创产出；生成的 PNG 属于本地演示素材，
放在 `user_pets/`（gitignore），随仓库分发的只有文字/代码管线。
