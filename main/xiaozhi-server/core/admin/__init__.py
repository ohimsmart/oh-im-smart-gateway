"""轻量管理后台：挂在 HTTP 服务（server.http_port）的 /admin 下，读写 data/.config.yaml。"""


def register_admin_routes(app, config, ws_server=None):
    """在 aiohttp 应用上注册管理后台路由。"""
    # 用时才导入：core.agent.grokbot_client 要用 core.admin.i18n_msg，包一导入就拉 admin_handler 会循环导入
    from core.admin.admin_handler import AdminHandler
    AdminHandler(config, ws_server).register(app)
