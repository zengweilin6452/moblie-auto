"""mobile_auto.viewer:本机网页调试服务(plan 8)。

FastAPI lifespan 管理会话;仅监听回环地址;前端为随包分发的
原生 HTML/CSS/JS。核心不依赖本包,依赖单向(网页 → 核心)。
"""
