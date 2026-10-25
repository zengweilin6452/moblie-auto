"""BYD Academy 公共导航流与锚点(全部锚点经 2026-10-25 真机验证)。

锚点选择原则(见 conftest 模块docstring):Compose 无 resource-id,
优先 accessibility desc(语言无关);TEXT 锚点受服务端语言漂移影响,
使用处均已标注,漂移失败时的替换路径写在注释。
"""

from __future__ import annotations

from mobile_auto import Locator, Target
from mobile_auto.errors import MobileAutoError

# ── 锚点 ─────────────────────────────────────────────────

HOME_SEARCH = Target(Locator.ACCESSIBILITY_ID, "Search")  # 首页独有(语言无关)
HOME_LANGUAGE = Target(Locator.ACCESSIBILITY_ID, "language")  # 启动就绪锚点
TAB_HOME = Target(Locator.TEXT, "首页")  # 语言漂移风险:漂移时改 IMAGE/desc
TAB_COURSE = Target(Locator.TEXT, "课程")
TAB_LIVE = Target(Locator.TEXT, "直播中心")
TAB_MINE = Target(Locator.TEXT, "我的")
MINE_STUDY = Target(Locator.TEXT, "学习时长")  # 「我的」页锚点
MINE_SETTINGS = Target(Locator.ACCESSIBILITY_ID, "settings")  # 齿轮(语言无关)
KB_ENTRY = Target(Locator.TEXT, "知识库")  # 首页模块入口
KB_COUNT_ITEM = Target(
    Locator.XPATH, '//android.widget.TextView[contains(@text,"数量:")]'
)  # 知识库条目标记(「数量:N」)
SETTINGS_FEEDBACK = Target(Locator.TEXT, "问题反馈")
WEBVIEW_NODE = Target(Locator.XPATH, "//android.webkit.WebView")
LOGIN_EDITTEXT = Target(Locator.XPATH, "//android.widget.EditText")

# MIUI 系统权限对话框(com.lbe.security.miui)随机弹出挡住锚点等待;
# 统一点「拒绝」保持用户既有授权状态(语言漂移双语都试)
_DISMISS_LABELS = ("拒绝", "Reject", "取消", "Cancel")


async def dismiss_system_dialogs(app) -> None:
    """尽力清理系统对话框;不是业务步骤,失败静默。"""
    for label in _DISMISS_LABELS:
        try:
            if await app.is_visible(Target(Locator.TEXT, label)):
                await app.click(Target(Locator.TEXT, label))
        except MobileAutoError:
            pass  # 对话框消失竞态:不作为用例失败依据


async def goto_home(app) -> str:
    """冷启动并归位;返回落点分类 "home" / "login"。

    冷启动落点由服务端会话状态决定(非随机):会话有效直达首页,
    过期落登录页须人工登录(图形验证码不可自动化),用例据此分流。
    """
    await app.goto("")  # 先停止再启动,起点确定
    try:
        await app.wait_for_load()  # 启动就绪锚点:desc=language(首页独有)
    except MobileAutoError:
        pass  # 落在登录页时锚点不可见:由下方分类判据分流,不当错误
    await dismiss_system_dialogs(app)
    if await app.is_visible(HOME_SEARCH):
        return "home"
    return "login"


async def open_tab(app, tab: Target, anchor: Target | None = None, timeout: float = 6):
    """点底部导航并等页面锚点;返回锚点是否出现。"""
    await app.click(tab)
    await dismiss_system_dialogs(app)
    if anchor is None:
        return True
    return await app.wait_for(anchor, timeout=timeout)
