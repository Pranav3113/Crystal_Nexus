from flask import Flask, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager, current_user

from config import Config

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    migrate.init_app(app, db)

    login_manager.init_app(app)
    login_manager.login_view = "auth.login"

    # Blueprints
    from .auth.routes import auth_bp
    from .admin.routes import admin_bp
    from .leads.routes import leads_bp
    from .pipeline.routes import pipeline_bp
    from .quotes.routes import quotes_bp
    from .admin.user_master import user_master_bp
    from .admin.designations import designations_bp
    from .admin.rbac_master import rbac_bp
    from .clients.routes import clients_bp
    from .cli import register_cli
    from app.payments.routes import payments_bp
    from .admin.industries import industries_bp
    from app.company_master.routes import company_bp
    from .admin.services import admin_services_bp
    from app.proforma.routes import proforma_bp
    from app.invoices.routes import invoices_bp
    from .admin.menu_master import menu_bp
    from .currencies.routes import currencies_bp
    from .admin.reports import reports_bp
    from app.projects.routes import projects_bp
    from app.admin.margin_settings import margin_settings_bp
    app.register_blueprint(margin_settings_bp)
    app.register_blueprint(projects_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(currencies_bp)
    app.register_blueprint(menu_bp, url_prefix="/admin")
    app.register_blueprint(proforma_bp)
    app.register_blueprint(invoices_bp)
    app.register_blueprint(admin_services_bp, url_prefix="/admin")
    app.register_blueprint(company_bp)
    app.register_blueprint(industries_bp, url_prefix="/admin")
    app.register_blueprint(payments_bp)

    register_cli(app)

    app.register_blueprint(clients_bp, url_prefix="/clients")
    app.register_blueprint(rbac_bp, url_prefix="/admin")
    app.register_blueprint(designations_bp, url_prefix="/admin")
    app.register_blueprint(user_master_bp, url_prefix="/admin")
    app.register_blueprint(quotes_bp, url_prefix="/quotes")
    app.register_blueprint(pipeline_bp, url_prefix="/pipeline")
    app.register_blueprint(leads_bp, url_prefix="/leads")
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")

    # -------------------------
    # Sidebar Menus (DB-driven)
    # -------------------------
    def _user_perm_codes(user):
        if not user or not getattr(user, "role", None):
            return set()
        return set([p.code for p in (user.role.permissions or [])])

    @app.context_processor
    def inject_sidebar_menus():
        if not current_user.is_authenticated:
            return {"sidebar_menus": []}

        # ✅ import models INSIDE function to avoid circular import
        from .models import Menu, SubMenu

        perm_codes = _user_perm_codes(current_user)
        menus = Menu.query.filter_by(is_active=True).order_by(Menu.sort_order.asc()).all()

        sidebar = []
        for m in menus:
            subs = m.submenus.filter_by(is_active=True).order_by(SubMenu.sort_order.asc()).all()

            visible_subs = []
            for s in subs:
                if s.permission_code and s.permission_code not in perm_codes:
                    continue

                href = "#"
                try:
                    if s.endpoint:
                        href = url_for(s.endpoint)
                    elif s.url:
                        href = s.url
                except Exception:
                    href = s.url or "#"

                visible_subs.append({
                    "title": s.title,
                    "icon": s.icon,
                    "href": href,
                    "endpoint": s.endpoint or ""
                })

            if visible_subs:
                sidebar.append({
                    "title": m.title,
                    "icon": m.icon,
                    "submenus": visible_subs
                })

        return {"sidebar_menus": sidebar}

    @app.route("/")
    def home():
        if current_user.is_authenticated:
            return redirect(url_for("admin.dashboard"))
        return redirect(url_for("auth.login"))

    return app