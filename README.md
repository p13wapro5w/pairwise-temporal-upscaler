# pairwise-temporal-upscaler

## 领域

游戏、图形与多媒体

## 主题

时域超分

## 说明

历史重投影、遮挡掩码与 UI 分离。

初始状态下 `render/temporal_upscaler.py` 中所有函数抛出 `NotImplementedError`，测试全部失败。
修复实现后运行：

```bash
python -m pytest -q
```

期望全部通过。
