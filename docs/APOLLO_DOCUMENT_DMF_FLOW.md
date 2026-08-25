# Apollo Studio 文档 DMF Flow 配置

文档查询链路要求 Workflow 的输入变量名为 `markdown`，输出变量名为
`structured_output`。

## 结构化输出

`structured_output` 必须是对象，并包含 `queries` 数组：

```json
{
  "queries": [
    {
      "dmf_no": "234",
      "applicant_name": "",
      "ingredients": ["Ibuprofen"]
    },
    {
      "dmf_no": "211",
      "applicant_name": "",
      "ingredients": ["NOT"]
    }
  ]
}
```

每个数组元素表示一组独立的查询条件。不要把不同数据行的 DMF 编号、申请商
和成分合并，否则会改变原始行之间的对应关系。

推荐在 LLM 节点使用以下提取要求：

```text
逐行读取输入 Markdown 中的所有表格和文本，提取所有 DMF 查询条件。
每一条包含 DMF 编号、申请商名称或成分的非空数据行，都生成一个 queries 元素。
不得只返回第一行，不得合并不同数据行，保持原始顺序。
缺失字段使用空字符串或空数组，不得猜测或补全原文不存在的值。
去除完全重复的查询项；如果没有任何条件，返回 {"queries": []}。
```

结构化输出节点的 Schema 可配置为：

```json
{
  "type": "object",
  "properties": {
    "queries": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "dmf_no": {"type": "string"},
          "applicant_name": {"type": "string"},
          "ingredients": {
            "type": "array",
            "items": {"type": "string"}
          }
        },
        "required": ["dmf_no", "applicant_name", "ingredients"]
      }
    }
  },
  "required": ["queries"]
}
```

Python 服务仍兼容旧的单对象输出，但旧格式只能表示一组条件，不能解决多行
Excel 的批量查询问题。修改 Schema 后需要重新发布 Workflow。