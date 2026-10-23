# Biko3 资源分卷

新版 IPA 内置了资源清单。用户只需复制 **6 个 `.b3data` 大文件**，不必逐个传输
169 个原始文件，也不必复制 JSON 清单。分卷不改变 `.pp`、`.tbl` 等文件的原始内容，
采用原样拼接，不重复压缩已经压缩或加密的资源。

## 在 iPhone 上导入

已打包的六个 `.b3data` 文件可从 [dev.3 Release](https://github.com/hyc1743/biko3-ios/releases/tag/v0.1.0-dev.3) 下载，无需自行打包；六卷需全部下载，保持原文件名。

分卷导入需要 dev.2 或更新版 IPA；dev.1 不支持此功能。

1. 安装包含资源导入功能的新版 IPA。
2. 将 `out/resource-packs/` 中 `Biko3Data.part001.b3data` 至
   `Biko3Data.part006.b3data` 复制到“文件 → 我的 iPhone → Biko3”的根目录，
   或该目录下的 `ResourcePacks` 文件夹。保持原文件名；不需要复制清单。
3. 打开 Biko3，保持应用在前台。应用按内置清单校验分卷与文件，再展开到
   `Documents/game/Data`。复制不完整、缺卷或校验失败时，按界面提示补齐后重试。

分卷合计 **2,982,621,688 字节**，解包另需约同样大小的可用空间。分卷和已展开的
资源同时保留时合计约 5.97 GB。首次导入完成并确认资源就绪后，可以自行删除手机中
的 6 个分卷以回收空间；电脑上的原始资源和分卷不受影响。旧方式直接复制完整
`Data` 文件夹到 `Documents/game/Data` 也保留。

界面显示当前校验或解包进度；返回前台时会重新检查，也可点“重新检查 / 继续解包”。
中断后保留已完成且校验正确的文件，当前未完成文件重新展开；不改动存档目录。
完成后写入校验记录，下次启动核对文件身份、大小和时间信息，避免重复读取全部资源。

这不是通用 ZIP 格式。不要改扩展名后用系统压缩工具展开；分卷边界可能落在一个
原始文件中间，新版应用使用内置清单恢复边界。仅有分卷而没有匹配的新版应用或清单，
无法确定原始文件的边界。

## 在电脑上生成和验证

在项目根目录执行：

```powershell
python tools/pack_resources.py --data switch/biko3/game/Data --out out/resource-packs --manifest-copy ios/resources/Biko3Data.manifest.json
python tools/pack_resources.py verify --parts out/resource-packs --data switch/biko3/game/Data
```

也可以显式使用 `pack` 子命令。输出目录必须尚不存在，避免覆盖已生成的包或验证记录。
打包器按路径排序，以最大 1 MiB 的块读取，更新原始文件和分卷的 SHA-256；每卷最多
512 MiB。原始文件保持不变。只有完整生成并验证布局后才发布清单，失败时可能留有
部分卷或 `.partial` 文件，这些不能当作完整资源包使用。

`verify` 单遍顺序读取分卷，同时检查每个分卷和每个原始文件的 SHA-256、大小及连续
覆盖关系。加上 `--data` 后，还读取原始目录逐文件核对大小和 SHA-256，确认源树身份。
验证不创建第二份约 3 GB 的解包目录。没有原始目录时可仅验证分卷：

```powershell
python tools/pack_resources.py verify --parts out/resource-packs
```

如果只保留了六卷，可以使用随项目或 IPA 对应版本提供的内置清单：

```powershell
python tools/pack_resources.py verify --parts out/resource-packs --manifest ios/resources/Biko3Data.manifest.json
```

电脑恢复功能用于独立校验或重建原始文件；它会占用额外磁盘空间，目标目录必须是新目录：

```powershell
python tools/pack_resources.py restore --parts out/resource-packs --out out/restored-resources
```

恢复结果位于 `out/restored-resources/Data`，文件通过自身 SHA-256 后才从临时文件改名。
验证或恢复失败时命令返回非零状态，不会报告资源已经就绪。

## 已生成的这一套分卷

2026-10-23 已按 [Switch 上游核对记录](upstream-status.md) 复核：本地程序与原补丁均匹配上游公布的版本；六个分卷和 169 个源文件全部通过大小、SHA-256 与布局校验。未发现可下载的新游戏资源，本次保留原分卷与内置清单，已有用户无需重新下载。

本地目录：`out/resource-packs/`。共 169 个文件，原始数据总长 2,982,621,688 字节。
前五卷每卷 536,870,912 字节，最后一卷 298,267,128 字节。

| 分卷 | 字节数 | SHA-256 |
| --- | ---: | --- |
| `Biko3Data.part001.b3data` | 536870912 | `ee23451e9784146425595356523f1cbe7d364fd32cdeeadae57c26f7fc4ec5ee` |
| `Biko3Data.part002.b3data` | 536870912 | `88ef26f7835027c649fe6012d36cb995fa9b43742ec72404718fa9f262000162` |
| `Biko3Data.part003.b3data` | 536870912 | `29dad10dc2c5066e369c4986f196734350cc076efee00f7b56e2ebd3b2a6d133` |
| `Biko3Data.part004.b3data` | 536870912 | `1597caec39d67d52cc6499b74d51d3638cb2b4fb71e852d4a83958bb3531e036` |
| `Biko3Data.part005.b3data` | 536870912 | `93c04d2771c9b67364babbcc5e67f23440b00c5f1639386278df9e0bc18127e6` |
| `Biko3Data.part006.b3data` | 298267128 | `1f1e00b3a76e8380cd1bc7271cbc775f01f2dc2eabbbff6ff2b9b3df1f0472b3` |

源文件清单身份 SHA-256：
`8076d499c762b0c33c2495b49c5a5c66ffd485c289247ca7c3b69a9e8a9203b6`。
这不包含密钥、NSP 或任何额外资源；工具只读取指定的 `Data` 目录。

## 清单格式和约束

`ios/resources/Biko3Data.manifest.json` 随 IPA 打包。六个分卷按用户要求单独发布为 dev.3
Release 附件，不放入 Git 源码历史或 IPA。`out/`、`switch/` 均已被 Git 忽略。

顶层字段为 `schema: 1`、`format: "biko3-resource-parts-v1"`、`game: "biko3"`、
`total_bytes`、`file_count`、`part_bytes`、`parts`、`files`。本打包器还写入可选字段
`source_identity_sha256`，它是按文件路径排序的 `{path,size,sha256}` 数组的规范 JSON
SHA-256：键排序、UTF-8、无多余空白、不转义非 ASCII 字符。它补充清单身份，不能替代
对实际分卷或文件字节的校验。

每卷包含 `name`、`size` 和 `sha256`。每个原始文件包含 `path`、`size`、`sha256` 及
有序 `segments`；每个段为 `{part, offset, length}`，`part` 从 **0** 开始。文件跨卷时
有多个段。空文件的大小为 0、SHA-256 为标准空输入摘要，`segments` 为空数组。

所有文件的段按清单顺序连续覆盖所有分卷，既无间隙，也无重叠或尾随数据；每个非最终
分卷恰好填满 `part_bytes`。路径必须是规范的 `Data/...` 相对路径，禁止目录逃逸、
绝对路径、反斜杠、控制字符、符号链接、Windows reparse point、大小写碰撞及文件与
目录路径冲突。打包时还检查源文件在读前、读后及最终目录扫描中的身份、大小和时间戳，
遇到源树变化则失败。验证源目录时另有真实逐字节 SHA-256 对照。

单元测试覆盖跨卷、跨文件边界、空文件、完整恢复、损坏、截断、额外尾字节、缺卷、
伪造文件摘要、源文件变化、重复 JSON 键、路径逃逸、碰撞和 reparse 属性。真实
符号链接和源目录大小写碰撞测试依赖宿主系统支持；Windows 无创建符号链接权限或使用
大小写不敏感文件系统时，这两项会明确跳过，清单层面的碰撞拒绝仍会执行。
