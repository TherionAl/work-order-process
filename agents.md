# agents.md

更新日期：2026-07-02

本文件是codex、Claude和其它agent的新会话入口，关键信息和参数会在此文档中进行补充

## 开发环境相关

1.github账户：15606949636@163.com

2.github用户名：TherionAl

3.项目环境：Windows10

4.项目上层地址：D:\Users\python_project

### 项目名称：工单数据获取及解析处理

1. 开发需求

   1.1 开发语言：python

   1.2 项目创建地址：D:\Users\python_project\work_order_process

   1.3 开发环境及依赖包管理工具：uv

   1.4 接口参数相关信息通用文档地址：https://doc.bangwo8.com/

   1.5  项目所需参数：

      USERNAME = "bosssoft2021"

   ​    PASSWORD = "Bosi_soft2024"

   ​    实际项目地址前缀 = "https://workorder.bosssoft.com.cn/api/v1"

   1.6 获取数据后的定义文件：数据字典-帮我吧.pdf

2. 实现目标

   2.1 获取所有的客户和联系人，并根据可获取的信息补全这2个实体的字段 

   2.2 获取2025年后的工单，先随机取10条，根据接口返回的信息，尽量补全工单相关的字段，返回的字段换成字典里的相关信息





