\# Case 004: 客户查询只检查列表第一个元素



\## 元信息

\- 仓库: ramgarapati/SpringBoot\_TCS（跨项目 case）

\- 组织: ramgarapati（个人，非 spring-projects）

\- 业务领域: 客户管理 (CRM)

\- 测试命令: mvn test -Dtest=CustomerServiceImplTest

\- Bug 注入点: CustomerServiceImpl.findByName / findByEmail



\## Bug 描述

原代码：

&#x20;   while (customerList.iterator().hasNext()) {

&#x20;       Customer customer = customerList.iterator().next();

&#x20;       ...

&#x20;   }

customerList.iterator() 每次调用返回新迭代器，若第一个元素不匹配则死循环。



Case 中改写为"只取第一个元素"（等价语义，不卡死）：

&#x20;   Customer first = customerList.iterator().next();

&#x20;   if (first.getName().equals(name)) return first;

&#x20;   return null;



\## 新增测试

\- testFindByName\_NonMatchingFirst

\- testFindByEmail\_NonMatchingFirst



\## 验证结果



| 阶段 | tests | failures | errors |

|---|---|---|---|

| before | 12 | 0 | 2 |

| after | 12 | 0 | 0 |



失败点：返回 null 导致断言处 NPE。



\## 检索结果



| 指标 | 结果 |

|---|---|

| 文件级 Top-1 | X (Controller.java，正确：ServiceImpl.java) |

| 文件级排名 | 正确文件在 rank 2/3 |

| 方法级 Top-1 | X (Controller.findByName) |

| 方法级 Top-5 | OK (ServiceImpl.findByName + findByEmail 都在) |



\## 检索失败根因分析



两个结构性缺陷：



1\. 无法区分接口与实现

&#x20;  - CustomerService.findByName（接口）得分 32

&#x20;  - CustomerServiceImpl.findByName（实现）得分 38

&#x20;  - 系统不知道接口无方法体，bug 只在实现类



2\. 无法区分 Controller 与 Service

&#x20;  - CustomerManagementController.findByName 得分 41

&#x20;  - CustomerServiceImpl.findByName 得分 38

&#x20;  - 系统不知道业务 bug 在 Service 层



这两个缺陷与以下现象同类：

\- Case 001：findOwner / showOwner 方法级同分

\- Case 003：processCreationForm / processUpdateForm 方法级同分



共同根因：检索器缺乏代码分层语义。



\## 为什么不修



修复需要引入：

\- Spring 注解 @Controller / @Service / @Repository 的分层感知

\- 接口与实现的绑定（implements 关系）

\- 调用链距离度量



列入未来工作。
