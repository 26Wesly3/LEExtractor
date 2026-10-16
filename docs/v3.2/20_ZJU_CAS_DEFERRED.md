# 20 CARSI 与出版社资源跳转

## 当前实施决定

本版本实施外部访问入口，暂不让 LEExtractor 成为浙大 CAS 客户端或 CARSI 服务提供商。application_sso_enabled=false、federation_sp_enabled=false、external_resource_links_enabled=true。直接 SSO 不纳入 M0至M5，不添加学校密码表单、票据验证接口或仿制登录页。

## 用户路径

论文详情展示“出版社原文”“通过学校访问 CARSI”“图书馆数据库导航”。打开新标签页前保留当前题名和 DOI，并提供复制动作。用户在 CARSI 选择浙江大学并在官方页面登录，再选择已授权资源；Elsevier 论文可打开已有 ScienceDirect 落地页，再使用该平台的机构登录。

若没有可核验单篇落地页，优先使用 DOI 官方解析地址；无法确定出版商时，不将所有论文都送到 Elsevier。CARSI 门户是通用入口，不能保证自动带回特定文章；未取得官方深链规范时不自行拼接身份参数。必要时用户复制题名/DOI继续检索，文案为“进入后可使用题名或 DOI 查找”。

登录后会话属于学校、CARSI 或出版商。LEExtractor 无权读取跨站 cookie，也不从用户切回页面推断“认证成功”或“全文已授权”。本应用保持原有匿名/账户会话。链接打开成功只算访问入口事件，不算全文下载成功。

## 固定注册表

`contracts/access_links.json` 保存本次核对的官方入口、标签、来源、checked_at、类型和粒度。界面只使用注册表及有来源的 paper landing_url。外链允许 HTTPS；不生成含 Key、校园身份或私人研究主题的通用登录链接。外链使用 noopener/noreferrer；不自动跟随执行登录。

默认入口：CARSI 用户门户、浙江大学数据库导航、浙江大学 ScienceDirect 说明页。出版社原文依据具体论文记录。数据库未订购或文章不在授权范围时，由目标平台给出状态；本产品不承诺登录后全部全文可得。

## 与检索 API 的区别

资源跳转不增加服务器召回能力，不把 CARSI 当作统一论文搜索 API，也不将 Elsevier 个人账号当作任意第三方登录 provider。若未来要使用 Elsevier/Scopus/ScienceDirect 的程序化 API，需另行核对官方接口、Key、订阅范围与部署限制，再通过 provider adapter 实现；本轮不以浏览器 cookie 代替 API 授权。

## 后续直接 SSO 的记录

用户给出的 zjuam.zju.edu.cn/cas/login 链接的 service 指向 eta.zju.edu.cn 已有系统。此地址不属于 LEExtractor，不能复制其回调注册。将来如恢复需求，先走学校应用接入申请，确认自己的 HTTPS service 回调、允许域名/部署环境、协议及版本、验证端点、最少身份属性、会话和退出策略。

正式开发时才评估成熟 CAS 客户端并新增独立 adapter。后端必须向已确认的学校端点验证一次性票据与精确 service；未验证不能建会话。防重放、过期票据、错误 service、开放重定向和登录请求绑定均需独立验收。通用 CAS 协议不等于学校当前实际配置。

## 当前验收

点击 CARSI 打开官方门户，保留当前文献；无外链不伪造地址；用户回来后不改变应用登录状态；服务未配置不显示已认证；不产生 CAS 校验请求。直接 SSO 测试 AC-29 标记 deferred。
