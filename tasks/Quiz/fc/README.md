# 题库共享 API（阿里云函数计算）

`app.py` + `merge.py` 可以直接部署到阿里云函数计算 **Web 函数**（Python 3.10 / 自定义运行时）。

## 环境变量

| 变量 | 必填 | 说明 |
|---|---|---|
| `GITHUB_TOKEN` | 是 | Fine-grained PAT，只需题库仓库的 `Contents: Read and write` |
| `GITHUB_REPO` | 是 | `owner/repo`，例如 `jiaysr/mzxy-quiz-db` |
| `GITHUB_BRANCH` | 否 | 默认 `main` |
| `BANK_PATH` | 否 | 默认 `quiz_zh.json` |
| `BANK_API_KEY` | 是 | 客户端提交时放在 `X-Api-Key` 头里 |

## 接口

### `GET /health`

返回仓库、分支、文件名，不返回 token。

### `POST /quiz/submit`

请求头：

```
Content-Type: application/json
X-Api-Key: <BANK_API_KEY>
```

请求体：

```json
{
  "questions": [
    {
      "id": "qabcdef123",
      "q": "题干",
      "options": ["选项A", "选项B", "选项C"],
      "answer": "选项A",
      "verified": true,
      "wrong": ["选项B"],
      "hits": 3,
      "correct": 2,
      "source": "llm",
      "updated": "2026-11-23"
    }
  ]
}
```

服务端流程：

1. 校验 API Key；
2. 校验并规范化每条题目（题干/选项/答案/排除表/计数/来源）；
3. 读取 GitHub 仓库里的当前题库；
4. 用与 `tasks/Quiz/bank_sync.py` 相同的规则按 id 合并；
5. 通过 GitHub Contents API 提交更新；
6. 遇到 409/422 冲突时重新拉取、合并、重试（最多 3 次）。

返回：

```json
{
  "ok": true,
  "changed": true,
  "total": 1234,
  "submitted": 100,
  "skipped": 0,
  "added": 5,
  "updated": 20,
  "conflict": 0,
  "sanitized": 1,
  "attempt": 1
}
```

`changed=false` 表示提交内容与仓库现有题库一致，服务端不会创建空提交。

## 部署

1. 函数计算控制台 → 云函数 → 创建 Web 函数；
2. 运行环境：Python 3.10 / 自定义运行时 / Debian 11；
3. 规格：0.10 vCPU + 256 MB 即可；最小实例数保持 **0**；
4. 启动命令 `python3 app.py`，监听端口 `9000`；
5. 环境变量填上表里的 5 项；
6. 把 `app.py` 和 `merge.py` 放在代码根目录，点「部署代码」；
7. HTTP 触发器认证方式改成「无需认证」，由代码里的 `X-Api-Key` 鉴权；
8. 访问 `https://<触发器域名>/health` 验证。

## 本地测试合并逻辑

`merge.py` 只依赖标准库，可以脱离 Flask 测试：

```bash
python -c "from merge import merge_questions; print(merge_questions([], []))"
```

项目里已有对照测试脚本，可自行用 `tasks/Quiz/bank_sync.py` 的 `merge_questions`
对同一份数据比较结果。
