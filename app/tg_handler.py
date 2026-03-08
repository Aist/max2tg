from telegram.ext import Application

from app.account_manager import AccountManager
from app.tg_commands import register_handlers


def build_tg_app(token: str, account_manager: AccountManager, admin_id: int) -> Application:
    app = Application.builder().token(token).build()
    app.bot_data["account_manager"] = account_manager
    app.bot_data["admin_id"] = int(admin_id)
    register_handlers(app)
    return app
