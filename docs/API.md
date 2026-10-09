# 接口说明

所有接口位于 `/api/`，使用 JSON。登录成功返回 CSRF token 并设置会话 Cookie；其他写接口要求 `X-CSRF-Token`。未登录返回401，无权限返回403，员工访问他人详情返回404。错误结构为 `{"error":"提示信息"}`。

| 方法 | 路径 | 权限 | 用途 |
| --- | --- | --- | --- |
| POST | `/login` | 未登录 | username、password；返回 user 与 csrf |
| GET | `/me` | 已登录 | user、csrf、query_allowed |
| POST | `/logout` | 已登录 | 注销会话 |
| GET | `/policies` | 已登录 | 员工/财务仅有效制度；行政全部版本 |
| POST | `/search` | 已登录 | question；返回匹配制度条款和出处 |
| POST | `/policies` | 行政 | 创建草稿 |
| POST | `/policies/{id}` | 行政 | 编辑草稿；已发布/撤回版本不可编辑 |
| POST | `/policies/{id}/publish` | 行政 | 发布草稿，校验同名版本有效期重叠 |
| POST | `/policies/{id}/withdraw` | 行政 | 撤回版本，保留原文 |
| GET | `/claims` | 财务或获授权员工 | 财务全量；员工仅本人 |
| GET | `/claims/{id}` | 财务或获授权员工 | 最新记录及 history；验证单据归属 |
| POST | `/claims/{id}` | 财务 | status、note、revision；更新并追加历史 |
| GET | `/access` | 财务 | 员工本人查询授权列表 |
| POST | `/access` | 财务 | employee、allowed（布尔值） |
| GET | `/audit` | 行政/财务 | 当前职责范围内最近100条记录 |

## 制度字段

`title`（200字以内）、`version`（40字以内）、`start`（YYYY-MM-DD）、`end`（可空）、`scope`（300字以内）、`content`（30000字以内）。除 end 外全部必填。版本修订创建新草稿，不覆盖旧版本。有效期首尾日期均包含。

## 核对并发控制

从详情读取 `revision`，保存时原样传入。成功保存后版本加1；版本不匹配返回409，不写入备注或历史。新核对记录包括核对人、时间、状态与备注。

状态值：材料审核中、待补充材料、待审批、已完成、已退回。这些是演示业务记录；更新接口不调用审批或支付服务。

## 检索输出

`mode: retrieval`、`message`、`as_of`、`sources`。每条来源包含 `policy_id`、`title`、`version`、`start`、`scope`、`excerpt`、`score`。score 是关键词排序分数，不是答案置信度或制度权威等级。

## 数据表

users（身份与职责）、policies（制度版本）、claims（最新单据事实）、claim_history（历次核对事实）、query_access（本人查询授权）、audit（职责操作记录）。
