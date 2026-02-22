from flask import Flask, current_app, redirect, url_for, g, request
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager, current_user

from config import Config

from sqlalchemy import create_engine
from flask_sqlalchemy import SQLAlchemy

try:
    # Flask-SQLAlchemy 3.x
    from flask_sqlalchemy.session import Session as BaseFSASession
except ImportError:
    # Flask-SQLAlchemy 2.5.x
    from flask_sqlalchemy import SignallingSession as BaseFSASession
_TENANT_ENGINES = {}

class TenantRoutingSession(BaseFSASession):
    def get_bind(self, mapper=None, clause=None, **kw):
        # 1) Respect bind_key models (platform)
        if mapper is not None:
            table = getattr(mapper, "local_table", None)
            if table is not None and table.info.get("bind_key"):
                return super().get_bind(mapper=mapper, clause=clause, **kw)

        # 2) Route to tenant engine if set
        engine = getattr(g, "tenant_engine", None)
        if engine is not None:
            return engine

        # 3) Default bind
        return super().get_bind(mapper=mapper, clause=clause, **kw)


class TenantSQLAlchemy(SQLAlchemy):
    def create_session(self, options):
        # options can be None; also remove any class_ to avoid duplicates
        options = dict(options or {})
        options.pop("class_", None)

        # Flask-SQLAlchemy 2.5.x expects db=self for SignallingSession
        from sqlalchemy.orm import sessionmaker
        return sessionmaker(class_=TenantRoutingSession, db=self, **options)

db = TenantSQLAlchemy()

migrate = Migrate()
login_manager = LoginManager()


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    migrate.init_app(app, db)

    from .platform_models import Tenant  # bind_key="platform"

    def _extract_subdomain(host: str, base_domain: str):
        host = (host or "").split(":")[0].lower()
        base_domain = (base_domain or "").lower()

        # localhost: no subdomain routing
        if host == "localhost" or host.endswith(".localhost") or host.startswith("127."):
            return None

        if base_domain and host.endswith("." + base_domain):
            prefix = host[: -(len(base_domain) + 1)]
            if prefix and "." not in prefix:
                return prefix

        parts = host.split(".")
        if len(parts) >= 3:
            return parts[0]
        return None

    from flask import request, g
    from .platform_models import Tenant

    def _get_subdomain_slug(app):
        host = request.host.split(":")[0]  # remove port
        base = app.config.get("BASE_DOMAIN")

        # If BASE_DOMAIN is set and host endswith it, try extract subdomain
        if base and host.endswith(base) and host != base:
            return host[: -(len(base) + 1)]  # remove ".base"
        return None

    @app.before_request
    def bind_tenant_database():
        # ✅ Do NOT force tenant binding for platform routes
        if request.path.startswith("/platform") or request.path.startswith("/static"):
            return

        slug = _get_subdomain_slug(current_app)

        # ✅ if no subdomain, use DEFAULT_TENANT_SLUG (optional)
        if not slug:
            slug = current_app.config.get("DEFAULT_TENANT_SLUG")

        # ✅ If still no slug => platform-only mode, skip tenant binding
        if not slug:
            g.tenant_engine = None
            return

        # ✅ Lookup tenant ONLY from platform bind
        tenant = Tenant.query.filter_by(slug=slug, is_active=True).first()
        if not tenant:
            # In platform-only mode, you may want to redirect to platform tenants page instead
            g.tenant_engine = None
            return

        g.tenant_engine = create_engine(tenant.db_uri, pool_pre_ping=True, pool_recycle=1800)

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
    from .platform.routes import platform_bp
    from .commands.tenant_seed import register_tenant_seed
    from .commands.reset_db import register_reset_db

    
    app.register_blueprint(platform_bp)
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
    register_tenant_seed(app)
    register_reset_db(app)

    app.register_blueprint(clients_bp, url_prefix="/clients")
    app.register_blueprint(rbac_bp, url_prefix="/admin")
    app.register_blueprint(designations_bp, url_prefix="/admin")
    app.register_blueprint(user_master_bp, url_prefix="/admin")
    app.register_blueprint(quotes_bp, url_prefix="/quotes")
    app.register_blueprint(pipeline_bp, url_prefix="/pipeline")
    app.register_blueprint(leads_bp, url_prefix="/leads")
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")

    # Sidebar Menus
    def _user_perm_codes(user):
        if not user or not getattr(user, "role", None):
            return set()
        return set([p.code for p in (user.role.permissions or [])])

    @app.context_processor
    def inject_tenant_branding():
        logo_url = None

        if getattr(g, "tenant", None) and getattr(g.tenant, "logo", None):
            logo_url = url_for("static", filename=f"tenant_logos/{g.tenant.logo}")

        # ✅ root / fallback logo
        if not logo_url:
            logo_url = url_for("static", filename="tenant_logos/company_logo.png")

        return dict(tenant_logo=logo_url)

    
    @app.context_processor
    def inject_sidebar_menus():
        if not current_user.is_authenticated:
            return {"sidebar_menus": []}

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