# strings-merge

iOS `.strings` 资源的流式无损合并库：把确认后的翻译写回人工维护的语言包，
除实际更新的值字面量和末尾新增内容外，所有原始字节（注释、排版、转义写法、
编码与 BOM）逐字节保留。读取分块方式不影响结果。

## 接口

```python
from strings_merge import iter_entries, merge, StringsParseError

# 依次产生解码后的 (key, value)，保留原顺序与重复键，惰性生成
for key, value in iter_entries(reader):
    ...

# 将合并结果写入 writer；updates 为键互异的有序映射
merge(reader, writer, {"hello": "你好", "new.key": "新值"})
```

- `reader` / `writer`：二进制流，无需支持 seek；`read(n)` 允许只返回少量
  字节，空字节串表示结束。
- 编码：无 BOM 按 UTF-8；支持带 BOM 的 UTF-8 / UTF-16LE / UTF-16BE；
  输出沿用原编码与 BOM。
- 合并语义：按解码后的键比较（不做 Unicode 规范化）；同一键的每次出现
  都会更新；某次出现的值与新值语义相同则保留原有字节；不存在的键按
  `updates` 顺序在文件末尾追加完整声明（末尾未换行的行注释不会吞掉
  追加内容）。
- 非法编码、非法转义、未闭合的字符串或块注释、不完整声明均抛出
  `StringsParseError`，即使 `updates` 为空也会完整校验输入。

## 格式支持

- 声明：`"key" = "value" ;`，标记之间允许空白与注释（`//` 行注释、
  `/* ... */` 非嵌套块注释）。
- 字符串转义：`\\` `\"` `\n` `\r` `\t`，1–3 位八进制（≤255），
  `\Uhhhh`（恰好 4 位十六进制；相邻高低代理项合成为一个字符，孤立代理项
  非法），反斜杠加换行表示续行。其余转义非法；字符串内禁止未转义的
  CR/LF。

## 资源约束

流式处理：额外峰值内存不超过 8 MiB（不计 `updates` 与当前单个词条），
任意长的注释或词条数都不会突破该限制。

## 运行测试

```sh
python3 -m unittest discover -s tests -v
```
