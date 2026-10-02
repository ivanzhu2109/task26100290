# strings_merge

流式、无损合并 iOS `.strings` 资源文件的 Python 库，用于把确认后的翻译写回
人工维护的语言包。除实际更新的值字面量和末尾追加的声明外，输出与输入
逐字节一致；读取分块方式不影响结果。

## 特性

- 编码：无 BOM 按 UTF-8；支持带 BOM 的 UTF-8 / UTF-16LE / UTF-16BE，
  输出沿用原编码与 BOM 状态，不猜测其他编码。
- 完整语法：键、值、等号、分号之间允许空白与注释；支持 `//` 行注释与
  非嵌套 `/* */` 块注释；字符串内的注释标记、`=`、`;` 均为普通内容。
- 转义：`\\`、`\"`、`\n`、`\r`、`\t`、1–3 位八进制（≤255）、`\Uhhhh`
  （恰好 4 位十六进制，相邻高低代理项合成为一个字符）、反斜杠续行。
- 流式：不整读文件、不缓存全部键；额外峰值内存 < 8 MiB（不计 updates
  与当前词条），任意长的注释也不会突破。
- 合并语义：按解码后的键比较（不做 Unicode 规范化）；重复键全部更新；
  语义未变的值保留原有字节；缺失键按 updates 顺序追加完整声明，且不会
  被文件末尾未换行的行注释吞掉。
- 非法编码、非法转义、未闭合字符串/注释、不完整声明均抛出
  `StringsParseError`，即使 updates 为空也会完整校验。

## 接口

```python
from strings_merge import iter_entries, merge, StringsParseError

# 依次产生解码后的 (key, value)，保留原顺序与重复键（生成器，不返回列表）
for key, value in iter_entries(reader):
    ...

# 将合并后的字节写入 writer；updates 为键互异的有序映射
merge(reader, writer, {"greeting": "你好", "new_key": "新值"})
```

`reader` / `writer` 为二进制流，不要求支持 seek；`read(n)` 可以每次只
返回 1 字节，只有空字节串表示结束。

## 运行测试

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖：三种编码 × 每次 1 字节及多组不规则分块（BOM、UTF-16 代理对、
转义与注释边界被截断）、混合转义等价的重复键、语义未变但写法不同的值、
含伪声明的注释、末尾行注释、空 updates 仍拒绝畸形输入，以及生成式
32 MiB 块注释 + 10 万词条下仅改一键的内容与内存上限验证。

## 文件结构

- `strings_merge/decoder.py` — BOM 探测与 UTF-8/UTF-16 增量解码、字节偏移映射
- `strings_merge/parser.py` — 流式语法状态机（注释/字符串/转义）
- `strings_merge/api.py` — `iter_entries` 与 `merge` 的合并/写出逻辑
- `strings_merge/errors.py` — `StringsParseError`
- `tests/test_strings_merge.py` — 测试套件
