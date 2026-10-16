# 11 中文学术资源与校园访问

## 本轮决策

按用户最新要求，暂不接入浙大统一身份认证。不开发 CAS 登录，不显示可用校园账号登录按钮，不以认证作为文献发现前置条件。未来说明集中在 `docs/20_ZJU_CAS_DEFERRED.md`，不属于当前验收范围。

保留“校园资源访问”以及 CNKI、万方、维普的官方检索/文献落地页入口。它们是用户主动打开的资源链接，不代表 LEExtractor 已取得数据库程序化检索授权。

## 三类能力

metadata_api 表示有正式可用且允许本产品使用的元数据接口；institution_link 表示学校/数据库的机构访问入口；fulltext_link 或 resolver 表示出版社/开放获取/馆藏解析入口。返回 AccessOption 必须包括 url、provider、kind、auth_required、institution、availability、verified、checked_at。

verified 仅说明链接或元数据来源已核验；availability 可取 unknown、public、requires_institution、entry_only、unavailable。不能将用户选择“浙江大学”当作授权证明，不能根据应用登录状态断定数据库全文可访问。

## 接入与降级

首期 institution adapter 为可配置浙江大学资源目录，官方入口在实现时从图书馆页面核对并记录检查日期；无确定的单篇匹配则提供数据库检索入口并标 `entry_only`。不要凭猜测拼接数据库内部 ID 或未知登录回调。

正式中文检索 adapter 在获得接口文档和许可后启用。未提供接口时配置 `metadata_api=external_blocked`，不影响开放来源检索；代码和 UI 不可将普通网页跳转统计为召回成功。

## 中文归一

保留原始中文与英文题名，Unicode NFKC、空白和标点规范化用于匹配；同一强标识符可合并。仅翻译题名相似不足以自动合并，应结合作者、年份和版本关系。作者不能因拼音同名直接合并。中文词法使用确定的分词或字符 n-gram adapter；跨语种向量必须确实具备模型能力。

## 状态文案

“开放获取”“需要机构权限”“仅提供检索入口”“尚未匹配到全文”。访问失败与文献不存在分开。无需显示 CAS、CARSI 等协议名；具体连接与错误进入诊断。未来认证接入也不得突破数据库版权或许可边界。
