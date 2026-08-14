# aipipe · AI 代码级工作流引擎

**自然语言驱动 → opencode 编写 Python 分步脚本 → Docker 沙箱确定性执行/复跑** 的代码级 AI 自动化引擎（渐进路线版）。

## 文档

- [项目功能文档（PRD v2）](docs/PRD.md)
- [原始需求](docs/REQUIREMENTS.md)

## 定位

- **开发期**（写代码、调通）：交给 **opencode**，不重复造轮子
- **运行期**（固化、复跑、沙箱执行）：自研薄服务（FastAPI + Docker 执行器 + 移动端优先 Web）

## 状态

文档已就绪（PRD v2），M1 执行计划已定：批次 A 执行闭环骨架 + 批次 B youtube-dub 试点流水线，待开工。
