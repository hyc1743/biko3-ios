# 现有中文补丁

本项目只套用游戏资源中已有的汉化补丁，不另行翻译剧本或修改贴图。`Data/patch.pp` 已包含 27 个 GBK 编码的剧本补丁、字体和图片补丁；基础剧本则为 CP932 日文。保留原补丁内容及汉化组署名。

`tools/biko3_assets.py` 提供了只读解析工具：

```powershell
python tools/biko3_assets.py <Data> out/localization-inventory.json --inventory
python tools/biko3_assets.py <Data> out/scenarios --export-scenarios
```

这些命令只读检查索引和补丁后的剧本，不修改原始资源。原程序查找 `game/patch.pp`，而分卷展开到 `game/Data/patch.pp`。文件适配层仅在根目录补丁不存在时，将只读访问回退到 `Data/patch.pp`；根目录补丁优先，写入不重定向。原有资源清单及补丁字节均不改变。

静态检查证明补丁含有简体中文，实际加载、字体和画面仍须通过新构建运行验证。现有补丁未覆盖的日文保持原样。
